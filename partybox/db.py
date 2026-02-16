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
              status TEXT NOT NULL DEFAULT 'queued',   -- queued|playing|done|skipped|canceled
              pos INTEGER NOT NULL DEFAULT 0,          -- ordering key
              created_ts INTEGER NOT NULL DEFAULT 0,
              started_ts INTEGER,
              ended_ts INTEGER,
              FOREIGN KEY(catalog_id) REFERENCES catalog(id)
            )
            """
        )

        # Backfill / migrate older DBs safely
        if not _col_exists(conn, "queue", "status"):
            conn.execute("ALTER TABLE queue ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'")
        if not _col_exists(conn, "queue", "pos"):
            conn.execute("ALTER TABLE queue ADD COLUMN pos INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, "queue", "created_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN created_ts INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, "queue", "started_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN started_ts INTEGER")
        if not _col_exists(conn, "queue", "ended_ts"):
            conn.execute("ALTER TABLE queue ADD COLUMN ended_ts INTEGER")

        # Ensure settings defaults
        if get_setting("admin_key", None) is None:
            set_setting("admin_key", "JBOX")
        if get_setting("requests_locked", None) is None:
            set_setting("requests_locked", "0")
        if get_setting("tv_paused", None) is None:
            set_setting("tv_paused", "0")

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


# ---------------- Queue ----------------

def _next_pos(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(pos), 0) AS m FROM queue WHERE status IN ('queued','playing')").fetchone()
    return int(r["m"]) + 1


def enqueue(catalog_id: int, note_text: Optional[str] = None) -> int:
    with _connect() as conn:
        ts = _now()
        pos = _next_pos(conn)
        cur = conn.execute(
            "INSERT INTO queue(catalog_id, note_text, status, pos, created_ts) VALUES(?,?,?,?,?)",
            (catalog_id, note_text, "queued", pos, ts),
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
              q.id, q.status, q.pos, q.note_text,
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
              q.id, q.status, q.pos, q.note_text,
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
    with _connect() as conn:
        rmin = conn.execute("SELECT COALESCE(MIN(pos), 0) AS m FROM queue WHERE status IN ('queued','playing')").fetchone()
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
