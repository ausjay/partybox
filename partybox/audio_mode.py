from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from . import db as DB

VALID_MEDIA_MODES = ("partybox", "spotify", "airplay", "bluetooth", "tv", "mute")
VALID_MEDIA_MODE_SET = set(VALID_MEDIA_MODES)

SETTING_MEDIA_MODE = "media_mode"
SETTING_LAST_SWITCH_TS = "media_mode_last_switch_ts"
SETTING_LAST_ERROR = "media_mode_last_error"
SETTING_LAST_ACTIONS = "media_mode_last_actions_json"

MANAGED_MODE_SERVICES = (
    "partybox-player.service",
    "librespot.service",
    "partybox-airplay.service",
    "partybox-bluetooth.service",
)

MODE_START_SERVICES = {
    "partybox": ("partybox-player.service",),
    "spotify": ("librespot.service",),
    "airplay": ("partybox-airplay.service",),
    "bluetooth": ("bluetooth.service", "partybox-bluetooth.service"),
    "tv": (),
    "mute": (),
}


def _log(msg: str) -> None:
    print(f"[audio_mode] {msg}", flush=True)


class AudioModeManager:
    def __init__(self) -> None:
        self._sudo_bin = os.getenv("PARTYBOX_SUDO_BIN", "sudo").strip() or "sudo"
        self._systemctl_bin = os.getenv("PARTYBOX_SYSTEMCTL_BIN", "systemctl").strip() or "systemctl"
        self._bt_alias = os.getenv("PARTYBOX_BLUETOOTH_ALIAS", "PartyBox BT").strip() or "PartyBox BT"
        self._status_cache_ttl = max(1.0, float(os.getenv("PARTYBOX_MEDIA_MODE_STATUS_CACHE_TTL_SECONDS", "5") or "5"))
        self._status_cache_ts = 0.0
        self._status_cache: Optional[Dict[str, Any]] = None

    def _run(self, cmd: List[str], timeout: float = 10.0) -> Dict[str, Any]:
        started = time.time()
        quoted = " ".join(shlex.quote(c) for c in cmd)
        try:
            proc = subprocess.Popen(
                cmd,
                capture_output=True,
                text=True,
                start_new_session=True,
            )
            try:
                out_raw, err_raw = proc.communicate(timeout=max(1.0, float(timeout)))
            except subprocess.TimeoutExpired:
                # Kill whole process group so child processes do not leak when command hangs.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    out_raw, err_raw = proc.communicate(timeout=1.0)
                except Exception:
                    out_raw, err_raw = "", ""
                return {
                    "ok": False,
                    "returncode": -1,
                    "cmd": quoted,
                    "stdout": str(out_raw or "").strip(),
                    "stderr": f"command timed out after {timeout}s",
                    "elapsed_ms": int((time.time() - started) * 1000),
                }

            out = (out_raw or "").strip()
            err = (err_raw or "").strip()
            return {
                "ok": int(proc.returncode) == 0,
                "returncode": int(proc.returncode),
                "cmd": quoted,
                "stdout": out,
                "stderr": err,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except Exception as e:
            return {
                "ok": False,
                "returncode": -1,
                "cmd": quoted,
                "stdout": "",
                "stderr": str(e),
                "elapsed_ms": int((time.time() - started) * 1000),
            }

    def _systemctl(self, action: str, unit: str, tolerate_missing: bool = True) -> Dict[str, Any]:
        cmd = [self._sudo_bin, "-n", self._systemctl_bin, action, unit]
        result = self._run(cmd, timeout=12.0)
        if result["ok"]:
            return result
        err = str(result.get("stderr") or "")
        if tolerate_missing and ("not loaded" in err.lower() or "could not be found" in err.lower()):
            result["ok"] = True
            result["ignored"] = True
        return result

    def _systemctl_is_active(self, unit: str) -> Dict[str, Any]:
        result = self._systemctl("is-active", unit, tolerate_missing=True)
        status = str(result.get("stdout") or "").strip().lower()
        # systemctl is-active returns non-zero for inactive; keep this as non-fatal status info.
        return {
            "ok": status == "active",
            "status": status or ("unknown" if result.get("ok") else "error"),
            "stderr": str(result.get("stderr") or ""),
            "raw": result,
        }

    def _bluetoothctl_single(self, *args: str, timeout: float = 2.5) -> Dict[str, Any]:
        cmd = [self._sudo_bin, "-n", "bluetoothctl", *[a for a in args if a]]
        return self._run(cmd, timeout=timeout)

    def _set_sink_mute(self, mute: bool) -> Dict[str, Any]:
        desired = "1" if mute else "0"
        r = self._run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", desired], timeout=5.0)
        if r["ok"]:
            return r
        return self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", desired], timeout=5.0)

    def _set_sink_volume(self, volume_pct: int) -> Dict[str, Any]:
        pct = max(0, min(150, int(volume_pct)))
        r = self._run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%"], timeout=5.0)
        if r["ok"]:
            return r
        return self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"], timeout=5.0)

    def _audio_muted(self) -> Optional[bool]:
        r = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=4.0)
        txt = (r.get("stdout") or "") if r.get("ok") else ""
        if txt:
            return "[MUTED]" in txt
        r2 = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=4.0)
        txt2 = (r2.get("stdout") or "") if r2.get("ok") else ""
        if txt2:
            return "yes" in txt2.lower()
        return None

    def _bluetooth_connected_devices(self, mode: str) -> List[str]:
        always_query = os.getenv("PARTYBOX_MEDIA_MODE_ALWAYS_QUERY_BT_STATUS", "0") == "1"
        if mode != "bluetooth" and not always_query:
            return []

        result = self._bluetoothctl_single("devices", "Connected", timeout=1.5)
        if not result.get("ok"):
            return []
        out = str(result.get("stdout") or "")
        devices: List[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                devices.append(parts[2].strip())
        return devices

    def _current_mode(self) -> str:
        raw = (DB.get_setting(SETTING_MEDIA_MODE, "") or "").strip().lower()
        if raw in VALID_MEDIA_MODE_SET:
            return raw
        av = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
        return "spotify" if av == "spotify" else "partybox"

    def _persist_mode(self, mode: str) -> None:
        DB.set_setting(SETTING_MEDIA_MODE, mode)
        DB.set_setting("av_mode", "spotify" if mode == "spotify" else "partybox")
        DB.set_setting("tv_paused", "0" if mode == "partybox" else "1")
        DB.set_setting("tv_muted", "1" if mode == "mute" else "0")
        DB.set_setting(SETTING_LAST_SWITCH_TS, str(int(time.time())))

    def _persist_last_error(self, error: str) -> None:
        DB.set_setting(SETTING_LAST_ERROR, (error or "").strip())

    def _persist_actions(self, actions: List[Dict[str, Any]]) -> None:
        try:
            DB.set_setting(SETTING_LAST_ACTIONS, json.dumps(actions)[-20000:])
        except Exception:
            pass

    def _service_states(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for unit in sorted(set(MANAGED_MODE_SERVICES + ("bluetooth.service",))):
            st = self._systemctl_is_active(unit)
            out[unit] = {"active": bool(st["ok"]), "status": st["status"], "stderr": st["stderr"]}
        return out

    def _apply_mode(
        self,
        mode: str,
        previous_mode: str,
        actions: List[Dict[str, Any]],
        stop_spotify_playback_cb: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        if mode not in VALID_MEDIA_MODE_SET:
            raise ValueError(f"invalid media mode: {mode}")

        start_units = list(MODE_START_SERVICES.get(mode, ()))

        if mode != "spotify" and stop_spotify_playback_cb is not None:
            try:
                pause_result = stop_spotify_playback_cb() or {}
            except Exception as e:
                pause_result = {"ok": False, "error": str(e)}
            pause_result["action"] = "spotify_pause_attempt"
            actions.append(pause_result)
            _log(f"spotify pause attempt: ok={bool(pause_result.get('ok'))} detail={pause_result}")

        for unit in MANAGED_MODE_SERVICES:
            if unit in start_units:
                continue
            r = self._systemctl("stop", unit, tolerate_missing=True)
            r["action"] = "systemctl_stop"
            r["unit"] = unit
            actions.append(r)

        # Only disable BT discoverable/pairable when actually leaving bluetooth mode.
        if previous_mode == "bluetooth" and mode != "bluetooth":
            for bt_args in (("pairable", "off"), ("discoverable", "off")):
                r = self._bluetoothctl_single(*bt_args)
                r["action"] = "bluetoothctl"
                r["args"] = " ".join(bt_args)
                actions.append(r)

        if mode == "mute":
            actions.append({"action": "sink_mute", **self._set_sink_mute(True)})
            actions.append({"action": "sink_volume", **self._set_sink_volume(35)})
        else:
            actions.append({"action": "sink_mute", **self._set_sink_mute(False)})

        for unit in start_units:
            r = self._systemctl("start", unit, tolerate_missing=False)
            r["action"] = "systemctl_start"
            r["unit"] = unit
            actions.append(r)
            if not r.get("ok"):
                raise RuntimeError(f"failed to start {unit}: {r.get('stderr') or r.get('stdout')}")

        if mode == "bluetooth":
            bt_steps = [
                ("power", "on"),
                ("pairable", "on"),
                ("discoverable", "on"),
                ("system-alias", self._bt_alias),
            ]
            for bt_args in bt_steps:
                r = self._bluetoothctl_single(*bt_args)
                r["action"] = "bluetoothctl"
                r["args"] = " ".join(bt_args)
                actions.append(r)

        for unit in start_units:
            st = self._systemctl_is_active(unit)
            actions.append({"action": "systemctl_verify_active", "unit": unit, **st})
            if not st.get("ok"):
                raise RuntimeError(f"{unit} is not active after start ({st.get('status')})")

    def get_media_mode_status(self, refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        if (
            not refresh
            and self._status_cache is not None
            and (now - self._status_cache_ts) < self._status_cache_ttl
        ):
            return dict(self._status_cache)

        mode = self._current_mode()
        last_actions_raw = DB.get_setting(SETTING_LAST_ACTIONS, "") or ""
        try:
            last_actions = json.loads(last_actions_raw) if last_actions_raw else []
        except Exception:
            last_actions = []
        payload = {
            "mode": mode,
            "valid_modes": list(VALID_MEDIA_MODES),
            "last_switch_ts": int((DB.get_setting(SETTING_LAST_SWITCH_TS, "0") or "0").strip() or "0"),
            "last_error": (DB.get_setting(SETTING_LAST_ERROR, "") or "").strip(),
            "services": self._service_states(),
            "audio_muted": self._audio_muted(),
            "bluetooth_connected_devices": self._bluetooth_connected_devices(mode),
            "last_actions": last_actions[-20:] if isinstance(last_actions, list) else [],
        }
        self._status_cache = dict(payload)
        self._status_cache_ts = time.time()
        return payload

    def make_bluetooth_discoverable(self, seconds: int = 300) -> Dict[str, Any]:
        duration = max(30, min(1800, int(seconds or 300)))
        actions: List[Dict[str, Any]] = []

        for unit in ("bluetooth.service", "partybox-bluetooth.service"):
            r = self._systemctl("start", unit, tolerate_missing=False)
            r["action"] = "systemctl_start"
            r["unit"] = unit
            actions.append(r)
            if not r.get("ok"):
                msg = f"failed to start {unit}: {r.get('stderr') or r.get('stdout')}"
                self._persist_last_error(msg)
                self._persist_actions(actions)
                return {"ok": False, "error": msg, "status": self.get_media_mode_status(refresh=True)}

        for bt_args in (
            ("power", "on"),
            ("pairable", "on"),
            ("discoverable-timeout", str(duration)),
            ("discoverable", "on"),
            ("system-alias", self._bt_alias),
        ):
            r = self._bluetoothctl_single(*bt_args)
            r["action"] = "bluetoothctl"
            r["args"] = " ".join(bt_args)
            actions.append(r)

        self._status_cache_ts = 0.0
        self._persist_actions(actions)
        return {
            "ok": True,
            "seconds": duration,
            "status": self.get_media_mode_status(refresh=True),
        }

    def _ensure_mode_active(
        self,
        mode: str,
        actions: List[Dict[str, Any]],
    ) -> None:
        start_units = list(MODE_START_SERVICES.get(mode, ()))

        if mode == "mute":
            actions.append({"action": "sink_mute", **self._set_sink_mute(True)})
            actions.append({"action": "sink_volume", **self._set_sink_volume(35)})
        else:
            actions.append({"action": "sink_mute", **self._set_sink_mute(False)})

        for unit in start_units:
            r = self._systemctl("start", unit, tolerate_missing=False)
            r["action"] = "systemctl_start"
            r["unit"] = unit
            actions.append(r)
            if not r.get("ok"):
                raise RuntimeError(f"failed to start {unit}: {r.get('stderr') or r.get('stdout')}")

        if mode == "bluetooth":
            bt_steps = [
                ("power", "on"),
                ("pairable", "on"),
                ("discoverable", "on"),
                ("system-alias", self._bt_alias),
            ]
            for bt_args in bt_steps:
                r = self._bluetoothctl_single(*bt_args)
                r["action"] = "bluetoothctl"
                r["args"] = " ".join(bt_args)
                actions.append(r)

        for unit in start_units:
            st = self._systemctl_is_active(unit)
            actions.append({"action": "systemctl_verify_active", "unit": unit, **st})
            if not st.get("ok"):
                raise RuntimeError(f"{unit} is not active after start ({st.get('status')})")

    def set_media_mode(
        self,
        mode: str,
        stop_spotify_playback_cb: Optional[Callable[[], Dict[str, Any]]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        target = (mode or "").strip().lower()
        if target not in VALID_MEDIA_MODE_SET:
            return {"ok": False, "error": "bad mode", "mode": self._current_mode(), "status": self.get_media_mode_status(refresh=True)}

        current = self._current_mode()
        if current == target and not force:
            _log(f"set_media_mode no-op: already {target}")
            self._persist_last_error("")
            status = self.get_media_mode_status(refresh=True)
            status["noop"] = True
            return {"ok": True, "mode": current, "status": status}

        if current == target and force:
            _log(f"set_media_mode ensure active: {target}")
            actions: List[Dict[str, Any]] = []
            self._persist_last_error("")
            try:
                self._ensure_mode_active(target, actions)
                self._persist_mode(target)
                self._persist_actions(actions)
                self._persist_last_error("")
                self._status_cache_ts = 0.0
                status = self.get_media_mode_status(refresh=True)
                status["ensured"] = True
                return {"ok": True, "mode": target, "status": status}
            except Exception as e:
                err = str(e)
                actions.append({"action": "ensure_error", "error": err})
                self._persist_actions(actions)
                self._persist_last_error(err)
                self._status_cache_ts = 0.0
                return {"ok": False, "error": err, "mode": self._current_mode(), "status": self.get_media_mode_status(refresh=True)}

        _log(f"switch media mode: {current} -> {target}")
        actions: List[Dict[str, Any]] = []
        self._persist_last_error("")
        try:
            self._apply_mode(target, current, actions, stop_spotify_playback_cb=stop_spotify_playback_cb)
            self._persist_mode(target)
            self._persist_actions(actions)
            self._persist_last_error("")
            self._status_cache_ts = 0.0
            return {"ok": True, "mode": target, "status": self.get_media_mode_status(refresh=True)}
        except Exception as e:
            err = str(e)
            _log(f"switch failed: {err}")
            actions.append({"action": "switch_error", "error": err})
            fallback_ok = False
            if target != "mute":
                try:
                    _log("attempting fallback to mute mode")
                    self._apply_mode("mute", target, actions, stop_spotify_playback_cb=None)
                    self._persist_mode("mute")
                    fallback_ok = True
                except Exception as e2:
                    err = f"{err}; fallback_to_mute_failed={e2}"
                    actions.append({"action": "fallback_error", "error": str(e2)})
            self._persist_actions(actions)
            self._persist_last_error(err)
            self._status_cache_ts = 0.0
            status = self.get_media_mode_status(refresh=True)
            return {
                "ok": False,
                "error": err,
                "mode": "mute" if fallback_ok else self._current_mode(),
                "status": status,
            }
