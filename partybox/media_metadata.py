from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _safe_text(value: Any, max_len: int = 200) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:max_len]


class MediaMetadataProbe:
    """
    Best-effort metadata probes for AirPlay and Bluetooth receiver modes.

    AirPlay:
    - Uses shairport-sync DBus / MPRIS when available.
    - Optionally reads a local metadata JSON file if configured.

    Bluetooth:
    - Detects connected device via bluetoothctl.
    - Attempts AVRCP metadata via BlueZ DBus MediaControl1/MediaPlayer1.
    - Falls back to stream/activity detection only.
    """

    def __init__(self) -> None:
        self.cache_ttl = max(1.0, float(os.getenv("PARTYBOX_EXTERNAL_METADATA_CACHE_TTL_SECONDS", "3") or "3"))
        self.airplay_meta_path = (os.getenv("PARTYBOX_AIRPLAY_METADATA_PATH", "") or "").strip()
        self.airplay_meta_max_age = max(1.0, float(os.getenv("PARTYBOX_AIRPLAY_METADATA_MAX_AGE_SECONDS", "20") or "20"))
        self._cache: Dict[str, Dict[str, Any]] = {}

    def snapshot(self, mode: str) -> Dict[str, Any]:
        mode_key = (mode or "").strip().lower()
        if mode_key not in ("airplay", "bluetooth"):
            return {
                "mode": mode_key or "unknown",
                "active": False,
                "metadata_available": False,
                "title": "",
                "artist": "",
                "album": "",
                "provider_id": "",
                "uri": "",
                "device_name": "",
                "device_id": "",
                "note": "",
                "ts": int(time.time()),
            }

        now = time.time()
        cached = self._cache.get(mode_key) or {}
        if (now - float(cached.get("ts_mono") or 0.0)) < self.cache_ttl:
            return dict(cached.get("payload") or {})

        payload = self._airplay_snapshot() if mode_key == "airplay" else self._bluetooth_snapshot()
        self._cache[mode_key] = {"ts_mono": now, "payload": dict(payload)}
        return payload

    def _run(self, cmd: list[str], timeout: float = 1.5) -> Tuple[bool, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            if proc.returncode != 0:
                return False, _safe_text(proc.stderr or proc.stdout, max_len=600)
            return True, str(proc.stdout or "")
        except Exception as exc:
            return False, _safe_text(exc, max_len=600)

    def _systemd_active(self, unit: str) -> bool:
        ok, out = self._run(["systemctl", "is-active", unit], timeout=1.2)
        if not ok:
            return False
        return (out or "").strip().lower() == "active"

    def _sink_inputs_short(self) -> str:
        ok, out = self._run(["pactl", "list", "sink-inputs", "short"], timeout=1.2)
        return out if ok else ""

    def _stream_matches(self, tokens: tuple[str, ...]) -> bool:
        listing = self._sink_inputs_short().lower()
        if not listing:
            return False
        return any(tok in listing for tok in tokens)

    def _read_airplay_metadata_file(self) -> Dict[str, Any]:
        path_raw = self.airplay_meta_path
        if not path_raw:
            return {}
        path = Path(path_raw)
        if not path.exists() or not path.is_file():
            return {}
        try:
            stat = path.stat()
            if (time.time() - float(stat.st_mtime)) > self.airplay_meta_max_age:
                return {}
            raw = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return {}
        if not raw:
            return {}

        candidates = [raw]
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if lines:
            candidates.append(lines[-1])
        for chunk in candidates:
            if not chunk.startswith("{"):
                continue
            try:
                parsed = json.loads(chunk)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    @staticmethod
    def _airplay_track_from_json(payload: Dict[str, Any]) -> Dict[str, str]:
        title = _safe_text(payload.get("title") or payload.get("name"), max_len=200)
        artist = _safe_text(payload.get("artist"), max_len=200)
        album = _safe_text(payload.get("album"), max_len=200)
        track_id = _safe_text(payload.get("track_id") or payload.get("provider_id") or payload.get("id"), max_len=200)
        uri = _safe_text(payload.get("uri"), max_len=512)
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "provider_id": track_id,
            "uri": uri,
        }

    def _busctl_get_property_raw(
        self,
        service: str,
        path: str,
        interface: str,
        prop: str,
        timeout: float = 1.5,
    ) -> str:
        ok, out = self._run(
            ["busctl", "--system", "get-property", service, path, interface, prop],
            timeout=timeout,
        )
        return out if ok else ""

    @staticmethod
    def _parse_busctl_string(raw: str) -> str:
        if not raw:
            return ""
        m = re.search(r'"([^"]*)"', raw)
        if m:
            return _safe_text(m.group(1), max_len=300)
        parts = raw.strip().split(" ", 1)
        if len(parts) >= 2 and parts[0] == "s":
            return _safe_text(parts[1].strip().strip('"'), max_len=300)
        return ""

    @staticmethod
    def _parse_busctl_bool(raw: str) -> bool:
        return bool(raw and "true" in raw.lower())

    @staticmethod
    def _parse_mpris_metadata(raw: str) -> Dict[str, str]:
        if not raw:
            return {}
        title = ""
        artist = ""
        album = ""
        uri = ""

        m_title = re.search(r'"xesam:title"\s+v\s+s\s+"([^"]*)"', raw)
        if m_title:
            title = _safe_text(m_title.group(1), max_len=200)

        m_album = re.search(r'"xesam:album"\s+v\s+s\s+"([^"]*)"', raw)
        if m_album:
            album = _safe_text(m_album.group(1), max_len=200)

        m_url = re.search(r'"xesam:url"\s+v\s+s\s+"([^"]*)"', raw)
        if m_url:
            uri = _safe_text(m_url.group(1), max_len=512)

        m_artist_arr = re.search(r'"xesam:artist"\s+v\s+as\s+\d+\s+(.+)', raw)
        if m_artist_arr:
            quoted = re.findall(r'"([^"]+)"', m_artist_arr.group(1))
            artist = _safe_text(", ".join([q for q in quoted if q]), max_len=200)

        # Fallback keys for non-MPRIS maps.
        m_title2 = re.search(r'"title"\s+v\s+s\s+"([^"]*)"', raw)
        if not title and m_title2:
            title = _safe_text(m_title2.group(1), max_len=200)
        m_album2 = re.search(r'"album"\s+v\s+s\s+"([^"]*)"', raw)
        if not album and m_album2:
            album = _safe_text(m_album2.group(1), max_len=200)
        m_artist2 = re.search(r'"artist"\s+v\s+s\s+"([^"]*)"', raw)
        if not artist and m_artist2:
            artist = _safe_text(m_artist2.group(1), max_len=200)

        return {
            "title": title,
            "artist": artist,
            "album": album,
            "uri": uri,
        }

    def _airplay_dbus_snapshot(self) -> Dict[str, Any]:
        playback_raw = self._busctl_get_property_raw(
            "org.mpris.MediaPlayer2.ShairportSync",
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            "PlaybackStatus",
        )
        metadata_raw = self._busctl_get_property_raw(
            "org.mpris.MediaPlayer2.ShairportSync",
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            "Metadata",
        )

        active_raw = self._busctl_get_property_raw(
            "org.gnome.ShairportSync",
            "/org/gnome/ShairportSync",
            "org.gnome.ShairportSync",
            "Active",
        )
        player_state_raw = self._busctl_get_property_raw(
            "org.gnome.ShairportSync",
            "/org/gnome/ShairportSync",
            "org.gnome.ShairportSync.RemoteControl",
            "PlayerState",
        )
        client_raw = self._busctl_get_property_raw(
            "org.gnome.ShairportSync",
            "/org/gnome/ShairportSync",
            "org.gnome.ShairportSync.RemoteControl",
            "Client",
        )
        metadata2_raw = self._busctl_get_property_raw(
            "org.gnome.ShairportSync",
            "/org/gnome/ShairportSync",
            "org.gnome.ShairportSync.RemoteControl",
            "Metadata",
        )

        playback = self._parse_busctl_string(playback_raw)
        player_state = self._parse_busctl_string(player_state_raw)
        client = self._parse_busctl_string(client_raw)
        active_flag = self._parse_busctl_bool(active_raw)
        mpris_meta = self._parse_mpris_metadata(metadata_raw)
        rc_meta = self._parse_mpris_metadata(metadata2_raw)

        title = _safe_text(mpris_meta.get("title") or rc_meta.get("title"), max_len=200)
        artist = _safe_text(mpris_meta.get("artist") or rc_meta.get("artist"), max_len=200)
        album = _safe_text(mpris_meta.get("album") or rc_meta.get("album"), max_len=200)
        uri = _safe_text(mpris_meta.get("uri") or rc_meta.get("uri"), max_len=512)

        metadata_available = bool(title or artist or album)
        playing = (playback.lower() == "playing") or (player_state.lower() == "playing")
        active = bool(playing or active_flag or client)

        return {
            "active": active,
            "playing": playing,
            "metadata_available": metadata_available,
            "title": title,
            "artist": artist,
            "album": album,
            "uri": uri,
            "client": client,
            "playback_status": playback,
            "player_state": player_state,
        }

    def _airplay_snapshot(self) -> Dict[str, Any]:
        service_active = self._systemd_active("partybox-airplay.service") or self._systemd_active("shairport-sync.service")
        dbus = self._airplay_dbus_snapshot()
        stream_active = bool(dbus.get("active")) or self._stream_matches(("shairport", "raop"))
        meta_payload = self._read_airplay_metadata_file()
        meta = self._airplay_track_from_json(meta_payload) if meta_payload else {}

        title = _safe_text(dbus.get("title") or meta.get("title"))
        artist = _safe_text(dbus.get("artist") or meta.get("artist"))
        album = _safe_text(dbus.get("album") or meta.get("album"))
        provider_id = _safe_text(meta.get("provider_id"), max_len=200)
        uri = _safe_text(dbus.get("uri") or meta.get("uri"), max_len=512)
        metadata_available = bool(title or artist or album or provider_id)
        active = bool(stream_active or (service_active and metadata_available))

        if not provider_id and (title or artist or album):
            digest = hashlib.sha1(f"{title}|{artist}|{album}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            provider_id = f"airplay:{digest}"

        note = ""
        if not metadata_available and bool(dbus.get("active")):
            note = "dbus_active_no_track_metadata"
        elif not self.airplay_meta_path:
            note = "metadata_file_unconfigured"
        elif not metadata_available and service_active:
            note = "metadata_unavailable"

        return {
            "mode": "airplay",
            "active": active,
            "service_active": service_active,
            "stream_active": stream_active,
            "metadata_available": metadata_available,
            "title": title,
            "artist": artist,
            "album": album,
            "provider_id": provider_id,
            "uri": uri,
            "device_name": "AirPlay",
            "device_id": _safe_text(dbus.get("client"), max_len=120),
            "note": note,
            "ts": int(time.time()),
        }

    @staticmethod
    def _mac_to_dbus_path(mac: str) -> str:
        cleaned = (mac or "").strip().replace(":", "_").replace("-", "_")
        return f"/org/bluez/hci0/dev_{cleaned}" if cleaned else ""

    def _bluetooth_connected_device(self) -> Tuple[str, str]:
        ok, out = self._run(["bluetoothctl", "devices", "Connected"], timeout=1.5)
        if not ok:
            return "", ""
        for line in (out or "").splitlines():
            text = line.strip()
            if not text.startswith("Device "):
                continue
            parts = text.split(" ", 2)
            if len(parts) >= 3:
                return parts[1].strip(), parts[2].strip()
            if len(parts) == 2:
                return parts[1].strip(), ""
        return "", ""

    def _busctl_get_property(self, path: str, interface: str, prop: str, timeout: float = 1.5) -> str:
        ok, out = self._run(
            ["busctl", "--system", "get-property", "org.bluez", path, interface, prop],
            timeout=timeout,
        )
        return out if ok else ""

    def _bluez_player_path_for_device(self, dev_path: str) -> str:
        raw = self._busctl_get_property(dev_path, "org.bluez.MediaControl1", "Player")
        if not raw:
            return ""
        m = re.search(r'"(/org/bluez/[^"]+)"', raw)
        if m:
            return m.group(1)
        parts = raw.strip().split()
        if parts and parts[0] == "o" and len(parts) >= 2:
            cand = parts[1].strip().strip('"')
            if cand.startswith("/org/bluez/"):
                return cand
        return ""

    @staticmethod
    def _parse_bluez_track(track_raw: str) -> Dict[str, str]:
        if not track_raw:
            return {}
        title = ""
        album = ""
        artist = ""

        # BlueZ may emit either:
        # - a{sv} ... "Title" s "..."
        # - a{sv} ... "Title" v s "..."
        m_title = re.search(r'"Title"\s+(?:v\s+)?s\s+"([^"]*)"', track_raw)
        if m_title:
            title = _safe_text(m_title.group(1), max_len=200)

        m_album = re.search(r'"Album"\s+(?:v\s+)?s\s+"([^"]*)"', track_raw)
        if m_album:
            album = _safe_text(m_album.group(1), max_len=200)

        m_artist_arr = re.search(r'"Artist"\s+(?:v\s+)?as\s+\d+\s+(.+)', track_raw)
        if m_artist_arr:
            quoted = re.findall(r'"([^"]+)"', m_artist_arr.group(1))
            artist = _safe_text(", ".join([q for q in quoted if q]), max_len=200)
        if not artist:
            m_artist_str = re.search(r'"Artist"\s+(?:v\s+)?s\s+"([^"]*)"', track_raw)
            if m_artist_str:
                artist = _safe_text(m_artist_str.group(1), max_len=200)

        return {
            "title": title,
            "artist": artist,
            "album": album,
        }

    def _bluetooth_metadata(self, device_id: str) -> Dict[str, str]:
        dev_path = self._mac_to_dbus_path(device_id)
        if not dev_path:
            return {}
        player_path = self._bluez_player_path_for_device(dev_path)
        if not player_path:
            return {}
        track_raw = self._busctl_get_property(player_path, "org.bluez.MediaPlayer1", "Track")
        if not track_raw:
            return {}
        meta = self._parse_bluez_track(track_raw)
        title = _safe_text(meta.get("title"))
        artist = _safe_text(meta.get("artist"))
        album = _safe_text(meta.get("album"))
        provider_id = ""
        if title or artist or album:
            digest = hashlib.sha1(f"{title}|{artist}|{album}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            provider_id = f"bt:{digest}"
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "provider_id": provider_id,
            "uri": "",
        }

    def _bluetooth_snapshot(self) -> Dict[str, Any]:
        service_active = self._systemd_active("bluetooth.service")
        device_id, device_name = self._bluetooth_connected_device()
        stream_active = self._stream_matches(("bluez", "a2dp", "bluetooth"))
        metadata = self._bluetooth_metadata(device_id) if device_id else {}

        title = _safe_text(metadata.get("title"))
        artist = _safe_text(metadata.get("artist"))
        album = _safe_text(metadata.get("album"))
        provider_id = _safe_text(metadata.get("provider_id"), max_len=200)
        uri = _safe_text(metadata.get("uri"), max_len=512)
        metadata_available = bool(title or artist or album or provider_id)
        active = bool(stream_active or metadata_available)

        note = ""
        if not device_id:
            note = "no_connected_device"
        elif not metadata_available:
            note = "avrcp_metadata_unavailable"

        return {
            "mode": "bluetooth",
            "active": active,
            "service_active": service_active,
            "stream_active": stream_active,
            "metadata_available": metadata_available,
            "title": title,
            "artist": artist,
            "album": album,
            "provider_id": provider_id,
            "uri": uri,
            "device_name": _safe_text(device_name, max_len=120),
            "device_id": _safe_text(device_id, max_len=40),
            "note": note,
            "ts": int(time.time()),
        }
