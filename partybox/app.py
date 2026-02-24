# partybox/app.py
from __future__ import annotations

import os
import re
import json
import time
import shlex
import secrets
import ssl
import base64
import socket
import subprocess
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import db as DB
from .spotify_client import SpotifyClient

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
HEALTH_CACHE: Dict[str, Any] = {"ts": 0.0, "status": 503, "payload": None}


def _media_dir() -> str:
    media_dir = os.getenv("PARTYBOX_MEDIA_DIR", "")
    if media_dir:
        return os.path.abspath(media_dir)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))


def _admin_or_403() -> str:
    admin_key = DB.get_setting("admin_key", "JBOX")
    key = request.args.get("key", "")
    if key != admin_key:
        abort(403)
    return key


def _sync_local_media() -> Dict[str, Any]:
    """
    Scan media dir for .mp4 files and ensure each has a catalog entry:
      youtube_id = file:<filename>
      title      = filename sans extension (unless already exists in DB)

    ALSO:
      - disable catalog entries whose local file is missing
      - remove queued items whose local file is missing
      - RE-ENABLE catalog entries whose file exists on disk
    """
    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    base = Path(media_dir)

    added = 0
    seen = 0

    # Track which local tokens exist on disk
    existing_tokens: set[str] = set()

    for name in sorted(os.listdir(media_dir)):
        if not name.lower().endswith(".mp4"):
            continue
        if ".." in name or name.startswith("/"):
            continue

        p = base / name
        if not p.is_file():
            continue

        seen += 1
        youtube_id = f"file:{name}"
        existing_tokens.add(youtube_id)

        title = os.path.splitext(name)[0]
        if DB.upsert_catalog_item(title=title, youtube_id=youtube_id):
            added += 1

    # Re-enable catalog items whose local file exists (important!)
    reenabled = 0
    for it in DB.list_catalog(enabled_only=False):
        yid = (it.get("youtube_id") or "").strip()
        if not yid.startswith("file:"):
            continue
        if yid not in existing_tokens:
            continue
        try:
            if not bool(it.get("enabled", True)):
                DB.set_catalog_enabled(int(it["id"]), True)
                reenabled += 1
        except Exception:
            pass

    # Disable catalog items whose file is missing
    disabled = 0
    for it in DB.list_catalog(enabled_only=False):
        yid = (it.get("youtube_id") or "").strip()
        if not yid.startswith("file:"):
            continue
        if yid in existing_tokens:
            continue
        try:
            DB.set_catalog_enabled(int(it["id"]), False)
            disabled += 1
        except Exception:
            pass

    # Remove queued items whose file is missing
    removed_from_queue = 0
    for q in DB.list_queue(limit=1000):
        yid = (q.get("youtube_id") or "").strip()
        if not yid.startswith("file:"):
            continue
        if yid in existing_tokens:
            continue
        try:
            DB.remove_from_queue(int(q["id"]))
            removed_from_queue += 1
        except Exception:
            pass

    return {
        "media_dir": media_dir,
        "seen_mp4": seen,
        "added": added,
        "reenabled_present": reenabled,
        "disabled_missing": disabled,
        "queue_removed_missing": removed_from_queue,
    }


