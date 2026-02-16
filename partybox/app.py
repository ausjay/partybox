# partybox/app.py
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import db as DB

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


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
    Scan data/media for .mp4 files and ensure each has a catalog entry:
      youtube_id = file:<filename>
      title      = filename sans extension (unless already exists in DB)

    ALSO:
      - disable catalog entries whose local file is missing
      - remove queued items whose local file is missing
    """
    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    base = Path(media_dir)

    added = 0
    seen = 0

    # Track which local tokens exist on disk
    existing_tokens = set()

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
        "disabled_missing": disabled,
        "queue_removed_missing": removed_from_queue,
    }


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../static", template_folder="../templates")

    DB.init_db()
    DB.seed_if_empty()

    # Ensure defaults
    if DB.get_setting("tv_muted", None) is None:
        DB.set_setting("tv_muted", "0")
    if DB.get_setting("av_mode", None) is None:
        DB.set_setting("av_mode", "partybox")

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

    # ---------- Pages ----------
    @app.get("/")
    def index():
        return redirect(url_for("tv"))

    @app.get("/tv")
    def tv():
        return render_template("tv.html")

    @app.get("/u")
    def user():
        items = DB.list_catalog(enabled_only=True)
        q = DB.list_queue(limit=50)
        locked = DB.get_setting("requests_locked", "0") == "1"
        return render_template("user.html", items=items, locked=locked, queue=q)

    @app.get("/admin")
    def admin():
        key = _admin_or_403()
        items = DB.list_catalog(enabled_only=False)
        q = DB.list_queue(limit=50)
        locked = DB.get_setting("requests_locked", "0") == "1"
        paused = DB.get_setting("tv_paused", "0") == "1"
        muted = DB.get_setting("tv_muted", "0") == "1"
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").strip().lower()

        return render_template(
            "admin.html",
            items=items,
            locked=locked,
            paused=paused,
            muted=muted,
            queue=q,
            key=key,
            av_mode=av_mode,
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
        locked = DB.get_setting("requests_locked", "0") == "1"
        paused = DB.get_setting("tv_paused", "0") == "1"
        muted = DB.get_setting("tv_muted", "0") == "1"
        av_mode = (DB.get_setting("av_mode", "partybox") or "partybox").lower()

        now = DB.get_now_playing()
        up = DB.peek_next()

        # IMPORTANT: When paused, do NOT rotate through idle picks.
        # Only show: currently-playing item, or next queued item, else nothing.
        if paused:
            if now:
                return jsonify(
                    {
                        "locked": locked,
                        "paused": True,
                        "muted": muted,
                        "av_mode": av_mode,
                        "mode": "paused",
                        "now": {
                            "queue_id": int(now["id"]),
                            "title": now["title"],
                            "youtube_id": now["youtube_id"],
                            "note_text": now.get("note_text") or "",
                        },
                        "up_next": (
                            {
                                "queue_id": int(up["id"]),
                                "title": up["title"],
                                "youtube_id": up["youtube_id"],
                                "note_text": up.get("note_text") or "",
                            }
                            if up
                            else None
                        ),
                    }
                )

            if up:
                return jsonify(
                    {
                        "locked": locked,
                        "paused": True,
                        "muted": muted,
                        "av_mode": av_mode,
                        "mode": "paused",
                        "now": {
                            "queue_id": int(up["id"]),
                            "title": up["title"],
                            "youtube_id": up["youtube_id"],
                            "note_text": up.get("note_text") or "",
                        },
                        "up_next": None,
                    }
                )

            return jsonify(
                {
                    "locked": locked,
                    "paused": True,
                    "muted": muted,
                    "av_mode": av_mode,
                    "mode": "paused",
                    "now": None,
                    "up_next": None,
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
                    "mode": "playing",
                    "now": {
                        "queue_id": int(now["id"]),
                        "title": now["title"],
                        "youtube_id": now["youtube_id"],
                        "note_text": now.get("note_text") or "",
                    },
                    "up_next": (
                        {
                            "queue_id": int(up["id"]),
                            "title": up["title"],
                            "youtube_id": up["youtube_id"],
                            "note_text": up.get("note_text") or "",
                        }
                        if up
                        else None
                    ),
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
                    "mode": "queue",
                    "now": {
                        "queue_id": int(up["id"]),
                        "title": up["title"],
                        "youtube_id": up["youtube_id"],
                        "note_text": up.get("note_text") or "",
                    },
                    "up_next": None,
                }
            )

        idle = DB.pick_idle()
        if idle:
            return jsonify(
                {
                    "locked": locked,
                    "paused": False,
                    "muted": muted,
                    "av_mode": av_mode,
                    "mode": "idle",
                    "now": {
                        "queue_id": None,
                        "title": idle["title"],
                        "youtube_id": idle["youtube_id"],
                        "note_text": "",
                    },
                    "up_next": None,
                }
            )

        return jsonify(
            {
                "locked": locked,
                "paused": False,
                "muted": muted,
                "av_mode": av_mode,
                "mode": "empty",
                "now": None,
                "up_next": None,
            }
        )


    @app.get("/api/queue")
    def api_queue():
        items = DB.list_queue(limit=100)
        return jsonify({"items": items})

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

        if DB.get_setting("requests_locked", "0") == "1":
            return jsonify({"ok": False, "error": "requests locked"}), 403

        data = request.get_json(force=True, silent=True) or {}
        catalog_id = int(data.get("catalog_id", 0))
        note_text = (data.get("note_text") or "").strip()

        if catalog_id <= 0:
            return jsonify({"ok": False, "error": "bad catalog_id"}), 400

        qid = DB.enqueue(catalog_id, note_text=note_text[:120] if note_text else None)
        return jsonify({"ok": True, "queue_id": qid})

    # ---- Admin-only endpoints ----
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
            subprocess.run(["/usr/local/bin/audio-mode", mode], check=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"audio-mode failed: {e}"}), 500

        DB.set_setting("av_mode", mode)
        return jsonify({"ok": True, "mode": mode})

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

        # YouTube mode (legacy)
        youtube_id = raw
        if "youtube.com" in raw or "youtu.be" in raw:
            m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", raw)
            if not m:
                m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", raw)
            if not m:
                m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", raw)
            if m:
                youtube_id = m.group(1)

        if not youtube_id or not YOUTUBE_ID_RE.match(youtube_id):
            return jsonify({"ok": False, "error": "bad youtube id"}), 400

        title = (data.get("title") or "").strip() or f"YouTube {youtube_id}"
        DB.add_catalog_item(title, youtube_id)
        return jsonify({"ok": True, "title": title, "youtube_id": youtube_id})

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

    @app.post("/api/admin/lock")
    def api_admin_lock():
        _admin_or_403()

        data = request.get_json(force=True, silent=True) or {}
        locked = bool(data.get("locked", False))
        DB.set_setting("requests_locked", "1" if locked else "0")
        return jsonify({"ok": True})

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

