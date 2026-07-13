import asyncio
import logging

import aiohttp

from app.config import (
    GEOBOXES,
    MAX_PAYMENT_AGE_MINUTES,
    SCAN_INTERVAL_MINUTES,
    STEP_LON,
    STEP_LAT,
    LIGHT_SERVER_URL,
    API_TOKEN,
)
from app import storage
from app.scraper import StationsClient
from app.tbank_client import fetch_tbank_stations, find_tbank_match
from app.gdebenz_client import fetch_gdebenz_stations, find_gdebenz_match

log = logging.getLogger(__name__)

_headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


async def _build_tbank_matches(bbox: tuple, sber_stations: dict) -> dict:
    """Тянет T-Bank по тому же bbox и матчит с найденными станциями
    sberazs по географической близости. sberazs остаётся главным
    источником — это только вторичное подтверждение для карточки."""
    tbank_stations = await fetch_tbank_stations(bbox)
    if not tbank_stations:
        return {}

    matches = {}
    for api_id, st in sber_stations.items():
        location = st.get("location") or {}
        match = find_tbank_match(location.get("lat"), location.get("lon"), tbank_stations)
        if match:
            matches[api_id] = match
    return matches


async def _build_gdebenz_matches(bbox: tuple, sber_stations: dict) -> dict:
    """Тянет gdebenz (краудсорсинг) по тому же bbox и матчит так же, как
    T-Bank — третье, самое слабое по надёжности мнение рядом с sberazs."""
    gdebenz_stations = await fetch_gdebenz_stations(bbox)
    if not gdebenz_stations:
        return {}

    matches = {}
    for api_id, st in sber_stations.items():
        location = st.get("location") or {}
        match = find_gdebenz_match(location.get("lat"), location.get("lon"), gdebenz_stations)
        if match:
            matches[api_id] = match
    return matches


async def _push_notifications(updates: list[dict]):
    """Скрапер сам стучится на лёгкий сервер и отдаёт свежие уведомления —
    он же за NAT, принимать входящие соединения не может."""
    if not updates or not LIGHT_SERVER_URL:
        return
    try:
        async with aiohttp.ClientSession(headers=_headers) as session:
            async with session.post(
                f"{LIGHT_SERVER_URL}/notifications",
                json={"updates": updates},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
    except Exception:
        log.exception("Не удалось отправить уведомления на лёгкий сервер")


async def _push_areas():
    """Раз в цикл сообщаем лёгкому серверу, какие города вообще настроены —
    он не видит GEOBOXES напрямую, только через этот пуш."""
    if not LIGHT_SERVER_URL:
        return
    try:
        async with aiohttp.ClientSession(headers=_headers) as session:
            async with session.post(
                f"{LIGHT_SERVER_URL}/areas",
                json={"areas": list(GEOBOXES.keys())},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
    except Exception:
        log.exception("Не удалось отправить список городов на лёгкий сервер")


async def _scan_cycle(client: StationsClient):
    if not GEOBOXES:
        log.warning("GEOBOXES пуст — планировщику нечего сканировать")
        return

    await _push_areas()

    for area, bbox in GEOBOXES.items():
        log.info("Сканирую область: %s", area)
        try:
            found = await client.collect_area(bbox, STEP_LON, STEP_LAT)
        except Exception:
            log.exception("Ошибка сканирования области %s", area)
            continue

        try:
            tbank_matches = await _build_tbank_matches(bbox, found)
            if tbank_matches:
                log.info("Область %s: сматчено %d станций с T-Bank", area, len(tbank_matches))
        except Exception:
            log.exception("Ошибка матчинга T-Bank для области %s", area)
            tbank_matches = {}

        try:
            gdebenz_matches = await _build_gdebenz_matches(bbox, found)
            if gdebenz_matches:
                log.info("Область %s: сматчено %d станций с gdebenz", area, len(gdebenz_matches))
        except Exception:
            log.exception("Ошибка матчинга gdebenz для области %s", area)
            gdebenz_matches = {}

        updates = storage.upsert_stations_and_get_updates(
            area, found, MAX_PAYMENT_AGE_MINUTES, tbank_matches, gdebenz_matches
        )
        log.info("Область %s: найдено %d станций, новых обновлений %d", area, len(found), len(updates))

        await _push_notifications(updates)


async def run_scheduler(client: StationsClient):
    log.info("Планировщик запущен, интервал %d минут", SCAN_INTERVAL_MINUTES)
    while True:
        try:
            await _scan_cycle(client)
        except Exception:
            log.exception("Ошибка в цикле сканирования")
        await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)
