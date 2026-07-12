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

log = logging.getLogger(__name__)

_headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


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


async def _scan_cycle(client: StationsClient):
    if not GEOBOXES:
        log.warning("GEOBOXES пуст — планировщику нечего сканировать")
        return

    for area, bbox in GEOBOXES.items():
        log.info("Сканирую область: %s", area)
        try:
            found = await client.collect_area(bbox, STEP_LON, STEP_LAT)
        except Exception:
            log.exception("Ошибка сканирования области %s", area)
            continue

        updates = storage.upsert_stations_and_get_updates(area, found, MAX_PAYMENT_AGE_MINUTES)
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
