"""
Клиент для toplivo.tbank.ru — запрос полностью открытый, антибот не
нужен (в отличие от sberazs.ru). Используется как ВТОРИЧНЫЙ источник:
подтверждает/дополняет данные sberazs, но не заменяет их — sberazs
остаётся главным (именно он триггерит уведомления о возврате топлива).
"""

import logging
import math

import aiohttp

log = logging.getLogger(__name__)

TBANK_API_URL = "https://toplivo.tbank.ru/api/v1/stations"

# Максимальное расстояние (метры), при котором считаем станцию sberazs
# и станцию T-Bank одной и той же заправкой.
MATCH_DISTANCE_M = 80

# Числовые ключи T-Bank -> канонические ключи топлива (как у sberazs).
TBANK_FUEL_TYPE_MAP = {
    "92": "ai92",
    "95": "ai95",
    "98": "ai98",
    "100": "ai100",
    "dt": "diesel",
    "diesel": "diesel",
}


async def fetch_tbank_stations(bbox: tuple[float, float, float, float]) -> list[dict]:
    """bbox: (min_lon, min_lat, max_lon, max_lat) — как у sberazs."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "minLat": min_lat, "maxLat": max_lat,
        "minLon": min_lon, "maxLon": max_lon,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TBANK_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return data.get("payload", [])
    except Exception:
        log.exception("Не удалось получить данные T-Bank для bbox=%s", bbox)
        return []


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_tbank_index(tbank_stations: list[dict]) -> list[dict]:
    """Просто возвращает список как есть — индекс не строим, т.к.
    станций в одном bbox обычно немного (десятки-сотни), полный перебор
    достаточно быстрый и не требует доп. зависимостей вроде scipy."""
    return tbank_stations


def find_tbank_match(sber_lat: float, sber_lon: float, tbank_stations: list[dict]) -> dict | None:
    """Ищет ближайшую станцию T-Bank в пределах MATCH_DISTANCE_M."""
    if sber_lat is None or sber_lon is None:
        return None

    best = None
    best_dist = MATCH_DISTANCE_M
    for t in tbank_stations:
        t_lat, t_lon = t.get("lat"), t.get("lon")
        if t_lat is None or t_lon is None:
            continue
        dist = _haversine_m(sber_lat, sber_lon, t_lat, t_lon)
        if dist < best_dist:
            best = t
            best_dist = dist
    return best
