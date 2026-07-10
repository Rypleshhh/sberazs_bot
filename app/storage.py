import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    api_id TEXT PRIMARY KEY,
    area TEXT NOT NULL,
    name TEXT,
    address TEXT,
    lat REAL,
    lon REAL,
    availability_status TEXT,
    fuels TEXT,
    last_payment_at TEXT,
    notified_payment_at TEXT
);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER NOT NULL,
    area TEXT NOT NULL,
    PRIMARY KEY (chat_id, area)
);

CREATE TABLE IF NOT EXISTS scanned_areas (
    area TEXT PRIMARY KEY,
    first_scan_done INTEGER NOT NULL DEFAULT 0
);
"""


def _connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def is_recent(last_payment_at: Optional[str], max_age_minutes: int) -> bool:
    if not last_payment_at:
        return False
    try:
        t = datetime.fromisoformat(last_payment_at)
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t.astimezone(timezone.utc)) <= timedelta(minutes=max_age_minutes)


def clean_address(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = " ".join(raw.split()).replace(" ,", ",")
    while ", ," in text or ",," in text:
        text = text.replace(", ,", ",").replace(",,", ",")
    return text.strip(", ").strip()


def flatten(api_id: str, area: str, st: dict) -> dict:
    location = st.get("location") or {}
    fuels = st.get("fuels") or []
    if fuels and isinstance(fuels[0], dict):
        fuels_str = "; ".join(f.get("name") or f.get("type") or str(f) for f in fuels)
    else:
        fuels_str = ", ".join(str(f) for f in fuels)

    return {
        "api_id": api_id,
        "area": area,
        "name": st.get("name"),
        "address": clean_address(st.get("address")),
        "lat": location.get("lat"),
        "lon": location.get("lon"),
        "availability_status": st.get("availabilityStatus"),
        "fuels": fuels_str,
        "last_payment_at": st.get("lastPaymentAt"),
    }


def is_first_scan(area: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT first_scan_done FROM scanned_areas WHERE area = ?", (area,)
        ).fetchone()
        return row is None or row["first_scan_done"] == 0


def mark_area_scanned(area: str):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO scanned_areas (area, first_scan_done) VALUES (?, 1) "
            "ON CONFLICT(area) DO UPDATE SET first_scan_done = 1",
            (area,),
        )
        conn.commit()


def upsert_stations_and_get_updates(area: str, stations: dict, max_age_minutes: int) -> list[dict]:
    """
    Обновляет БД станциями из свежего скана этой области.
    Возвращает список станций, по которым стоит УВЕДОМИТЬ подписчиков:
    lastPaymentAt изменился по сравнению с уже разосланным значением,
    и новая оплата достаточно свежая.
    На первом скане области уведомления не генерируются (только
    наполняем базу), чтобы не вываливать пользователю всю историю сразу.
    """
    first_scan = is_first_scan(area)
    updates_to_notify = []

    with closing(_connect()) as conn:
        for api_id, st in stations.items():
            row = flatten(api_id, area, st)
            existing = conn.execute(
                "SELECT last_payment_at, notified_payment_at FROM stations WHERE api_id = ?",
                (api_id,),
            ).fetchone()

            conn.execute(
                """
                INSERT INTO stations (api_id, area, name, address, lat, lon,
                                       availability_status, fuels, last_payment_at, notified_payment_at)
                VALUES (:api_id, :area, :name, :address, :lat, :lon,
                        :availability_status, :fuels, :last_payment_at, NULL)
                ON CONFLICT(api_id) DO UPDATE SET
                    area=:area, name=:name, address=:address, lat=:lat, lon=:lon,
                    availability_status=:availability_status, fuels=:fuels,
                    last_payment_at=:last_payment_at
                """,
                row,
            )

            if first_scan:
                continue

            new_payment = row["last_payment_at"]
            already_notified = existing["notified_payment_at"] if existing else None
            prev_payment = existing["last_payment_at"] if existing else None

            payment_changed = new_payment and new_payment != prev_payment
            not_yet_notified = new_payment != already_notified

            if payment_changed and not_yet_notified and is_recent(new_payment, max_age_minutes):
                updates_to_notify.append(row)
                conn.execute(
                    "UPDATE stations SET notified_payment_at = ? WHERE api_id = ?",
                    (new_payment, api_id),
                )

        conn.commit()

    if first_scan:
        mark_area_scanned(area)

    return updates_to_notify


def get_fresh_stations(area: str, max_age_minutes: int) -> list[dict]:
    """Все станции области с оплатой не старше max_age_minutes (для /scan)."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM stations WHERE area = ?", (area,)).fetchall()
    return [dict(r) for r in rows if is_recent(r["last_payment_at"], max_age_minutes)]


# --- подписчики ---

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
            conn.execute(
                "DELETE FROM subscribers WHERE chat_id = ? AND area = ?", (chat_id, area)
            )
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
        rows = conn.execute(
            "SELECT area FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchall()
    return [r["area"] for r in rows]
