# partybox/tv_player.py
# =============================================================================
# PartyBox TV Player (mpv)
#
# - Polls the local Flask API (/api/state) for what to play
# - Plays local media (file:*.mp4) and YouTube (id or URL) via mpv/yt-dlp
# - Marks queue items playing/done via /api/tv/mark_playing and /api/tv/mark_done
#
# RULES:
# - If media_mode!=partybox -> do nothing (leave HDMI audio to selected input mode)
# - If paused=true     -> do nothing (TV status page can show PAUSED)
# - Only queue items get mark_playing/mark_done.
# - If paused/spotify flips ON while mpv is running -> stop mpv.
#
# HEARTBEAT:
# - Posts /api/tv/heartbeat every few seconds so Admin can show if TV agent is alive.
#
# IMPORTANT:
# - Force mpv to use SDL video output to avoid DRM/KMS permission issues in kiosk setups.
# - Prefer ALSA audio to avoid PulseAudio "stream is suspended" behavior.
# =============================================================================

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

API_BASE = os.getenv("PARTYBOX_API", "http://127.0.0.1:5000").rstrip("/")
POLL_SECONDS = float(os.getenv("PARTYBOX_POLL_SECONDS", "1.0"))
HB_EVERY = float(os.getenv("PARTYBOX_HEARTBEAT_SECONDS", "3.0"))

# Prefer PARTYBOX_MPV_BIN, but also accept MPV_BIN for backward compatibility
MPV_BIN = (os.getenv("PARTYBOX_MPV_BIN") or os.getenv("MPV_BIN") or "mpv").strip()

DEFAULT_MEDIA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))
MEDIA_DIR = os.path.abspath(os.getenv("PARTYBOX_MEDIA_DIR", DEFAULT_MEDIA_DIR))

# Optional ALSA device name (example: "plughw:PCH,0" or "default")
# If blank, mpv will use ALSA default.
ALSA_DEVICE = (os.getenv("PARTYBOX_ALSA_DEVICE") or "").strip()

EXTRA_MPV_ARGS = shlex.split(os.getenv("PARTYBOX_MPV_ARGS", "").strip()) if os.getenv("PARTYBOX_MPV_ARGS") else []

# Force kiosk-safe output:
# - --vo=sdl prevents DRM/KMS "permission denied" failures
# - --ao=alsa avoids PulseAudio "stream is suspended"
BASE_MPV_ARGS = [
    "--fs",
    "--no-border",
    "--ontop",
    "--cursor-autohide=always",
    "--force-window=yes",
    "--keep-open=no",
    "--really-quiet",
    "--hwdec=auto-safe",
    "--vo=sdl",
    "--ao=alsa",
]

YTDL_ARGS_ON = [
    "--ytdl=yes",
    "--ytdl-format=bestvideo+bestaudio/best",
]

YTDL_ARGS_OFF = [
    "--ytdl=no",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(url: str, method: str = "GET", payload: Optional[dict] = None) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=6) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)


def post_heartbeat(mode: str, title: str = "", youtube_id: str = "") -> None:
    try:
        http_json(
            f"{API_BASE}/api/tv/heartbeat",
            method="POST",
            payload={
                "mode": mode,
                "title": (title or "")[:120],
                "youtube_id": (youtube_id or "")[:200],
            },
        )
    except Exception:
        pass


def mark_playing(queue_id: int) -> None:
    http_json(f"{API_BASE}/api/tv/mark_playing", method="POST", payload={"queue_id": queue_id})


def mark_done(queue_id: int) -> None:
    http_json(f"{API_BASE}/api/tv/mark_done", method="POST", payload={"queue_id": queue_id})


def get_state() -> Dict[str, Any]:
    return http_json(f"{API_BASE}/api/state")


def is_local_token(youtube_id: str) -> bool:
    return (youtube_id or "").startswith("file:")


