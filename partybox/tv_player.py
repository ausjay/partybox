# partybox/tv_player.py
# =============================================================================
# PartyBox TV Player (mpv)
#
# - Polls the local Flask API (/api/state) for what to play
# - Plays local media (file:*.mp4) and YouTube (id or URL) via mpv/yt-dlp
# - Marks queue items playing/done via /api/tv/mark_playing and /api/tv/mark_done
#
# RULES:
# - If av_mode=spotify -> do nothing (leave HDMI alone; Spotify Connect only)
# - If paused=true     -> do nothing (TV status page can show PAUSED)
# - Only queue items get mark_playing/mark_done.
# - If paused/spotify flips ON while mpv is running -> stop mpv.
#
# HEARTBEAT:
# - Posts /api/tv/heartbeat every few seconds so Admin can show if TV agent is alive.
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

MPV_BIN = os.getenv("MPV_BIN", "mpv")

DEFAULT_MEDIA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))
MEDIA_DIR = os.path.abspath(os.getenv("PARTYBOX_MEDIA_DIR", DEFAULT_MEDIA_DIR))

EXTRA_MPV_ARGS = shlex.split(os.getenv("PARTYBOX_MPV_ARGS", "").strip()) if os.getenv("PARTYBOX_MPV_ARGS") else []

BASE_MPV_ARGS = [
    "--fs",
    "--no-border",
    "--ontop",
    "--cursor-autohide=always",
    "--force-window=yes",
    "--keep-open=no",
    "--really-quiet",
    "--hwdec=auto-safe",
    "--ytdl=yes",
    "--ytdl-format=bestvideo+bestaudio/best",
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
                "title": title[:120],
                "youtube_id": youtube_id[:200],
            },
        )
    except Exception:
        # heartbeat should never kill playback loop
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
    av_mode = (state.get("av_mode") or "partybox").lower()
    if av_mode == "spotify":
        return (None, None, None, "spotify")

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


def start_mpv(target: str) -> subprocess.Popen:
    cmd = [MPV_BIN, *BASE_MPV_ARGS, *EXTRA_MPV_ARGS, target]
    return subprocess.Popen(cmd)


def stop_mpv(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:
        return
    for _ in range(10):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        proc.kill()
    except Exception:
        pass


def main() -> None:
    last_key: Optional[Tuple[str, Optional[int]]] = None
    last_mode: Optional[str] = None

    hb_last = 0.0
    HB_EVERY = float(os.getenv("PARTYBOX_HEARTBEAT_SECONDS", "3.0"))

    log(f"[tv_player] starting. API_BASE={API_BASE} MEDIA_DIR={MEDIA_DIR}")

    while True:
        try:
            state = get_state()
            youtube_id, queue_id, title, mode = pick_item_from_state(state)

            # Heartbeat (always)
            now_ts = time.time()
            if now_ts - hb_last >= HB_EVERY:
                hb_last = now_ts
                post_heartbeat(mode=mode, title=title or "", youtube_id=youtube_id or "")

            # Log mode transitions (so service isn't "silent")
            if mode != last_mode:
                last_mode = mode
                log(f"[tv_player] state: mode={mode} av_mode={state.get('av_mode')} paused={state.get('paused')}")

            if not youtube_id:
                last_key = None
                time.sleep(POLL_SECONDS)
                continue

            key = (youtube_id, queue_id)
            if key == last_key:
                time.sleep(POLL_SECONDS)
                continue
            last_key = key

            # Determine mpv target
            if is_local_token(youtube_id):
                local_path = local_path_from_token(youtube_id)
                if not local_path:
                    log(f"[tv_player] local missing/invalid: {youtube_id}")
                    time.sleep(0.5)
                    continue
                target = local_path
            else:
                target = youtube_or_url(youtube_id)
                if not target:
                    time.sleep(0.5)
                    continue

            is_queue_item = (queue_id is not None and queue_id > 0 and mode in ("queue", "playing"))

            if is_queue_item:
                try:
                    mark_playing(queue_id)
                except Exception as e:
                    log(f"[tv_player] warn: mark_playing failed: {e}")

            log(f"[tv_player] playing: {title or youtube_id} ({mode})")
            proc = start_mpv(target)

            while proc.poll() is None:
                time.sleep(0.5)

                # Keep heartbeat alive during playback too
                now_ts = time.time()
                if now_ts - hb_last >= HB_EVERY:
                    hb_last = now_ts
                    post_heartbeat(mode="playing", title=title or "", youtube_id=youtube_id or "")

                try:
                    s2 = get_state()
                    if bool(s2.get("paused", False)) or (str(s2.get("av_mode") or "").lower() == "spotify"):
                        log("[tv_player] stopping mpv due to paused/spotify flip")
                        stop_mpv(proc)
                        break
                except Exception:
                    pass

            rc = proc.poll()
            if rc is None:
                stop_mpv(proc)
                rc = proc.poll()

            log(f"[tv_player] mpv exit rc={int(rc) if rc is not None else -1}")

            if is_queue_item:
                try:
                    mark_done(queue_id)  # type: ignore[arg-type]
                except Exception as e:
                    log(f"[tv_player] warn: mark_done failed: {e}")

            time.sleep(0.2)

        except Exception as e:
            log(f"[tv_player] error: {e}")
            time.sleep(1.0)


if __name__ == "__main__":
    main()
