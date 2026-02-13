# partybox/app.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, redirect, url_for, send_from_directory, abort

from . import db as DB

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../static", template_folder="../templates")

    DB.init_db()
    DB.seed_if_empty()

    def _sync_local_media() -> Dict[str, Any]:
        """
        Scan data/media for .mp4 files and ensure each has a catalog entry:
          youtube_id = file:<filename>
          title      = filename sans extension
        """
        media_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))
        os.makedirs(media_dir, exist_ok=True)

        added = 0
        seen = 0
        for name in sorted(os.listdir(media_dir)):
            if not name.lower().endswith(".mp4"):
                continue
            if ".." in name or name.startswith("/"):
                continue

            seen += 1
            youtube_id = f"file:{name}"
            title = os.path.splitext(name)[0]
            if DB.upsert_catalog_item(title=title, youtube_id=youtube_id):
                added += 1

        return {"media_dir": media_dir, "seen_mp4": seen, "added": added}

    # optional auto scan on service start
    if os.getenv("PARTYBOX_AUTO_MEDIA_SCAN", "0") == "1":
        try:
            r = _sync_local_media()
            print(f"[partybox] media auto-scan ok: seen={r['seen_mp4']} added={r['added']} dir={r['media_dir']}", flush=True)
        except Exception as e:
            print(f"[partybox] media auto-scan FAILED: {e}", flush=True)

    @app.get("/")
    def index():
        return redirect(url_for("tv"))

    @app.get("/media/<path:filename>")
    def media(filename: str):
        # Configurable media directory (default: ../data/media)
        media_dir = os.getenv("PARTYBOX_MEDIA_DIR", "").strip()
        if media_dir:
            media_dir = os.path.abspath(media_dir)
        else:
            media_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))

        base = Path(media_dir)

        # Tight path safety: only allow a single file name (no subdirs)
        name = Path(filename).name
        if not name or name.startswith(".") or ".." in filename or filename.startswith("/"):
            abort(400)

        exact = base / name
        if exact.is_file():
            return send_from_directory(str(base), name, conditional=True)

        # Case-insensitive fallback
        try:
            target_lower = name.lower()
            for f in base.iterdir():
                if f.is_file() and f.name.lower() == target_lower:
                    return send_from_directory(str(base), f.name, conditional=True)
        except FileNotFoundError:
            pass

        abort(404)

    # ---------- Pages ----------
    @app.get("/tv")
    def tv():
        return render_template("tv.html")

    @app.get("/u")
    def user():
        items = DB.list_catalog(enabled_only=True)
        locked = DB.get_setting("requests_locked", "0") == "1"
        return render_template("user.html", items=items, locked=locked)

    @app.get("/admin")
    def admin():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return "nope", 403
        items = DB.list_catalog(enabled_only=False)
        locked = DB.get_setting("requests_locked", "0") == "1"
        return render_template("admin.html", items=items, locked=locked, key=key)

    # ---------- APIs ----------
    @app.get("/api/state")
    def api_state():
        locked = DB.get_setting("requests_locked", "0") == "1"

        nxt = DB.peek_next()
        if nxt:
            return jsonify(
                {
                    "locked": locked,
                    "mode": "queue",
                    "next": {
                        "queue_id": int(nxt["id"]),
                        "title": nxt["title"],
                        "youtube_id": nxt["youtube_id"],
                        "note_text": nxt.get("note_text") or "",
                    },
                }
            )

        idle = DB.pick_idle()
        if idle:
            return jsonify(
                {
                    "locked": locked,
                    "mode": "idle",
                    "next": {
                        "queue_id": None,
                        "title": idle["title"],
                        "youtube_id": idle["youtube_id"],
                        "note_text": "",
                    },
                }
            )

        return jsonify({"locked": locked, "mode": "empty", "next": None})

    @app.get("/api/queue")
    def api_queue():
        items = DB.list_queue(limit=25)
        return jsonify({"items": items})

    @app.post("/api/admin/media_scan")
    def api_admin_media_scan():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403

        result = _sync_local_media()
        return jsonify({"ok": True, **result})

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
        if DB.get_setting("requests_locked", "0") == "1":
            return jsonify({"ok": False, "error": "requests locked"}), 403

        data = request.get_json(force=True, silent=True) or {}
        catalog_id = int(data.get("catalog_id", 0))
        note_text = (data.get("note_text") or "").strip()

        if catalog_id <= 0:
            return jsonify({"ok": False, "error": "bad catalog_id"}), 400

        qid = DB.enqueue(catalog_id, note_text=note_text[:120] if note_text else None)
        return jsonify({"ok": True, "queue_id": qid})

    @app.post("/api/admin/catalog_add")
    def api_catalog_add():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403

        data = request.get_json(force=True, silent=True) or {}
        raw = (data.get("youtube") or data.get("youtube_id") or "").strip()
        if not raw:
            return jsonify({"ok": False, "error": "missing youtube url or id"}), 400

        # --- Local file mode ---
        media_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "media"))

        def _exists_in_media(fn: str) -> bool:
            if not fn or ".." in fn or fn.startswith("/"):
                return False
            p = os.path.abspath(os.path.join(media_dir, fn))
            return p.startswith(media_dir + os.sep) and os.path.isfile(p)

        if raw.startswith("file:") or raw.lower().endswith(".mp4"):
            filename = raw[5:].strip() if raw.startswith("file:") else raw
            if not _exists_in_media(filename):
                return jsonify({"ok": False, "error": f"local_file_not_found: {filename}"}), 400

            youtube_id = f"file:{filename}"
            title = (data.get("title") or "").strip() or os.path.splitext(filename)[0]
            DB.add_catalog_item(title, youtube_id)
            return jsonify({"ok": True, "title": title, "youtube_id": youtube_id})

        # --- YouTube mode (kept, but TV will ignore unless PARTYBOX_ALLOW_YOUTUBE=1) ---
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
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403

        data = request.get_json(force=True, silent=True) or {}
        item_id = int(data.get("id", 0))
        enabled = bool(data.get("enabled", True))
        if item_id <= 0:
            return jsonify({"ok": False, "error": "bad id"}), 400
        DB.set_catalog_enabled(item_id, enabled)
        return jsonify({"ok": True})

    @app.post("/api/admin/lock")
    def api_admin_lock():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403

        data = request.get_json(force=True, silent=True) or {}
        locked = bool(data.get("locked", False))
        DB.set_setting("requests_locked", "1" if locked else "0")
        return jsonify({"ok": True})

    @app.post("/api/admin/skip")
    def api_admin_skip():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        skipped = DB.skip_current_or_next()
        return jsonify({"ok": True, "skipped": skipped})

    @app.post("/api/admin/clear_queue")
    def api_admin_clear_queue():
        admin_key = DB.get_setting("admin_key", "JBOX")
        key = request.args.get("key", "")
        if key != admin_key:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        DB.clear_queue()
        return jsonify({"ok": True})

    return app


def main() -> None:
    app = create_app()
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug)


if __name__ == "__main__":
    main()
