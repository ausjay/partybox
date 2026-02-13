# partybox/db.py
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "partybox.db"

_DB_PATH = Path(os.getenv("PARTYBOX_DB", str(DEFAULT_DB))).expanduser().resolve()


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog (
              id        INTEGER PRIMARY KEY AUTOINCREMENT,
              title     TEXT NOT NULL,
              youtube_id TEXT NOT NULL UNIQUE,
              enabled   INTEGER NOT NULL DEFAULT 1,
              created_ts INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS queue (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              catalog_id INTEGER NOT NULL,
              note_text  TEXT,
              status     TEXT NOT NULL DEFAULT 'queued', -- queued|playing|done
              created_ts INTEGER NOT NULL DEFAULT (strftime('%s','now')),
              playing_ts INTEGER,
              done_ts    INTEGER,
              FOREIGN KEY(catalog_id) REFERENCES catalog(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_queue_status_created
              ON queue(status, created_ts);

            CREATE INDEX IF NOT EXISTS idx_catalog_enabled
              ON catalog(enabled);
            """
        )


def seed_if_empty() -> None:
    with _connect() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM settings")
        if int(cur.fetchone()["n"]) == 0:
            conn.execute("INSERT INTO settings(key,value) VALUES('admin_key','JBOX')")
            conn.execute("INSERT INTO settings(key,value) VALUES('requests_locked','0')")
            conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def list_catalog(enabled_only: bool = True) -> List[Dict[str, Any]]:
    with _connect() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT id,title,youtube_id,enabled FROM catalog WHERE enabled=1 ORDER BY title COLLATE NOCASE"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,title,youtube_id,enabled FROM catalog ORDER BY title COLLATE NOCASE"
            ).fetchall()
    return [dict(r) for r in rows]


def add_catalog_item(title: str, youtube_id: str) -> None:
    title = (title or "").strip() or youtube_id
    youtube_id = (youtube_id or "").strip()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO catalog(title,youtube_id,enabled) VALUES(?,?,1)",
            (title, youtube_id),
        )
        conn.commit()


def upsert_catalog_item(title: str, youtube_id: str) -> bool:
    """
    Returns True if a new row was added.
    If the item already exists, we DO NOT overwrite title/enabled.
    """
    title = (title or "").strip() or youtube_id
    youtube_id = (youtube_id or "").strip()
    with _connect() as conn:
        cur = conn.execute("SELECT id FROM catalog WHERE youtube_id=?", (youtube_id,))
        row = cur.fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO catalog(title,youtube_id,enabled) VALUES(?,?,1)",
            (title, youtube_id),
        )
        conn.commit()
        return True


def set_catalog_enabled(item_id: int, enabled: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE catalog SET enabled=? WHERE id=?", (1 if enabled else 0, int(item_id)))
        conn.commit()


def enqueue(catalog_id: int, note_text: Optional[str] = None) -> int:
    note_text = (note_text or "").strip() or None
    if note_text:
        note_text = note_text[:120]
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO queue(catalog_id,note_text,status,created_ts) VALUES(?,?, 'queued', ?)",
            (int(catalog_id), note_text, int(time.time())),
        )
        conn.commit()
        return int(cur.lastrowid)


def _peek_next_row(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    # If something is already "playing", keep returning it so TV is stable across refreshes.
    row = conn.execute(
        """
        SELECT q.id, q.catalog_id, q.note_text, q.status, c.title, c.youtube_id
        FROM queue q
        JOIN catalog c ON c.id = q.catalog_id
        WHERE q.status IN ('playing','queued')
          AND c.enabled=1
        ORDER BY
          CASE q.status WHEN 'playing' THEN 0 ELSE 1 END,
          q.created_ts ASC,
          q.id ASC
        LIMIT 1
        """
    ).fetchone()
    return row


def peek_next() -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = _peek_next_row(conn)
        return dict(row) if row else None


def mark_playing(queue_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE queue
            SET status='playing', playing_ts=?
            WHERE id=? AND status='queued'
            """,
            (int(time.time()), int(queue_id)),
        )
        conn.commit()


def mark_done(queue_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE queue
            SET status='done', done_ts=?
            WHERE id=? AND status IN ('queued','playing')
            """,
            (int(time.time()), int(queue_id)),
        )
        conn.commit()


def clear_queue() -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE queue SET status='done', done_ts=? WHERE status IN ('queued','playing')",
            (int(time.time()),),
        )
        conn.commit()


def skip_current_or_next() -> bool:
    with _connect() as conn:
        row = _peek_next_row(conn)
        if not row:
            return False
        conn.execute(
            "UPDATE queue SET status='done', done_ts=? WHERE id=?",
            (int(time.time()), int(row["id"])),
        )
        conn.commit()
        return True


def list_queue(limit: int = 20) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT q.id, q.status, q.note_text, q.created_ts, c.title, c.youtube_id
            FROM queue q
            JOIN catalog c ON c.id = q.catalog_id
            WHERE q.status IN ('queued','playing')
            ORDER BY
              CASE q.status WHEN 'playing' THEN 0 ELSE 1 END,
              q.created_ts ASC,
              q.id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def pick_idle() -> Optional[Dict[str, Any]]:
    """
    Pick an enabled catalog item for idle mode.
    Default: LOCAL FILES ONLY (youtube_id like 'file:%') so TV never hangs on YT items.
    Set PARTYBOX_ALLOW_YOUTUBE=1 if you want to include YT ids in idle rotation.
    """
    allow_youtube = os.getenv("PARTYBOX_ALLOW_YOUTUBE", "0") == "1"
    with _connect() as conn:
        if allow_youtube:
            row = conn.execute(
                """
                SELECT id,title,youtube_id
                FROM catalog
                WHERE enabled=1
                ORDER BY RANDOM()
                LIMIT 1
                """
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id,title,youtube_id
                FROM catalog
                WHERE enabled=1 AND youtube_id LIKE 'file:%'
                ORDER BY RANDOM()
                LIMIT 1
                """
            ).fetchone()
    return dict(row) if row else None