def local_path_from_token(youtube_id: str) -> Optional[str]:
    name = (youtube_id or "")[5:].strip()
    if not name:
        return None

    safe_name = Path(name).name
    if safe_name != name:
        return None

    p = os.path.abspath(os.path.join(MEDIA_DIR, safe_name))
    if not p.startswith(MEDIA_DIR + os.sep):
        return None
    if not os.path.isfile(p):
        return None
    return p


def youtube_or_url(youtube_id: str) -> str:
    y = (youtube_id or "").strip()
    if not y:
        return ""
    if "://" in y:
        return y
    return f"https://www.youtube.com/watch?v={y}"


def pick_item_from_state(state: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[str], str]:
    media_mode = (state.get("media_mode") or "").lower()
    if not media_mode:
        av_mode = (state.get("av_mode") or "partybox").lower()
        media_mode = "spotify" if av_mode == "spotify" else "partybox"
    if media_mode != "partybox":
        return (None, None, None, media_mode)

    if bool(state.get("paused", False)):
        return (None, None, None, "paused")

    now = state.get("now") or None
    if not now:
        return (None, None, None, "empty")

    youtube_id = now.get("youtube_id") or ""
    title = now.get("title") or ""
    mode = str(state.get("mode") or "unknown")

    qid = now.get("queue_id", None)
    queue_id: Optional[int] = None
    try:
        if qid is not None:
            queue_id = int(qid)
    except Exception:
        queue_id = None

    return (str(youtube_id), queue_id, str(title), mode)


def build_mpv_cmd(target: str, use_ytdl: bool) -> list[str]:
    ytdl_args = YTDL_ARGS_ON if use_ytdl else YTDL_ARGS_OFF
    args = [MPV_BIN, *BASE_MPV_ARGS, *ytdl_args, *EXTRA_MPV_ARGS]

    # If user wants a specific ALSA device, set it explicitly
    if ALSA_DEVICE:
        args += [f"--audio-device=alsa/{ALSA_DEVICE}"]

    args += [target]
    return args


def start_mpv(target: str, use_ytdl: bool) -> subprocess.Popen:
    cmd = build_mpv_cmd(target, use_ytdl=use_ytdl)
    log(f"[tv_player] mpv cmd: {' '.join(shlex.quote(x) for x in cmd)}")

    # Capture mpv stderr/stdout to a file so we can see why it exits (if it exits fast)
    log_path = os.getenv("PARTYBOX_MPV_LOG", "/tmp/partybox-mpv.log")
    try:
        f = open(log_path, "ab", buffering=0)  # noqa: SIM115
    except Exception:
        f = None

    return subprocess.Popen(cmd, stdout=f or subprocess.DEVNULL, stderr=f or subprocess.DEVNULL)