def _is_youtube_url(s: str) -> bool:
    """
    True if s looks like a YouTube URL we can hand directly to mpv/ytdl.
    """
    if "://" not in s:
        return False
    try:
        u = urlparse(s)
        host = (u.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com")
    except Exception:
        return False


def _extract_youtube_id_from_url(raw: str) -> Optional[str]:
    """
    Best-effort extraction for common forms:
      - https://youtu.be/<id>
      - https://www.youtube.com/watch?v=<id>
      - https://www.youtube.com/shorts/<id>
    Returns id or None if not found.
    """
    s = (raw or "").strip()
    if not s:
        return None

    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", s)
    if not m:
        m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", s)
    if not m:
        m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", s)
    if m:
        return m.group(1)
    return None


def _youtube_url_from_token(token: str) -> Optional[str]:
    if not token:
        return None
    if "://" in token:
        return token
    return f"https://www.youtube.com/watch?v={token}"


def _fetch_youtube_title(token: str) -> Optional[str]:
    """
    Best-effort title fetch via yt-dlp. Returns None on any failure.
    """
    url = _youtube_url_from_token(token)
    if not url:
        return None
    try:
        proc = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download", "--print", "%(title)s", url],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        title = (proc.stdout or "").strip()
        return title or None
    except Exception:
        return None


def _parse_systemctl_show(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _service_check(unit: str, expected_user: Optional[str] = None) -> Dict[str, Any]:
    props = [
        "LoadState",
        "ActiveState",
        "SubState",
        "User",
        "MainPID",
        "Result",
        "UnitFileState",
    ]
    cmd = ["systemctl", "show", unit, *[f"--property={p}" for p in props]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, check=False)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            return {"ok": False, "error": err}

        data = _parse_systemctl_show(proc.stdout)
        active = data.get("ActiveState") == "active"
        pid_ok = (data.get("MainPID") or "0") != "0"
        user = data.get("User") or ""

        ok = active and pid_ok
        if expected_user and user and user != expected_user:
            ok = False

        result: Dict[str, Any] = {
            "ok": ok,
            "active_state": data.get("ActiveState"),
            "sub_state": data.get("SubState"),
            "main_pid": int(data.get("MainPID") or 0),
            "user": user,
        }
        if expected_user:
            result["expected_user"] = expected_user
        if data.get("Result"):
            result["result"] = data.get("Result")
        if data.get("UnitFileState"):
            result["unit_file_state"] = data.get("UnitFileState")
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _internet_check() -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    tcp_ok = False
    https_ok = False

    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.0):
            pass
        tcp_ok = True
        checks["tcp_1_1_1_1_53"] = {"ok": True}
    except Exception as e:
        checks["tcp_1_1_1_1_53"] = {"ok": False, "error": str(e)}

    try:
        req = urllib.request.Request(
            "https://www.google.com/generate_204",
            headers={"User-Agent": "partybox-health/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            code = int(getattr(resp, "status", 0) or 0)
        https_ok = code in (200, 204)
        checks["https_generate_204"] = {"ok": https_ok, "status": code}
    except urllib.error.URLError as e:
        checks["https_generate_204"] = {"ok": False, "error": str(e.reason)}
    except Exception as e:
        checks["https_generate_204"] = {"ok": False, "error": str(e)}

    return {"ok": bool(tcp_ok or https_ok), "checks": checks}


def _tv_heartbeat_check(max_age_seconds: int = 20) -> Dict[str, Any]:
    raw = DB.get_setting("tv_heartbeat_json", "") or ""
    if not raw:
        return {"ok": False, "error": "missing heartbeat"}
    try:
        hb = json.loads(raw)
        hb_ts = int(hb.get("ts") or 0)
        age = max(0, int(time.time()) - hb_ts)
        return {
            "ok": age <= max_age_seconds,
            "age_seconds": age,
            "max_age_seconds": max_age_seconds,
            "mode": hb.get("mode"),
            "title": (hb.get("title") or "")[:120],
        }
    except Exception as e:
        return {"ok": False, "error": f"invalid heartbeat json: {e}"}


def _filesystem_check() -> Dict[str, Any]:
    raw_paths = os.getenv("PARTYBOX_HEALTH_DISK_PATHS", "/,/home/user/projects/partybox/data/media")
    max_used_pct = float(os.getenv("PARTYBOX_HEALTH_DISK_USED_MAX_PCT", "92"))
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]
    per_path: Dict[str, Any] = {}
    all_ok = True

    for p in paths:
        try:
            target = p if os.path.exists(p) else os.path.dirname(p) or "/"
            total, used, free = shutil.disk_usage(target)
            used_pct = (float(used) / float(total) * 100.0) if total else 0.0
            ok = used_pct <= max_used_pct
            if not ok:
                all_ok = False
            per_path[p] = {
                "ok": ok,
                "used_pct": round(used_pct, 1),
                "max_used_pct": max_used_pct,
                "free_gb": round(free / (1024 ** 3), 2),
                "total_gb": round(total / (1024 ** 3), 2),
            }
        except Exception as e:
            all_ok = False
            per_path[p] = {"ok": False, "error": str(e)}

    return {"ok": all_ok, "paths": per_path, "max_used_pct": max_used_pct}


def _memory_check() -> Dict[str, Any]:
    max_used_pct = float(os.getenv("PARTYBOX_HEALTH_MEM_USED_MAX_PCT", "92"))
    try:
        meminfo: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                num = v.strip().split()[0]
                if num.isdigit():
                    meminfo[k.strip()] = int(num)

        total_kb = int(meminfo.get("MemTotal", 0))
        avail_kb = int(meminfo.get("MemAvailable", 0))
        if total_kb <= 0:
            return {"ok": False, "error": "MemTotal missing", "max_used_pct": max_used_pct}

        used_kb = max(0, total_kb - avail_kb)
        used_pct = (float(used_kb) / float(total_kb)) * 100.0
        ok = used_pct <= max_used_pct
        return {
            "ok": ok,
            "used_pct": round(used_pct, 1),
            "max_used_pct": max_used_pct,
            "available_gb": round(avail_kb / (1024 ** 2), 2),
            "total_gb": round(total_kb / (1024 ** 2), 2),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "max_used_pct": max_used_pct}


def _desktop_autostart_check() -> Dict[str, Any]:
    desktop_file = os.getenv(
        "PARTYBOX_HEALTH_KIOSK_AUTOSTART_FILE",
        "/home/partybox/.config/autostart/partybox-tv.desktop",
    )
    expected_path_fragment = os.getenv("PARTYBOX_HEALTH_KIOSK_EXPECTED_PATH_FRAGMENT", "/tv")

    result: Dict[str, Any] = {
        "ok": False,
        "path": desktop_file,
        "exists": False,
        "contains_expected_path": False,
        "enabled": False,
    }

    try:
        p = Path(desktop_file)
        if not p.exists() or not p.is_file():
            result["error"] = "autostart desktop file missing"
            return result

        result["exists"] = True
        raw = p.read_text(encoding="utf-8", errors="ignore")
        lowered = raw.lower()

        enabled = "hidden=true" not in lowered and "x-gnome-autostart-enabled=false" not in lowered
        contains_expected = expected_path_fragment in raw
        launcher_path = ""

        if not contains_expected:
            exec_line = ""
            for line in raw.splitlines():
                if line.lower().startswith("exec="):
                    exec_line = line[5:].strip()
                    break

            if exec_line:
                result["exec"] = exec_line
                try:
                    argv = shlex.split(exec_line)
                except Exception:
                    argv = [exec_line]

                if argv:
                    candidate = os.path.expanduser(argv[0])
                    if os.path.isabs(candidate):
                        launcher_path = candidate
                        result["launcher_path"] = launcher_path
                        if os.path.isfile(launcher_path):
                            launcher_raw = Path(launcher_path).read_text(encoding="utf-8", errors="ignore")
                            contains_expected = expected_path_fragment in launcher_raw

        result["enabled"] = enabled
        result["contains_expected_path"] = contains_expected
        result["ok"] = bool(enabled and contains_expected)
        if not result["ok"]:
            if not enabled:
                result["error"] = "autostart entry appears disabled"
            elif not contains_expected:
                result["error"] = f"autostart file does not reference expected path fragment '{expected_path_fragment}'"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def _http_status_no_redirect(
    url: str, timeout: float = 2.5, insecure_tls: bool = False
) -> Tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "partybox-health/1.0"},
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            return None

    handlers: list[Any] = [_NoRedirect()]
    if insecure_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or 0), "", str(resp.headers.get("Location") or "")
    except urllib.error.HTTPError as e:
        return (
            int(getattr(e, "code", 0) or 0),
            str(getattr(e, "reason", "") or ""),
            str(getattr(e, "headers", {}).get("Location") if getattr(e, "headers", None) else ""),
        )
    except Exception as e:
        return 0, str(e), ""


