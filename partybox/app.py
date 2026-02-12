# partybox/app.py
from __future__ import annotations

import os
import re
from flask import Flask, jsonify, render_template, request, redirect, url_for

from . import db as DB

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../static", template_folder="../templates")

    DB.init_db()
    DB.seed_if_empty()

    @app.get("/")
    def index():
        return redirect(url_for("tv"))

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
        # TV polls this every 2s to know what to play next
        locked = DB.get_setting("requests_locked", "0") == "1"
        nxt = DB.peek_next()
        if not nxt:
            return jsonify({"locked": locked, "next": None})
        return jsonify(
            {
                "locked": locked,
                "next": {
                    "queue_id": int(nxt["id"]),
                    "title": nxt["title"],
                    "youtube_id": nxt["youtube_id"],
                    "note_text": nxt["note_text"] or "",
                },
            }
        )

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
        title = (data.get("title") or "").strip()
        youtube_id = (data.get("youtube_id") or "").strip()

        if not title or not youtube_id or not YOUTUBE_ID_RE.match(youtube_id):
            return jsonify({"ok": False, "error": "bad title or youtube_id"}), 400

        DB.add_catalog_item(title, youtube_id)
        return jsonify({"ok": True})

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
    # Pi POC: bind on LAN
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)


if __name__ == "__main__":
    main()
