"""
Клиент для gdebenz.ru — краудсорсинговый сервис (отметки водителей,
не платежи). Используется как ТРЕТИЧНЫЙ, самый слабый по надёжности
источник: просто ещё одно мнение рядом с sberazs (главный) и T-Bank
(вторичный, тоже платёжный). sberazs как был, так и остаётся
единственным триггером уведомлений.
"""

import logging
import math

import aiohttp

log = logging.getLogger(__name__)

GDEBENZ_API_URL = "https://gdebenz.ru/api/stations"

# Максимальное расстояние (метры) для матчинга станций между источниками.
MATCH_DISTANCE_M = 80

# Статусы gdebenz — со слов пользователей, не проверяются сервисом.
GDEBENZ_STATUS_LABELS = {
    "no": "не работает",
    "low": "мало топлива / долго",
    "queue": "очередь",
    "yes": "работает",
    "available": "работает",
}


async def fetch_gdebenz_stations(bbox: tuple[float, float, float, float]) -> list[dict]:
    """bbox: (min_lon, min_lat, max_lon, max_lat) — как у sberazs/T-Bank."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "lat1": min_lat, "lon1": min_lon,
        "lat2": max_lat, "lon2": max_lon,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GDEBENZ_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return data.get("stations", [])
    except Exception:
        log.exception("Не удалось получить данные gdebenz для bbox=%s", bbox)
        return []


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_gdebenz_match(sber_lat: float, sber_lon: float, gdebenz_stations: list[dict]) -> dict | None:
    if sber_lat is None or sber_lon is None:
        return None

    best = None
    best_dist = MATCH_DISTANCE_M
    for g in gdebenz_stations:
        g_lat, g_lon = g.get("lat"), g.get("lon")
        if g_lat is None or g_lon is None:
            continue
        dist = _haversine_m(sber_lat, sber_lon, g_lat, g_lon)
        if dist < best_dist:
            best = g
            best_dist = dist
    return best
