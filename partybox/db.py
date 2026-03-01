# partybox/db.py
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("PARTYBOX_DB_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "partybox.db")))


def _now() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              youtube_id TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_ts INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              catalog_id INTEGER NOT NULL,
              note_text TEXT,
              requested_by TEXT DEFAULT '',
              request_device TEXT DEFAULT '',
              status TEXT NOT NULL DEFAULT 'queued',   -- queued|playing|done|skipped|canceled
              pos INTEGER NOT NULL DEFAULT 0,          -- ordering key
              created_ts INTEGER NOT NULL DEFAULT 0,
              started_ts INTEGER,
              ended_ts INTEGER,
              FOREIGN KEY(catalog_id) REFERENCES catalog(id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS play_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              mode TEXT NOT NULL,
              track_id TEXT NOT NULL,
              title TEXT DEFAULT '',
              artist TEXT DEFAULT '',
              uri TEXT DEFAULT '',
              duration_ms INTEGER DEFAULT 0,
              started_by TEXT DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_mode_track ON play_events(mode, track_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_ts ON play_events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_mode_ts ON play_events(mode, ts)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS play_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              mode TEXT NOT NULL,
              actor TEXT DEFAULT '',
              title TEXT DEFAULT '',
              artist TEXT DEFAULT '',
              album TEXT DEFAULT '',
              provider_id TEXT DEFAULT '',
              uri TEXT DEFAULT '',
              extra_json TEXT DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_history_ts ON play_history(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_history_mode_ts ON play_history(mode, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_play_history_mode_provider ON play_history(mode, provider_id)")

        # Backfill / migrate older DBs safely
        if not _col_exists(conn, "queue", "status"):
            conn.execute("ALTER TABLE queue ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'")
        if not _col_exists(conn, "queue", "requested_by"):
            conn.execute("ALTER TABLE queue ADD COLUMN requested_by TEXT DEFAULT ''")
        if not _col_exists(conn, "queue", "request_device"):
            conn.execute("ALTER TABLE queue ADD COLUMN request_device TEXT DEFAULT ''")
        if not _col_exists(conn, "queue", "pos"):
            conn.execute("ALTER TABLE queue ADD COLUMN pos INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, "queue", "created_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN created_ts INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, "queue", "started_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN started_ts INTEGER")
        if not _col_exists(conn, "queue", "ended_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN ended_ts INTEGER")
        if not _col_exists(conn, "play_history", "actor"):
            conn.execute("ALTER TABLE play_history ADD COLUMN actor TEXT DEFAULT ''")

        # One-time compatibility backfill from older play_events storage.
        conn.execute(
            """
            INSERT INTO play_history(ts, mode, actor, title, artist, album, provider_id, uri, extra_json)
            SELECT pe.ts, pe.mode, pe.started_by, pe.title, pe.artist, '', pe.track_id, pe.uri, ''
            FROM play_events pe
            WHERE NOT EXISTS (SELECT 1 FROM play_history LIMIT 1)
            """
        )

        # Ensure settings defaults using the same transaction/connection.
        def _setting_get(key: str, default: Optional[str] = None) -> Optional[str]:
            row = conn.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
            return str(row["v"]) if row else default

        def _setting_set_default(key: str, value: str) -> None:
            conn.execute(
                "INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO NOTHING",
                (key, value),
            )

        _setting_set_default("admin_key", "JBOX")
        _setting_set_default("requests_locked", "0")
        _setting_set_default("tv_paused", "0")
        if _setting_get("media_mode", None) is None:
            legacy_av = (_setting_get("av_mode", "partybox") or "partybox").strip().lower()
            default_media_mode = "spotify" if legacy_av == "spotify" else "partybox"
            _setting_set_default("media_mode", default_media_mode)

        conn.commit()


def seed_if_empty() -> None:
    # keep it minimal; your real catalog comes from media scan / admin add
    pass


def get_setting(k: str, default: Optional[str] = None) -> Optional[str]:
    with _connect() as conn:
        r = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default


def set_setting(k: str, v: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        conn.commit()


# ---------------- Catalog ----------------

def list_catalog(enabled_only: bool = True) -> List[Dict[str, Any]]:
    q = "SELECT id, title, youtube_id, enabled FROM catalog"
    args: List[Any] = []
    if enabled_only:
        q += " WHERE enabled=1"
    q += " ORDER BY id DESC"
    with _connect() as conn:
        rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]


def add_catalog_item(title: str, youtube_id: str) -> int:
    with _connect() as conn:
        ts = _now()
        cur = conn.execute(
            "INSERT INTO catalog(title, youtube_id, enabled, created_ts) VALUES(?,?,1,?)",
            (title, youtube_id, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def upsert_catalog_item(title: str, youtube_id: str) -> bool:
    """
    Returns True if inserted new row, False if already existed.
    """
    with _connect() as conn:
        ts = _now()
        r = conn.execute("SELECT id FROM catalog WHERE youtube_id=?", (youtube_id,)).fetchone()
        if r:
            return False
        conn.execute(
            "INSERT INTO catalog(title, youtube_id, enabled, created_ts) VALUES(?,?,1,?)",
            (title, youtube_id, ts),
        )
        conn.commit()
        return True


def set_catalog_enabled(item_id: int, enabled: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE catalog SET enabled=? WHERE id=?", (1 if enabled else 0, item_id))
        conn.commit()


def delete_catalog_item(item_id: int) -> int:
    """
    Hard-delete a catalog item and remove any queue entries referencing it.
    Returns number of catalog rows deleted (0 or 1).
    """
    with _connect() as conn:
        conn.execute("DELETE FROM queue WHERE catalog_id=?", (item_id,))
        cur = conn.execute("DELETE FROM catalog WHERE id=?", (item_id,))
        conn.commit()
    # Normalize queue positions after deletions.
    normalize_queue_positions()
    return int(cur.rowcount)


# ---------------- Queue ----------------

def _next_pos(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(pos), 0) AS m FROM queue WHERE status IN ('queued','playing')").fetchone()
    return int(r["m"]) + 1


def enqueue(
    catalog_id: int,
    note_text: Optional[str] = None,
    requested_by: str = "",
    request_device: str = "",
) -> int:
    with _connect() as conn:
        ts = _now()
        pos = _next_pos(conn)
        cur = conn.execute(
            "INSERT INTO queue(catalog_id, note_text, requested_by, request_device, status, pos, created_ts) VALUES(?,?,?,?,?,?,?)",
            (catalog_id, note_text, (requested_by or "")[:120], (request_device or "")[:120], "queued", pos, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_queue(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Return queue items in order, including catalog fields.
    Compatible with older schemas (doesn't require started_ts/ended_ts).
    """
    limit = int(limit or 50)
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
              q.id, q.status, q.pos, q.note_text, q.requested_by, q.request_device,
              c.id AS catalog_id, c.title, c.youtube_id
            FROM queue q
            JOIN catalog c ON c.id = q.catalog_id
            WHERE q.status IN ('queued', 'playing')
            ORDER BY q.pos ASC, q.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def queue_depth() -> int:
    with _connect() as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS c FROM queue WHERE status IN ('queued','playing')"
        ).fetchone()
        return int(r["c"] or 0) if r else 0


def get_queue_item(queue_id: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT
              q.id, q.status, q.pos, q.note_text, q.requested_by, q.request_device,
              c.id AS catalog_id, c.title, c.youtube_id
            FROM queue q
            JOIN catalog c ON c.id = q.catalog_id
            WHERE q.id = ?
            LIMIT 1
            """,
            (int(queue_id),),
        ).fetchone()
        return dict(r) if r else None


def normalize_queue_positions() -> None:
    """
    Ensure queue.pos is a clean 1..N sequence (stable ordering by pos,id).
    Safe to call after inserts/deletes/promotions.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM queue
            WHERE status IN ('queued', 'playing')
            ORDER BY pos ASC, id ASC
            """
        ).fetchall()

        pos = 1
        for r in rows:
            conn.execute("UPDATE queue SET pos=? WHERE id=?", (pos, int(r["id"])))
            pos += 1

        conn.commit()


def get_now_playing() -> Optional[Dict[str, Any]]:
    """
    Return the queue item currently marked as playing, or None.

    NOTE: We intentionally do NOT depend on started_ts / ended_ts columns so we
    remain compatible with older DB versions.
    """
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT
              q.id, q.status, q.pos, q.note_text, q.requested_by, q.request_device,
              c.title, c.youtube_id
            FROM queue q
            JOIN catalog c ON c.id = q.catalog_id
            WHERE q.status = 'playing'
            ORDER BY q.pos ASC, q.id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(r) if r else None




def peek_next() -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT q.id, q.status, q.pos, q.note_text,
                   q.requested_by, q.request_device,
                   c.title, c.youtube_id
            FROM queue q
            JOIN catalog c ON c.id=q.catalog_id
            WHERE q.status='queued'
            ORDER BY q.pos ASC, q.id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(r) if r else None



def pick_idle() -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT id, title, youtube_id
            FROM catalog
            WHERE enabled=1
            ORDER BY RANDOM()
            LIMIT 1
            """
        ).fetchone()
        return dict(r) if r else None


def mark_playing(queue_id: int) -> None:
    with _connect() as conn:
        ts = _now()
        # move to front if needed: set pos to (min pos - 1)
        rmin = conn.execute("SELECT COALESCE(MIN(pos), 0) AS m FROM queue WHERE status IN ('queued','playing')").fetchone()
        min_pos = int(rmin["m"])
        conn.execute(
            "UPDATE queue SET status='playing', started_ts=?, pos=? WHERE id=?",
            (ts, min_pos - 1, queue_id),
        )
        # any other playing -> leave as playing? we only want one playing
        conn.execute("UPDATE queue SET status='queued' WHERE status='playing' AND id<>?", (queue_id,))
        conn.commit()


def mark_done(queue_id: int) -> None:
    with _connect() as conn:
        ts = _now()
        conn.execute("UPDATE queue SET status='done', ended_ts=? WHERE id=?", (ts, queue_id))
        conn.commit()


def remove_from_queue(queue_id: int) -> None:
    with _connect() as conn:
        ts = _now()
        conn.execute("UPDATE queue SET status='canceled', ended_ts=? WHERE id=?", (ts, queue_id))
        conn.commit()


def promote_queue(queue_id: int) -> None:
    """
    Move a queued item to the top of the queued list (but not above the playing item).
    """
    with _connect() as conn:
        rmin = conn.execute("SELECT COALESCE(MIN(pos), 0) AS m FROM queue WHERE status='queued'").fetchone()
        min_pos = int(rmin["m"])
        conn.execute("UPDATE queue SET pos=? WHERE id=? AND status='queued'", (min_pos - 1, queue_id))
        conn.commit()


def clear_queue() -> None:
    with _connect() as conn:
        ts = _now()
        conn.execute("UPDATE queue SET status='canceled', ended_ts=? WHERE status IN ('queued','playing')", (ts,))
        conn.commit()


def skip_current_or_next() -> int:
    """
    If something is playing, cancel it. Else cancel the next queued item.
    Returns number of rows affected.
    """
    with _connect() as conn:
        ts = _now()
        r = conn.execute("SELECT id FROM queue WHERE status='playing' ORDER BY started_ts DESC LIMIT 1").fetchone()
        if r:
            cur = conn.execute("UPDATE queue SET status='skipped', ended_ts=? WHERE id=?", (ts, int(r["id"])))
            conn.commit()
            return int(cur.rowcount)

        r2 = conn.execute("SELECT id FROM queue WHERE status='queued' ORDER BY pos ASC, id ASC LIMIT 1").fetchone()
        if r2:
            cur = conn.execute("UPDATE queue SET status='skipped', ended_ts=? WHERE id=?", (ts, int(r2["id"])))
            conn.commit()
            return int(cur.rowcount)

        return 0


# ---------------- Play events ----------------

def add_play_event(
    mode: str,
    track_id: str,
    title: str = "",
    artist: str = "",
    uri: str = "",
    duration_ms: int = 0,
    started_by: str = "",
    ts: Optional[int] = None,
) -> int:
    mode_val = (mode or "").strip().lower()
    if mode_val not in ("partybox", "spotify"):
        raise ValueError("mode must be 'partybox' or 'spotify'")
    track_val = (track_id or "").strip()
    if not track_val:
        raise ValueError("track_id is required")
    ts_val = int(ts if ts is not None else _now())
    duration_val = max(0, int(duration_ms or 0))
    # Keep unified play history in sync for admin/reporting views.
    add_play_history_event(
        ts=ts_val,
        mode=mode_val,
        actor=started_by,
        title=title,
        artist=artist,
        album="",
        provider_id=track_val,
        uri=uri,
        extra_json="",
    )
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO play_events(ts, mode, track_id, title, artist, uri, duration_ms, started_by)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                ts_val,
                mode_val,
                track_val[:128],
                (title or "")[:200],
                (artist or "")[:200],
                (uri or "")[:512],
                duration_val,
                (started_by or "")[:80],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def add_play_history_event(
    mode: str,
    actor: str = "",
    title: str = "",
    artist: str = "",
    album: str = "",
    provider_id: str = "",
    uri: str = "",
    extra_json: str = "",
    ts: Optional[int] = None,
) -> int:
    mode_val = (mode or "").strip().lower()
    if not mode_val:
        mode_val = "unknown"
    ts_val = int(ts if ts is not None else _now())
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO play_history(ts, mode, actor, title, artist, album, provider_id, uri, extra_json)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                ts_val,
                mode_val[:24],
                (actor or "")[:120],
                (title or "")[:200],
                (artist or "")[:200],
                (album or "")[:200],
                (provider_id or "")[:200],
                (uri or "")[:512],
                (extra_json or "")[:2000],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_play_history(limit: int = 25) -> List[Dict[str, Any]]:
    lim = max(1, min(200, int(limit or 25)))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
              id,
              ts,
              mode,
              actor,
              title,
              artist,
              album,
              provider_id,
              uri,
              extra_json
            FROM play_history
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_played(mode: str, limit: int = 25, window_days: Optional[int] = None) -> List[Dict[str, Any]]:
    mode_val = (mode or "").strip().lower()
    if mode_val not in ("partybox", "spotify"):
        return []
    lim = max(1, min(100, int(limit or 25)))
    args: List[Any] = [mode_val]
    time_filter = ""
    if window_days is not None:
        days = int(window_days or 0)
        if days > 0:
            cutoff = _now() - (days * 86400)
            time_filter = " AND ts >= ?"
            args.append(cutoff)
    args.append(lim)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
              provider_id AS track_id,
              MAX(title) AS title,
              MAX(artist) AS artist,
              COUNT(*) AS plays
            FROM play_history
            WHERE mode = ?
              AND provider_id != ''
              {time_filter}
            GROUP BY provider_id
            ORDER BY plays DESC, MAX(ts) DESC
            LIMIT ?
            """,
            args,
        ).fetchall()
        return [dict(r) for r in rows]