def _nginx_http_check() -> Dict[str, Any]:
    admin_key = DB.get_setting("admin_key", "JBOX")
    endpoint_specs = {
        "http_root_redirect": {
            "url": "http://partybox.local/",
            "expected": (302,),
            "location_prefix": "/user",
        },
        "http_user_redirect": {
            "url": "http://partybox.local/user",
            "expected": (302,),
            "location_prefix": "/u",
        },
        "http_tv": {"url": "http://partybox.local/tv", "expected": (200,)},
        "http_u": {"url": "http://partybox.local/u", "expected": (200,)},
        "http_admin": {
            "url": f"http://partybox.local/admin?key={urllib.parse.quote(admin_key)}",
            "expected": (200,),
        },
    }

    per_endpoint: Dict[str, Any] = {}
    all_ok = True

    for name, spec in endpoint_specs.items():
        url = str(spec.get("url") or "")
        expected = tuple(int(x) for x in (spec.get("expected") or ()))
        insecure = bool(spec.get("insecure_tls"))
        location_prefix = str(spec.get("location_prefix") or "")
        status, err, location = _http_status_no_redirect(url, insecure_tls=insecure)
        ok = status in expected
        if ok and location_prefix:
            parsed_location = urllib.parse.urlparse(location or "")
            location_path = str(parsed_location.path or "")
            ok = location.startswith(location_prefix) or location_path.startswith(location_prefix)
        if not ok:
            all_ok = False
        result: Dict[str, Any] = {
            "ok": ok,
            "url": url,
            "expected_status": list(expected),
            "status": status,
        }
        if location:
            result["location"] = location
        if location_prefix:
            result["expected_location_prefix"] = location_prefix
        if err:
            result["error"] = err
        per_endpoint[name] = result

    return {"ok": all_ok, "checks": per_endpoint}


