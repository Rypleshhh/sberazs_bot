import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from app.config import BOT_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER NOT NULL,
    area TEXT NOT NULL,
    PRIMARY KEY (chat_id, area)
);

CREATE TABLE IF NOT EXISTS poll_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect():
    Path(BOT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BOT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_last_notification_id() -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT value FROM poll_state WHERE key = 'last_notification_id'"
        ).fetchone()
    return int(row["value"]) if row else 0


def set_last_notification_id(value: int):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO poll_state (key, value) VALUES ('last_notification_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (str(value), str(value)),
        )
        conn.commit()


def add_subscriber(chat_id: int, area: str):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers (chat_id, area) VALUES (?, ?)",
            (chat_id, area),
        )
        conn.commit()


def remove_subscriber(chat_id: int, area: Optional[str] = None):
    with closing(_connect()) as conn:
        if area is None:
            conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        else:
            conn.execute("DELETE FROM subscribers WHERE chat_id = ? AND area = ?", (chat_id, area))
        conn.commit()


def get_subscribers_for_area(area: str) -> list[int]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM subscribers WHERE area = ? OR area = 'all'",
            (area,),
        ).fetchall()
    return [r["chat_id"] for r in rows]


def get_subscriptions(chat_id: int) -> list[str]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT area FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchall()
    return [r["area"] for r in rows]
