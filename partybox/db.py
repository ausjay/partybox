# partybox/db.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "partybox.db")


def db_path() -> str:
    return os.getenv("PARTYBOX_DB", DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    p = db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init_db() -> None:
    con = connect()
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            youtube_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','playing','done','skipped')) DEFAULT 'queued',
            requested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            started_at INTEGER,
            finished_at INTEGER,
            note_text TEXT,
            note_photo_path TEXT,
            FOREIGN KEY(catalog_id) REFERENCES catalog(id) ON DELETE CASCADE
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    # Defaults
    cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('requests_locked','0');")
    cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('admin_key','JBOX');")

    con.commit()
    con.close()


def get_setting(key: str, default: str = "") -> str:
    con = connect()
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    con = connect()
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()
    con.close()


def seed_if_empty() -> None:
    con = connect()
    row = con.execute("SELECT COUNT(*) AS n FROM catalog").fetchone()
    if row and int(row["n"]) == 0:
        samples = [
            ("Jamiroquai - Virtual Insanity", "4JkIs37a2JE"),
            ("Daft Punk - Around the World", "LKYPYj2XX80"),
            ("Johnny Cash - Hurt", "8AHCfZTRGiI"),
            ("Outkast - Hey Ya!", "PWgvGjAhvIw"),
        ]
        con.executemany("INSERT INTO catalog(title,youtube_id,enabled) VALUES(?,?,1)", samples)
        con.commit()
    con.close()


def list_catalog(enabled_only: bool = True) -> List[sqlite3.Row]:
    con = connect()
    if enabled_only:
        rows = con.execute("SELECT * FROM catalog WHERE enabled=1 ORDER BY title COLLATE NOCASE").fetchall()
    else:
        rows = con.execute("SELECT * FROM catalog ORDER BY enabled DESC, title COLLATE NOCASE").fetchall()
    con.close()
    return rows


def add_catalog_item(title: str, youtube_id: str) -> None:
    con = connect()
    con.execute("INSERT INTO catalog(title,youtube_id,enabled) VALUES(?,?,1)", (title.strip(), youtube_id.strip()))
    con.commit()
    con.close()


def set_catalog_enabled(item_id: int, enabled: bool) -> None:
    con = connect()
    con.execute("UPDATE catalog SET enabled=? WHERE id=?", (1 if enabled else 0, item_id))
    con.commit()
    con.close()


def enqueue(catalog_id: int, note_text: Optional[str] = None) -> int:
    con = connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO queue(catalog_id,status,note_text) VALUES(?, 'queued', ?)",
        (catalog_id, note_text.strip() if note_text else None),
    )
    con.commit()
    qid = int(cur.lastrowid)
    con.close()
    return qid


def peek_next() -> Optional[sqlite3.Row]:
    con = connect()
    row = con.execute(
        """
        SELECT q.*, c.title, c.youtube_id
        FROM queue q
        JOIN catalog c ON c.id=q.catalog_id
        WHERE q.status='queued' AND c.enabled=1
        ORDER BY q.requested_at ASC, q.id ASC
        LIMIT 1
        """
    ).fetchone()
    con.close()
    return row


def mark_playing(queue_id: int) -> None:
    con = connect()
    con.execute(
        "UPDATE queue SET status='playing', started_at=strftime('%s','now') WHERE id=?",
        (queue_id,),
    )
    con.commit()
    con.close()


def mark_done(queue_id: int) -> None:
    con = connect()
    con.execute(
        "UPDATE queue SET status='done', finished_at=strftime('%s','now') WHERE id=?",
        (queue_id,),
    )
    con.commit()
    con.close()


def skip_current_or_next() -> Optional[int]:
    # If something is playing, skip it; else skip next queued.
    con = connect()
    row = con.execute("SELECT id FROM queue WHERE status='playing' ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
    if row:
        qid = int(row["id"])
        con.execute("UPDATE queue SET status='skipped', finished_at=strftime('%s','now') WHERE id=?", (qid,))
        con.commit()
        con.close()
        return qid

    row2 = con.execute("SELECT id FROM queue WHERE status='queued' ORDER BY requested_at ASC, id ASC LIMIT 1").fetchone()
    if row2:
        qid = int(row2["id"])
        con.execute("UPDATE queue SET status='skipped', finished_at=strftime('%s','now') WHERE id=?", (qid,))
        con.commit()
        con.close()
        return qid

    con.close()
    return None


def clear_queue() -> None:
    con = connect()
    con.execute("DELETE FROM queue WHERE status IN ('queued','playing')")
    con.commit()
    con.close()

def pick_idle() -> Optional[sqlite3.Row]:
    con = connect()
    rows = con.execute(
        "SELECT id, title, youtube_id FROM catalog WHERE enabled=1 ORDER BY title COLLATE NOCASE"
    ).fetchall()
    con.close()
    if not rows:
        return None

    import time
    idx = int(time.time() // 90) % len(rows)  # rotate every 90s
    return rows[idx]