def _build_admin_health() -> Tuple[int, Dict[str, Any]]:
    checks: Dict[str, Any] = {}
    ok = True

    try:
        with DB._connect() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["db_connect"] = {"ok": True}
    except Exception as e:
        ok = False
        checks["db_connect"] = {"ok": False, "error": str(e)}

    try:
        with DB._connect() as conn:
            conn.execute("SELECT COUNT(*) FROM settings").fetchone()
        checks["db_query"] = {"ok": True}
    except Exception as e:
        ok = False
        checks["db_query"] = {"ok": False, "error": str(e)}

    required_services = [
        s.strip()
        for s in os.getenv(
            "PARTYBOX_HEALTH_REQUIRED_SERVICES",
            "partybox.service,lightdm.service,nginx.service,librespot.service",
        ).split(",")
        if s.strip()
    ]
    av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
    service_checks: Dict[str, Any] = {}
    for unit in required_services:
        expected_user = "partybox" if unit.startswith("partybox") else None
        c = _service_check(unit, expected_user=expected_user)
        service_checks[unit] = c

    # librespot is only required while Spotify backend is active.
    # In PartyBox mode, keep it visible in diagnostics but optional by default.
    require_librespot = os.getenv("PARTYBOX_HEALTH_REQUIRE_LIBRESPOT_SERVICE", "0") == "1"
    librespot_check = service_checks.get("librespot.service")
    if isinstance(librespot_check, dict) and av_mode != "spotify" and not require_librespot:
        librespot_check["optional"] = True
        librespot_check["n_a"] = True
        librespot_check["ok"] = True
        librespot_check["note"] = "n/a while PartyBox backend is active"

    require_player = os.getenv("PARTYBOX_HEALTH_REQUIRE_PLAYER_SERVICE", "0") == "1"
    player_expected_user = (
        os.getenv("PARTYBOX_PLAYER_SERVICE_USER", "partybox").strip() if require_player else None
    )
    player_check = _service_check("partybox-player.service", expected_user=player_expected_user)
    # partybox-player may be inactive depending on playback backend and flow.
    # Keep it visible in health details, but optional by default so it does not
    # degrade global health on its own.
    player_check["optional"] = not require_player
    if av_mode == "spotify":
        player_check["n_a"] = True
        player_check["ok"] = True
        player_check["note"] = "n/a while Spotify backend is active"
    elif not require_player and not bool(player_check.get("ok")):
        player_check["ok"] = True
        player_check["note"] = "optional service (inactive is allowed)"
    service_checks["partybox-player.service"] = player_check

    services_ok = True
    for unit_result in service_checks.values():
        unit_result = unit_result or {}
        if unit_result.get("optional"):
            continue
        if not bool(unit_result.get("ok", False)):
            services_ok = False
            break

    checks["services"] = {"ok": services_ok, "units": service_checks}
    ok = ok and services_ok

    hb_max_age = int(os.getenv("PARTYBOX_HEALTH_TV_HEARTBEAT_MAX_AGE_SECONDS", "20"))
    hb = _tv_heartbeat_check(max_age_seconds=hb_max_age)
    if av_mode == "spotify":
        hb["optional"] = True
        hb["n_a"] = True
        hb["ok"] = True
        hb["note"] = "n/a while Spotify backend is active"
    checks["tv_player_heartbeat"] = hb
    # Legacy alias for older clients/scripts.
    checks["tv_heartbeat"] = hb
    ok = ok and bool(hb.get("ok"))

    internet = _internet_check()
    checks["internet"] = internet
    ok = ok and bool(internet.get("ok"))

    nginx_http = _nginx_http_check()
    checks["nginx_http"] = nginx_http
    ok = ok and bool(nginx_http.get("ok"))

    filesystem = _filesystem_check()
    checks["filesystem"] = filesystem
    ok = ok and bool(filesystem.get("ok"))

    memory = _memory_check()
    checks["memory"] = memory
    ok = ok and bool(memory.get("ok"))

    if os.getenv("PARTYBOX_HEALTH_CHECK_DESKTOP_AUTOSTART", "1") == "1":
        desktop_autostart = _desktop_autostart_check()
        checks["desktop_autostart"] = desktop_autostart
        ok = ok and bool(desktop_autostart.get("ok"))

    failed: list[str] = []
    for name, result in checks.items():
        result = result or {}
        if name == "tv_heartbeat":
            # Legacy alias; do not count separately in failed summary.
            continue
        if name == "services":
            units = result.get("units") if isinstance(result.get("units"), dict) else {}
            for unit_name, unit_result in units.items():
                unit_result = unit_result or {}
                if unit_result.get("optional"):
                    continue
                if not bool(unit_result.get("ok", False)):
                    failed.append(f"service:{unit_name}")
            continue
        if not bool(result.get("ok", False)):
            failed.append(name)
    summary = {"failed": len(failed), "failed_checks": failed}

    payload = {
        "ok": ok,
        "checks": checks,
        "summary": summary,
        "ts": int(time.time()),
    }
    return (200 if ok else 503), payload


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../static", template_folder="../templates")
    spotify_client = SpotifyClient.from_env()

    DB.init_db()
    DB.seed_if_empty()

    # Ensure defaults
    if DB.get_setting("tv_muted", None) is None:
        DB.set_setting("tv_muted", "0")
    if DB.get_setting("av_mode", None) is None:
        DB.set_setting("av_mode", "partybox")
    if DB.get_setting("tv_qr_enabled", None) is None:
        DB.set_setting("tv_qr_enabled", "1")

    # Optional: after restart, default to PAUSED so TV does not autoplay.
    # Override with PARTYBOX_START_PAUSED=0 if you want autoplay again.
    if os.getenv("PARTYBOX_START_PAUSED", "1") == "1":
        DB.set_setting("tv_paused", "1")

    # Auto-scan media on service start if enabled
    if os.getenv("PARTYBOX_AUTO_MEDIA_SCAN", "0") == "1":
        try:
            r = _sync_local_media()
            print(f"[partybox] media auto-scan ok: seen={r['seen_mp4']} added={r['added']} dir={r['media_dir']}")
        except Exception as e:
            print(f"[partybox] media auto-scan FAILED: {e}")

    def _bool_setting(name: str, default: str = "0") -> bool:
        """
        Be tolerant: if something ever wrote 'true'/'on' into settings,
        we still behave correctly.
        """
        v = (DB.get_setting(name, default) or "").strip().lower()
        return v in ("1", "true", "yes", "y", "on")

    def _spotify_snapshot(force: bool = False, allow_fetch: bool = True) -> Dict[str, Any]:
        return spotify_client.get_state(force=force, allow_fetch=allow_fetch)

    def _spotify_oauth_scope() -> str:
        return os.getenv("SPOTIFY_SCOPE", "user-read-playback-state user-read-currently-playing").strip()

    def _spotify_redirect_uri() -> str:
        return os.getenv("SPOTIFY_REDIRECT_URI", "https://partybox.local/spotify/callback").strip()

    def _spotify_exchange_code(code: str) -> Tuple[Optional[Dict[str, Any]], str]:
        cid = (os.getenv("SPOTIFY_CLIENT_ID", "") or "").strip()
        csec = (os.getenv("SPOTIFY_CLIENT_SECRET", "") or "").strip()
        redirect_uri = _spotify_redirect_uri()
        if not cid or not csec:
            return None, "missing spotify client credentials in environment"
        if not code:
            return None, "missing oauth code"

        body = urllib.parse.urlencode(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        ).encode("utf-8")

        basic_raw = f"{cid}:{csec}".encode("utf-8")
        basic_auth = base64.b64encode(basic_raw).decode("ascii")
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic_auth}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads((resp.read() or b"{}").decode("utf-8", errors="ignore"))
                return payload, ""
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read() or b""
            except Exception:
                pass
            detail = str(e.reason or "oauth_exchange_failed")
            if raw:
                try:
                    j = json.loads(raw.decode("utf-8", errors="ignore"))
                    detail = str(j.get("error_description") or j.get("error") or detail)
                except Exception:
                    pass
            return None, detail
        except Exception as e:
            return None, str(e)

    def _persist_env_key(key: str, value: str) -> Tuple[bool, str]:
        env_path = Path(os.getenv("PARTYBOX_ENV_FILE", "/etc/partybox.env"))
        if not key:
            return False, "missing key"
        value = (value or "").strip()
        if not value:
            return False, "missing value"
        try:
            existing = []
            if env_path.exists():
                existing = env_path.read_text(encoding="utf-8").splitlines()

            out: list[str] = []
            replaced = False
            prefix = f"{key}="
            for line in existing:
                if line.startswith(prefix):
                    out.append(f"{key}={value}")
                    replaced = True
                else:
                    out.append(line)
            if not replaced:
                out.append(f"{key}={value}")

            env_path.write_text("\\n".join(out).strip() + "\\n", encoding="utf-8")
            return True, str(env_path)
        except Exception as e:
            return False, str(e)

    # ---------- Pages ----------
    @app.get("/")
    def index():
        return redirect(url_for("tv"))

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "ts": int(time.time())})

    @app.get("/tv")
    def tv():
        return render_template("tv.html")

    @app.get("/u")
    def user():
        items = DB.list_catalog(enabled_only=True)
        queue = DB.list_queue(limit=50)
        locked = (DB.get_setting("requests_locked", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
        return render_template("user.html", items=items, locked=locked, queue=queue, av_mode=av_mode)

    @app.get("/user")
    def user_public():
        return redirect(url_for("user"))

    @app.get("/admin")
    def admin():
        key = _admin_or_403()
        items = DB.list_catalog(enabled_only=False)
        q = DB.list_queue(limit=50)
        locked = _bool_setting("requests_locked", "0")
        paused = _bool_setting("tv_paused", "0")
        muted = _bool_setting("tv_muted", "0")
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
        tv_qr_enabled = _bool_setting("tv_qr_enabled", "1")

        return render_template(
            "admin.html",
            items=items,
            locked=locked,
            paused=paused,
            muted=muted,
            queue=q,
            key=key,
            av_mode=av_mode,
            tv_qr_enabled=tv_qr_enabled,
        )

    @app.get("/media/<path:filename>")
    def media(filename: str):
        base = Path(_media_dir())
        name = Path(filename).name  # strips any path

        if not name or name.startswith(".") or ".." in filename or filename.startswith("/"):
            abort(400)

        exact = base / name
        if exact.is_file():
            return send_from_directory(str(base), name, conditional=True)

        # case-insensitive fallback
        try:
            target_lower = name.lower()
            for f in base.iterdir():
                if f.is_file() and f.name.lower() == target_lower:
                    return send_from_directory(str(base), f.name, conditional=True)
        except FileNotFoundError:
            pass

        abort(404)

    # ---------- APIs ----------
    @app.get("/api/state")
    def api_state():
        locked = _bool_setting("requests_locked", "0")
        paused = _bool_setting("tv_paused", "0")
        muted = _bool_setting("tv_muted", "0")
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
        tv_qr_enabled = _bool_setting("tv_qr_enabled", "1")
        key = (request.args.get("key", "") or "").strip()
        admin_key = (DB.get_setting("admin_key", "JBOX") or "JBOX").strip()
        is_admin = bool(key and key == admin_key)
        refresh_spotify = (request.args.get("spotify_refresh", "0") or "").strip() in ("1", "true", "yes", "on")
        force_spotify = bool(is_admin and refresh_spotify)

        if av_mode == "spotify" or force_spotify:
            spotify = _spotify_snapshot(force=force_spotify, allow_fetch=True)
        else:
            spotify = _spotify_snapshot(force=False, allow_fetch=False)
            spotify["state"] = "disabled"
            spotify["ok"] = True
            spotify["error"] = ""

        now = DB.get_now_playing()
        up = DB.peek_next()

        def _pack(qrow: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "queue_id": int(qrow["id"]),
                "title": qrow["title"],
                "youtube_id": qrow["youtube_id"],
                "note_text": qrow.get("note_text") or "",
            }

        # IMPORTANT: When paused, do NOT rotate through idle picks.
        # Only show: currently-playing item, or next queued item, else nothing.
        if paused:
            return jsonify(
                {
                    "locked": locked,
                    "paused": True,
                    "muted": muted,
                    "av_mode": av_mode,
                    "tv_qr_enabled": tv_qr_enabled,
                    "spotify": spotify,
                    "mode": "paused",
                    "now": _pack(now) if now else (_pack(up) if up else None),
                    "up_next": _pack(up) if (now and up) else None,
                }
            )

        # ---- Normal (not paused) behavior ----
        if now:
            return jsonify(
                {
                    "locked": locked,
                    "paused": False,
                    "muted": muted,
                    "av_mode": av_mode,
                    "tv_qr_enabled": tv_qr_enabled,
                    "spotify": spotify,
                    "mode": "playing",
                    "now": _pack(now),
                    "up_next": _pack(up) if up else None,
                }
            )

        if up:
            # nothing marked playing yet -> treat next as now (TV will mark_playing on start)
            return jsonify(
                {
                    "locked": locked,
                    "paused": False,
                    "muted": muted,
                    "av_mode": av_mode,
                    "tv_qr_enabled": tv_qr_enabled,
                    "spotify": spotify,
                    "mode": "queue",
                    "now": _pack(up),
                    "up_next": None,
                }
            )

        return jsonify(
            {
                "locked": locked,
                "paused": False,
                "muted": muted,
                "av_mode": av_mode,
                "tv_qr_enabled": tv_qr_enabled,
                "spotify": spotify,
                "mode": "empty",
                "now": None,
                "up_next": None,
            }
        )

    @app.get("/api/queue")
    def api_queue():
        items = DB.list_queue(limit=100)
        return jsonify({"items": items})

    @app.post("/api/tv/heartbeat")
    def api_tv_heartbeat():
        """
        TV player agent heartbeat (posted by tv_player.py)
        Stores last seen + basic info for admin UI.
        """
        try:
            data = request.get_json(force=True, silent=True) or {}
            mode = str(data.get("mode") or "")
            title = str(data.get("title") or "")
            youtube_id = str(data.get("youtube_id") or "")

            payload = {
                "ts": int(time.time()),
                "mode": mode[:40],
                "title": title[:200],
                "youtube_id": youtube_id[:200],
                "remote_addr": request.remote_addr,
            }

            # Reuse settings table if you have it (k/v text)
            # If your db helper is different, change these 2 lines only.
            DB.set_setting("tv_heartbeat_json", json.dumps(payload))
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.post("/api/tv/mark_playing")
    def api_mark_playing():
        data = request.get_json(force=True, silent=True) or {}
        qid = int(data.get("queue_id", 0))
        if qid <= 0:
            return jsonify({"ok": False, "error": "bad queue_id"}), 400
        DB.mark_playing(qid)
        return jsonify({"ok": True})

    @app.post("/api/tv/mark_done")
    def api_mark_done():
        data = request.get_json(force=True, silent=True) or {}
        qid = int(data.get("queue_id", 0))
        if qid <= 0:
            return jsonify({"ok": False, "error": "bad queue_id"}), 400
        DB.mark_done(qid)
        return jsonify({"ok": True})

    @app.post("/api/request_video")
    def api_request_video():
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
        if av_mode == "spotify":
            return jsonify({"ok": False, "error": "spotify mode"}), 409

        if _bool_setting("requests_locked", "0"):
            return jsonify({"ok": False, "error": "requests locked"}), 403

        data = request.get_json(force=True, silent=True) or {}
        catalog_id = int(data.get("catalog_id", 0))
        note_text = (data.get("note_text") or "").strip()

        if catalog_id <= 0:
            return jsonify({"ok": False, "error": "bad catalog_id"}), 400

        qid = DB.enqueue(catalog_id, note_text=note_text[:120] if note_text else None)
        return jsonify({"ok": True, "queue_id": qid})

    @app.get("/spotify/auth")
    def spotify_auth():
        client_id = (os.getenv("SPOTIFY_CLIENT_ID", "") or "").strip()
        if not client_id:
            return jsonify({"ok": False, "error": "SPOTIFY_CLIENT_ID missing"}), 500

        state = secrets.token_urlsafe(16)
        DB.set_setting("spotify_oauth_state", state)
        redirect_uri = _spotify_redirect_uri()
        q = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": _spotify_oauth_scope(),
                "state": state,
                "show_dialog": "true",
            }
        )
        return redirect(f"https://accounts.spotify.com/authorize?{q}")

    @app.get("/spotify/callback")
    def spotify_callback():
        err = (request.args.get("error", "") or "").strip()
        if err:
            return f"<h3>Spotify OAuth failed</h3><p>{err}</p>", 400

        code = (request.args.get("code", "") or "").strip()
        state = (request.args.get("state", "") or "").strip()
        expected = (DB.get_setting("spotify_oauth_state", "") or "").strip()
        if expected and state != expected:
            return "<h3>Spotify OAuth failed</h3><p>State mismatch.</p>", 400
        if not code:
            return "<h3>Spotify OAuth failed</h3><p>Missing code.</p>", 400

        token_payload, token_err = _spotify_exchange_code(code)
        if not token_payload:
            return f"<h3>Spotify OAuth failed</h3><p>{token_err}</p>", 400

        refresh_token = str(token_payload.get("refresh_token") or "").strip()
        if not refresh_token:
            # Spotify may omit refresh_token on some re-auth cases; keep current if present.
            refresh_token = (os.getenv("SPOTIFY_REFRESH_TOKEN", "") or "").strip()
            if not refresh_token:
                return "<h3>Spotify OAuth failed</h3><p>No refresh token returned.</p>", 400

        spotify_client.refresh_token = refresh_token
        spotify_client._access_token = ""
        spotify_client._access_token_expires_at = 0.0
        spotify_client.enabled = bool(spotify_client.client_id and spotify_client.client_secret and spotify_client.refresh_token)

        ok_write, detail = _persist_env_key("SPOTIFY_REFRESH_TOKEN", refresh_token)
        persist_line = (
            f"Stored SPOTIFY_REFRESH_TOKEN in {detail}."
            if ok_write
            else f"Token exchange succeeded but could not update /etc/partybox.env automatically ({detail})."
        )
        return f"""
        <html><body style="font-family: sans-serif; padding: 24px;">
        <h2>Spotify OAuth Complete</h2>
        <p>{persist_line}</p>
        <p>Restart PartyBox service to ensure environment and in-memory token are aligned:</p>
        <pre>sudo systemctl restart partybox.service</pre>
        </body></html>
        """
        
    # ---- Admin-only endpoints ----
    @app.get("/api/admin/health")
    def api_admin_health():
        _admin_or_403()
        now = time.time()
        ttl = float(os.getenv("PARTYBOX_HEALTH_CACHE_TTL_SECONDS", "4.0"))
        cached = HEALTH_CACHE.get("payload")
        if cached is not None and (now - float(HEALTH_CACHE.get("ts") or 0.0)) < ttl:
            return jsonify(cached), int(HEALTH_CACHE.get("status") or 503)

        status, payload = _build_admin_health()
        HEALTH_CACHE["ts"] = now
        HEALTH_CACHE["status"] = status
        HEALTH_CACHE["payload"] = payload
        return jsonify(payload), status

    @app.post("/api/admin/media_scan")
    def api_admin_media_scan():
        _admin_or_403()
        result = _sync_local_media()
        return jsonify({"ok": True, **result})

    @app.post("/api/admin/av_mode")
    def api_admin_av_mode():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        mode = (data.get("mode") or "").strip().lower()
        if mode not in ("partybox", "spotify"):
            return jsonify({"ok": False, "error": "bad mode"}), 400

        try:
            cmd = ["sudo", "-n", "/usr/local/bin/audio-mode", mode]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip()
            msg = f"audio-mode failed: {detail or e}"
            return jsonify({"ok": False, "error": msg}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": f"audio-mode failed: {e}"}), 500

        DB.set_setting("av_mode", mode)
        return jsonify({"ok": True, "mode": mode})

    @app.get("/api/tv/status")
    def api_tv_status():
        try:
            raw = DB.get_setting("tv_heartbeat_json") or ""
            hb_status = json.loads(raw) if raw else None
            av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()
            locked = _bool_setting("requests_locked", "0")
            paused = _bool_setting("tv_paused", "0")
            muted = _bool_setting("tv_muted", "0")
            tv_qr_enabled = _bool_setting("tv_qr_enabled", "1")

            # In Spotify mode, allow cached/live refreshes via SpotifyClient cache policy.
            spotify = _spotify_snapshot(force=False, allow_fetch=(av_mode == "spotify"))
            if av_mode != "spotify":
                spotify["state"] = "disabled"
                spotify["ok"] = True
                spotify["error"] = ""

            now = DB.get_now_playing()
            up = DB.peek_next()

            def _pack(qrow: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "queue_id": int(qrow["id"]),
                    "title": qrow["title"],
                    "youtube_id": qrow["youtube_id"],
                    "note_text": qrow.get("note_text") or "",
                }

            if paused:
                mode = "paused"
                now_out = _pack(now) if now else (_pack(up) if up else None)
                up_out = _pack(up) if (now and up) else None
            elif now:
                mode = "playing"
                now_out = _pack(now)
                up_out = _pack(up) if up else None
            elif up:
                mode = "queue"
                now_out = _pack(up)
                up_out = None
            else:
                mode = "empty"
                now_out = None
                up_out = None

            return jsonify(
                {
                    "ok": True,
                    "status": hb_status,
                    "state": {
                        "locked": locked,
                        "av_mode": av_mode,
                        "paused": paused,
                        "muted": muted,
                        "tv_qr_enabled": tv_qr_enabled,
                        "mode": mode,
                        "now": now_out,
                        "up_next": up_out,
                        "spotify": spotify,
                    },
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


    @app.post("/api/admin/catalog_add")
    def api_catalog_add():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        raw = (data.get("youtube") or data.get("youtube_id") or "").strip()
        if not raw:
            return jsonify({"ok": False, "error": "missing youtube url or id"}), 400

        media_dir = _media_dir()

        def _exists_in_media(fn: str) -> bool:
            if not fn or ".." in fn or fn.startswith("/"):
                return False
            p = os.path.abspath(os.path.join(media_dir, fn))
            return p.startswith(media_dir + os.sep) and os.path.isfile(p)

        # Local file mode
        if raw.startswith("file:") or raw.lower().endswith(".mp4"):
            filename = raw[5:].strip() if raw.startswith("file:") else raw
            if not _exists_in_media(filename):
                return jsonify({"ok": False, "error": f"local_file_not_found: {filename}"}), 400

            youtube_id = f"file:{filename}"
            title = (data.get("title") or "").strip() or os.path.splitext(filename)[0]
            DB.add_catalog_item(title, youtube_id)
            return jsonify({"ok": True, "title": title, "youtube_id": youtube_id})

        # YouTube mode (Option 1):
        # - If we can extract an ID from the URL, store the ID (clean + dedupe)
        # - If we cannot extract an ID (playlist/share edge cases), store the FULL URL
        #   and let mpv/ytdl handle it.
        youtube_token = raw
        extracted = _extract_youtube_id_from_url(raw)
        if extracted:
            youtube_token = extracted

        # Validate
        if "://" in youtube_token:
            # Must be a YouTube URL to accept as a direct target
            if not _is_youtube_url(youtube_token):
                return jsonify({"ok": False, "error": "bad youtube url"}), 400
        else:
            # Must be an ID-like token
            if not youtube_token or not YOUTUBE_ID_RE.match(youtube_token):
                return jsonify({"ok": False, "error": "bad youtube id"}), 400

        title = (data.get("title") or "").strip()
        if not title:
            title = _fetch_youtube_title(youtube_token) or ""
        if not title:
            title = f"YouTube {youtube_token}" if "://" not in youtube_token else "YouTube Video"

        DB.add_catalog_item(title, youtube_token)
        return jsonify({"ok": True, "title": title, "youtube_id": youtube_token})

    @app.post("/api/admin/catalog_enable")
    def api_catalog_enable():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        item_id = int(data.get("id", 0))
        enabled = bool(data.get("enabled", True))
        if item_id <= 0:
            return jsonify({"ok": False, "error": "bad id"}), 400
        DB.set_catalog_enabled(item_id, enabled)
        return jsonify({"ok": True})

    @app.post("/api/admin/catalog_delete")
    def api_catalog_delete():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        item_id = int(data.get("id", 0))
        if item_id <= 0:
            return jsonify({"ok": False, "error": "bad id"}), 400

        deleted = DB.delete_catalog_item(item_id)
        return jsonify({"ok": True, "deleted": deleted})

    @app.post("/api/admin/lock")
    def api_admin_lock():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        locked = bool(data.get("locked", False))
        DB.set_setting("requests_locked", "1" if locked else "0")
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_qr")
    def api_admin_tv_qr():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        enabled = bool(data.get("enabled", True))
        DB.set_setting("tv_qr_enabled", "1" if enabled else "0")
        return jsonify({"ok": True, "enabled": enabled})

    @app.post("/api/admin/skip")
    def api_admin_skip():
        _admin_or_403()
        skipped = DB.skip_current_or_next()
        return jsonify({"ok": True, "skipped": skipped})

    @app.post("/api/admin/clear_queue")
    def api_admin_clear_queue():
        _admin_or_403()
        DB.clear_queue()
        return jsonify({"ok": True})

    @app.post("/api/admin/queue_remove")
    def api_admin_queue_remove():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        qid = int(data.get("queue_id", 0))
        if qid <= 0:
            return jsonify({"ok": False, "error": "bad queue_id"}), 400
        DB.remove_from_queue(qid)
        return jsonify({"ok": True})

    @app.post("/api/admin/queue_promote")
    def api_admin_queue_promote():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        qid = int(data.get("queue_id", 0))
        if qid <= 0:
            return jsonify({"ok": False, "error": "bad queue_id"}), 400
        DB.promote_queue(qid)
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_pause")
    def api_admin_tv_pause():
        _admin_or_403()
        DB.set_setting("tv_paused", "1")
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_resume")
    def api_admin_tv_resume():
        _admin_or_403()
        DB.set_setting("tv_paused", "0")
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_stop")
    def api_admin_tv_stop():
        """
        "Stop" = pause TV AND clear queue (so it doesn't immediately start again).
        """
        _admin_or_403()
        DB.set_setting("tv_paused", "1")
        DB.clear_queue()
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_mute")
    def api_admin_tv_mute():
        _admin_or_403()
        DB.set_setting("tv_muted", "1")
        return jsonify({"ok": True})

    @app.post("/api/admin/tv_unmute")
    def api_admin_tv_unmute():
        _admin_or_403()
        DB.set_setting("tv_muted", "0")
        return jsonify({"ok": True})

    return app


def main() -> None:
    app = create_app()
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug)


if __name__ == "__main__":
    main()