def stop_mpv(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:
        return
    for _ in range(20):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        proc.kill()
    except Exception:
        pass


def main() -> None:
    log(f"[tv_player] starting. API_BASE={API_BASE} MEDIA_DIR={MEDIA_DIR} MPV_BIN={MPV_BIN}")

    proc: Optional[subprocess.Popen] = None
    cur_youtube_id: Optional[str] = None
    cur_queue_id: Optional[int] = None
    cur_title: str = ""
    cur_mode: str = "unknown"
    cur_is_queue_item: bool = False
    complete_current_on_exit: bool = True

    last_mode: Optional[str] = None
    pending_key: Optional[Tuple[str, Optional[int]]] = None
    pending_seen: int = 0  # require >=2 before starting

    hb_last = 0.0

    while True:
        try:
            # If mpv is running, do NOT chase API "now" changes.
            if proc is not None and proc.poll() is None:
                now_ts = time.time()
                if now_ts - hb_last >= HB_EVERY:
                    hb_last = now_ts
                    post_heartbeat(mode="playing", title=cur_title, youtube_id=cur_youtube_id or "")

                # Stop mpv if paused/spotify flips on, or if "now" moved to another
                # queue item (skip/remove/clear/promote effects).
                try:
                    s2 = get_state()
                    s2_media_mode = str(s2.get("media_mode") or "").lower()
                    if not s2_media_mode:
                        s2_media_mode = "spotify" if str(s2.get("av_mode") or "").lower() == "spotify" else "partybox"
                    paused_or_spotify = bool(s2.get("paused", False)) or (s2_media_mode != "partybox")

                    state_now = s2.get("now") or None
                    state_qid: Optional[int] = None
                    if state_now is not None and state_now.get("queue_id") is not None:
                        try:
                            state_qid = int(state_now.get("queue_id"))
                        except Exception:
                            state_qid = None

                    queue_item_replaced = bool(
                        cur_is_queue_item
                        and cur_queue_id is not None
                        and state_qid != cur_queue_id
                    )

                    if paused_or_spotify or queue_item_replaced:
                        complete_current_on_exit = False
                        if paused_or_spotify:
                            log("[tv_player] stopping mpv due to paused/spotify flip")
                        else:
                            log("[tv_player] stopping mpv because current queue item changed")
                        stop_mpv(proc)
                except Exception:
                    pass

                time.sleep(0.5)
                continue

            # If we had a proc and it ended, finalize it
            if proc is not None:
                rc = proc.poll()
                if rc is None:
                    stop_mpv(proc)
                    rc = proc.poll()
                log(f"[tv_player] mpv exit rc={int(rc) if rc is not None else -1}")

                if complete_current_on_exit and cur_is_queue_item and cur_queue_id is not None:
                    try:
                        mark_done(cur_queue_id)
                    except Exception as e:
                        log(f"[tv_player] warn: mark_done failed: {e}")

                proc = None
                cur_youtube_id = None
                cur_queue_id = None
                cur_title = ""
                cur_mode = "unknown"
                cur_is_queue_item = False
                complete_current_on_exit = True
                time.sleep(0.2)

            state = get_state()
            youtube_id, queue_id, title, mode = pick_item_from_state(state)

            now_ts = time.time()
            if now_ts - hb_last >= HB_EVERY:
                hb_last = now_ts
                post_heartbeat(mode=mode, title=title or "", youtube_id=youtube_id or "")

            if mode != last_mode:
                last_mode = mode
                log(
                    f"[tv_player] state: mode={mode} media_mode={state.get('media_mode')} "
                    f"av_mode={state.get('av_mode')} paused={state.get('paused')}"
                )

            if not youtube_id:
                pending_key = None
                pending_seen = 0
                time.sleep(POLL_SECONDS)
                continue

            key = (youtube_id, queue_id)
            if key != pending_key:
                pending_key = key
                pending_seen = 1
                time.sleep(POLL_SECONDS)
                continue
            pending_seen += 1

            if pending_seen < 2:
                time.sleep(POLL_SECONDS)
                continue

            if is_local_token(youtube_id):
                local_path = local_path_from_token(youtube_id)
                if not local_path:
                    log(f"[tv_player] local missing/invalid: {youtube_id}")
                    time.sleep(0.5)
                    continue
                target = local_path
                use_ytdl = False
            else:
                target = youtube_or_url(youtube_id)
                if not target:
                    time.sleep(0.5)
                    continue
                use_ytdl = True

            is_queue_item = (queue_id is not None and queue_id > 0 and mode in ("queue", "playing"))

            if is_queue_item and queue_id is not None:
                try:
                    mark_playing(queue_id)
                except Exception as e:
                    log(f"[tv_player] warn: mark_playing failed: {e}")

            cur_youtube_id = youtube_id
            cur_queue_id = queue_id
            cur_title = title or youtube_id
            cur_mode = mode
            cur_is_queue_item = is_queue_item
            complete_current_on_exit = True

            log(f"[tv_player] playing: {cur_title} ({mode})")
            proc = start_mpv(target, use_ytdl=use_ytdl)

            pending_key = None
            pending_seen = 0

        except Exception as e:
            log(f"[tv_player] error: {e}")
            time.sleep(1.0)


if __name__ == "__main__":
    main()
