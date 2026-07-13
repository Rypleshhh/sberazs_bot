import json
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
    fuels_json TEXT,
    operations_count INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS pending_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,
    name TEXT,
    address TEXT,
    resumed_fuels_json TEXT,
    operations_count INTEGER DEFAULT 0,
    last_payment_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Человекочитаемые названия видов топлива.
FUEL_LABELS = {
    "ai92": "АИ-92",
    "ai95": "АИ-95",
    "ai98": "АИ-98",
    "ai100": "АИ-100",
    "diesel": "Дизель",
    "propane": "Газ (пропан)",
    "methane": "Газ (метан)",
}

# Как показываем статус конкретного вида топлива.
FUEL_STATUS_LABELS = {
    "available": "Топливо есть",
    "stale": "Возможно есть",
    "unknown": "Нет данных",
}


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


def _fuel_map(fuels_json: Optional[str]) -> dict:
    """{'ai92': {...}, 'ai95': {...}, ...} по type из сырого списка fuels."""
    if not fuels_json:
        return {}
    try:
        fuels = json.loads(fuels_json)
    except (TypeError, ValueError):
        return {}
    return {f.get("type"): f for f in fuels if isinstance(f, dict) and f.get("type")}


def flatten(api_id: str, area: str, st: dict) -> dict:
    location = st.get("location") or {}
    fuels_raw = st.get("fuels") or []

    return {
        "api_id": api_id,
        "area": area,
        "name": st.get("name"),
        "address": clean_address(st.get("address")),
        "lat": location.get("lat"),
        "lon": location.get("lon"),
        "availability_status": st.get("availabilityStatus"),
        "fuels_json": json.dumps(fuels_raw, ensure_ascii=False),
        "operations_count": st.get("operationsCount") or 0,
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
    Возвращает список уведомлений о ВОЗВРАЩЕНИИ топлива: для каждой
    станции сравнивает предыдущий и новый статус по каждому виду
    топлива, и если какой-то вид перешёл из "не available" в
    "available" — это и есть момент "бензин закончился, потом снова
    появился", который нужно поймать.
    На первом скане области уведомления не генерируются (только
    наполняем базу), чтобы не вываливать пользователю всю историю сразу.
    """
    first_scan = is_first_scan(area)
    updates_to_notify = []

    with closing(_connect()) as conn:
        for api_id, st in stations.items():
            row = flatten(api_id, area, st)
            existing = conn.execute(
                "SELECT fuels_json, last_payment_at FROM stations WHERE api_id = ?",
                (api_id,),
            ).fetchone()

            conn.execute(
                """
                INSERT INTO stations (api_id, area, name, address, lat, lon,
                                       availability_status, fuels_json, operations_count,
                                       last_payment_at, notified_payment_at)
                VALUES (:api_id, :area, :name, :address, :lat, :lon,
                        :availability_status, :fuels_json, :operations_count,
                        :last_payment_at, NULL)
                ON CONFLICT(api_id) DO UPDATE SET
                    area=:area, name=:name, address=:address, lat=:lat, lon=:lon,
                    availability_status=:availability_status, fuels_json=:fuels_json,
                    operations_count=:operations_count, last_payment_at=:last_payment_at
                """,
                row,
            )

            if first_scan:
                continue

            old_fuels = _fuel_map(existing["fuels_json"] if existing else None)
            new_fuels = _fuel_map(row["fuels_json"])

            resumed = []
            for ftype, fdata in new_fuels.items():
                was_available = old_fuels.get(ftype, {}).get("availabilityStatus") == "available"
                now_available = fdata.get("availabilityStatus") == "available"
                if now_available and not was_available:
                    resumed.append({"type": ftype, "limitLiters": fdata.get("limitLiters")})

            if resumed and is_recent(row["last_payment_at"], max_age_minutes):
                notify_row = dict(row)
                notify_row["resumed_fuels_json"] = json.dumps(resumed, ensure_ascii=False)
                updates_to_notify.append(notify_row)
                conn.execute(
                    """
                    INSERT INTO pending_notifications
                        (area, name, address, resumed_fuels_json, operations_count, last_payment_at)
                    VALUES (:area, :name, :address, :resumed_fuels_json, :operations_count, :last_payment_at)
                    """,
                    notify_row,
                )

        conn.commit()

    if first_scan:
        mark_area_scanned(area)

    return updates_to_notify


def get_fresh_stations(area: str, max_age_minutes: int) -> list[dict]:
    """Все станции области с оплатой не старше max_age_minutes (для /scan_city)."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM stations WHERE area = ?", (area,)).fetchall()
    return [dict(r) for r in rows if is_recent(r["last_payment_at"], max_age_minutes)]


def get_notifications_after(after_id: int) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM pending_notifications WHERE id > ? ORDER BY id ASC",
            (after_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_areas() -> list[str]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT DISTINCT area FROM stations").fetchall()
    return [r["area"] for r in rows]


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
