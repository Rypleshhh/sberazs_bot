import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import DB_PATH, MIN_OPERATIONS_COUNT

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
    notified_payment_at TEXT,
    tbank_json TEXT,
    gdebenz_json TEXT
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
    tbank_json TEXT,
    gdebenz_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fuel_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    status TEXT NOT NULL,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fuel_history_lookup
    ON fuel_history(api_id, fuel_type, scanned_at DESC, id DESC);
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

# Категории первой ступени, по которым можно фильтровать подписку.
FUEL_CATEGORIES = {
    "ai92": "petrol",
    "ai95": "petrol",
    "ai98": "petrol",
    "ai100": "petrol",
    "diesel": "diesel",
    "propane": "gas",
    "methane": "gas",
}

CATEGORY_LABELS = {
    "petrol": "⛽ Бензин",
    "diesel": "🚛 Дизель",
    "gas": "🔥 Газ",
    "all": "🌐 Всё",
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


def _last_history_status(conn, api_id: str, fuel_type: str) -> Optional[str]:
    """Статус из ПОСЛЕДНЕЙ (по времени) записи истории для этой станции и
    вида топлива — не важно, каким сканом (авто или ручным) она была
    создана. Это и есть база для сравнения 'было / стало'."""
    row = conn.execute(
        "SELECT status FROM fuel_history WHERE api_id = ? AND fuel_type = ? "
        "ORDER BY scanned_at DESC, id DESC LIMIT 1",
        (api_id, fuel_type),
    ).fetchone()
    return row["status"] if row else None


def get_available_since(api_id: str, fuel_type: str) -> Optional[str]:
    """С какого момента топливо доступно непрерывно (для тайм-лога) —
    идём по истории от новой записи к старой, пока статус 'available'."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT status, scanned_at FROM fuel_history WHERE api_id = ? AND fuel_type = ? "
            "ORDER BY scanned_at DESC, id DESC",
            (api_id, fuel_type),
        ).fetchall()
    since = None
    for r in rows:
        if r["status"] != "available":
            break
        since = r["scanned_at"]
    return since


def upsert_stations_and_get_updates(
    area: str, stations: dict, max_age_minutes: int,
    tbank_matches: Optional[dict] = None, gdebenz_matches: Optional[dict] = None,
) -> list[dict]:
    """
    Обновляет БД станциями из свежего скана этой области — не важно,
    автоматический это цикл планировщика или разовый /scan_city от
    пользователя: оба пишут в ОДНУ И ТУ ЖЕ историю (fuel_history), и
    детекция 'закончилось → появилось' всегда сравнивается с последней
    записью там, а не со снимком, который могла перезаписать другая
    сторона. Так ручной скан больше не ломает автоматический трекинг.

    tbank_matches / gdebenz_matches: {api_id: raw_station_dict} —
    необязательные вторичные подтверждения (по географическому матчу),
    sberazs остаётся главным источником, они только обогащают вывод.

    Возвращает список уведомлений о ВОЗВРАЩЕНИИ топлива.
    На первом скане области уведомления не генерируются (только
    наполняем базу/историю), чтобы не вываливать пользователю всю
    историю сразу.
    """
    first_scan = is_first_scan(area)
    updates_to_notify = []
    tbank_matches = tbank_matches or {}
    gdebenz_matches = gdebenz_matches or {}

    with closing(_connect()) as conn:
        for api_id, st in stations.items():
            row = flatten(api_id, area, st)
            row["tbank_json"] = json.dumps(tbank_matches.get(api_id), ensure_ascii=False) if tbank_matches.get(api_id) else None
            row["gdebenz_json"] = json.dumps(gdebenz_matches.get(api_id), ensure_ascii=False) if gdebenz_matches.get(api_id) else None

            # Меньше MIN_OPERATIONS_COUNT оплат за последнее время —
            # считаем данные шумом: не пишем в историю топлива вообще
            # (снимок в stations всё равно обновится — адрес/название/
            # оплата актуальны, только фактический статус топлива не
            # трогаем, чтобы следующее надёжное сравнение шло с
            # последним ДОСТОВЕРНЫМ значением, а не с шумом).
            reliable = (row["operations_count"] or 0) >= MIN_OPERATIONS_COUNT

            new_fuels = _fuel_map(row["fuels_json"])
            resumed = []

            if reliable:
                for ftype, fdata in new_fuels.items():
                    prev_status = _last_history_status(conn, api_id, ftype)
                    now_status = fdata.get("availabilityStatus", "unknown")

                    if not first_scan and now_status == "available" and prev_status != "available":
                        resumed.append({
                            "type": ftype,
                            "limitLiters": fdata.get("limitLiters"),
                            "category": FUEL_CATEGORIES.get(ftype, "other"),
                        })

                    conn.execute(
                        "INSERT INTO fuel_history (api_id, fuel_type, status, scanned_at) VALUES (?, ?, ?, ?)",
                        (api_id, ftype, now_status, datetime.now(timezone.utc).isoformat()),
                    )
            else:
                # Ненадёжный скан — снимок в stations ниже всё равно
                # обновится по остальным полям, но fuels_json/status
                # снимка оставляем прежними (см. ниже), в историю не пишем.
                existing_snapshot = conn.execute(
                    "SELECT fuels_json, availability_status FROM stations WHERE api_id = ?",
                    (api_id,),
                ).fetchone()
                if existing_snapshot:
                    row["fuels_json"] = existing_snapshot["fuels_json"]
                    row["availability_status"] = existing_snapshot["availability_status"]

            conn.execute(
                """
                INSERT INTO stations (api_id, area, name, address, lat, lon,
                                       availability_status, fuels_json, operations_count,
                                       last_payment_at, notified_payment_at, tbank_json, gdebenz_json)
                VALUES (:api_id, :area, :name, :address, :lat, :lon,
                        :availability_status, :fuels_json, :operations_count,
                        :last_payment_at, NULL, :tbank_json, :gdebenz_json)
                ON CONFLICT(api_id) DO UPDATE SET
                    area=:area, name=:name, address=:address, lat=:lat, lon=:lon,
                    availability_status=:availability_status, fuels_json=:fuels_json,
                    operations_count=:operations_count, last_payment_at=:last_payment_at,
                    tbank_json=:tbank_json, gdebenz_json=:gdebenz_json
                """,
                row,
            )

            if resumed and is_recent(row["last_payment_at"], max_age_minutes):
                notify_row = dict(row)
                notify_row["resumed_fuels_json"] = json.dumps(resumed, ensure_ascii=False)
                updates_to_notify.append(notify_row)
                conn.execute(
                    """
                    INSERT INTO pending_notifications
                        (area, name, address, resumed_fuels_json, operations_count, last_payment_at, tbank_json, gdebenz_json)
                    VALUES (:area, :name, :address, :resumed_fuels_json, :operations_count, :last_payment_at, :tbank_json, :gdebenz_json)
                    """,
                    notify_row,
                )

        conn.commit()

    if first_scan:
        mark_area_scanned(area)

    return updates_to_notify


def get_fuel_since_map(api_id: str, fuels_json: Optional[str]) -> dict:
    """Для каждого вида топлива, который сейчас available — с какого
    момента он непрерывно доступен (для тайм-лога в карточке)."""
    fuels = _fuel_map(fuels_json)
    result = {}
    for ftype, fdata in fuels.items():
        if fdata.get("availabilityStatus") == "available":
            since = get_available_since(api_id, ftype)
            if since:
                result[ftype] = since
    return result


def _has_available_fuel(fuels_json: Optional[str]) -> bool:
    fuels = _fuel_map(fuels_json)
    return any(f.get("availabilityStatus") == "available" for f in fuels.values())


def get_fresh_stations(area: str, max_age_minutes: int) -> list[dict]:
    """Станции области с оплатой не старше max_age_minutes, не менее
    MIN_OPERATIONS_COUNT недавних оплат И хотя бы одним видом топлива
    в наличии прямо сейчас (для /scan_city — выдаём только то, что
    реально есть, а не всё подряд)."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM stations WHERE area = ?", (area,)).fetchall()

    result = []
    for r in rows:
        if not is_recent(r["last_payment_at"], max_age_minutes):
            continue
        if (r["operations_count"] or 0) < MIN_OPERATIONS_COUNT:
            continue
        if not _has_available_fuel(r["fuels_json"]):
            continue

        d = dict(r)
        since_map = get_fuel_since_map(d["api_id"], d["fuels_json"])
        d["fuel_since_json"] = json.dumps(since_map, ensure_ascii=False)
        result.append(d)

    return result


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
