# partybox/tv_player.py
# =============================================================================
# PartyBox TV Player (mpv)
#
# - Polls the local Flask API (/api/state) for the next item (queue or idle)
# - Plays YouTube videos fullscreen via mpv using yt-dlp
# - Marks queue items playing/done via /api/tv/mark_playing and /api/tv/mark_done
#
# Notes:
# - This avoids YouTube iframe "embed blocked" + autoplay/sound restrictions.
# - Designed to run on the TV box (Pi now, NUC later).
# =============================================================================

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

API_BASE = os.getenv("PARTYBOX_API", "http://127.0.0.1:5000")
POLL_SECONDS = float(os.getenv("PARTYBOX_POLL_SECONDS", "1.0"))
MPV_BIN = os.getenv("MPV_BIN", "mpv")

# mpv args: fullscreen, stay on top, no OSD spam
MPV_ARGS = [
    "--fs",
    "--ontop",
    "--no-border",
    "--cursor-autohide=always",
    "--force-window=yes",
    "--keep-open=no",
    "--really-quiet",
    # Better A/V stability on small devices
    "--hwdec=auto-safe",
    # Helps when yt-dlp is used under the hood by mpv
    "--ytdl=yes",
    "--ytdl-format=bestvideo+bestaudio/best",
]


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


def youtube_watch_url(youtube_id: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_id}"


def mark_playing(queue_id: int) -> None:
    http_json(f"{API_BASE}/api/tv/mark_playing", method="POST", payload={"queue_id": queue_id})


def mark_done(queue_id: int) -> None:
    http_json(f"{API_BASE}/api/tv/mark_done", method="POST", payload={"queue_id": queue_id})


def get_state() -> Dict[str, Any]:
    return http_json(f"{API_BASE}/api/state")


def pick_next(state: Dict[str, Any]) -> Tuple[str, Optional[int], Optional[str], Optional[str]]:
    """
    Returns (mode, queue_id, youtube_id, title)
    mode in {"queue","idle","empty"}
    """
    mode = state.get("mode") or "empty"
    nxt = state.get("next") or None
    if not nxt:
        return ("empty", None, None, None)
    qid = nxt.get("queue_id", None)
    yid = nxt.get("youtube_id", None)
    title = nxt.get("title", None)
    return (mode, int(qid) if qid else None, str(yid) if yid else None, str(title) if title else None)


def play_with_mpv(url: str) -> int:
    cmd = [MPV_BIN, *MPV_ARGS, url]
    # mpv return code is not always meaningful; we treat "finished" as done either way
    proc = subprocess.run(cmd)
    return int(proc.returncode)


def main() -> None:
    last_key = None  # (mode, queue_id, youtube_id)
    while True:
        try:
            state = get_state()
            mode, queue_id, youtube_id, title = pick_next(state)

            key = (mode, queue_id, youtube_id)
            if mode == "empty" or not youtube_id:
                last_key = None
                time.sleep(POLL_SECONDS)
                continue

            # If the same item is still "next", don't restart playback.
            if key == last_key:
                time.sleep(POLL_SECONDS)
                continue

            last_key = key
            url = youtube_watch_url(youtube_id)

            if mode == "queue" and queue_id:
                try:
                    mark_playing(queue_id)
                except Exception:
                    pass

            print(f"[tv_player] playing: {title or youtube_id} ({mode})")
            rc = play_with_mpv(url)
            print(f"[tv_player] mpv exit rc={rc}")

            if mode == "queue" and queue_id:
                try:
                    mark_done(queue_id)
                except Exception:
                    pass

            # short breathe so we don't thrash
            time.sleep(0.2)

        except Exception as e:
            print(f"[tv_player] error: {e}")
            time.sleep(1.0)


if __name__ == "__main__":
    main()
