import asyncio
import logging

from app.config import GEOBOXES, MAX_PAYMENT_AGE_MINUTES, SCAN_INTERVAL_MINUTES, STEP_LON, STEP_LAT
from app import storage
from app.scraper import StationsClient

log = logging.getLogger(__name__)


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

        # Уведомления складываются в очередь pending_notifications внутри
        # upsert_stations_and_get_updates — их дальше читает либо сам бот
        # (моно-режим), либо API-сервер (режим scraper+bot на разных хостах).
        updates = storage.upsert_stations_and_get_updates(area, found, MAX_PAYMENT_AGE_MINUTES)
        log.info("Область %s: найдено %d станций, новых обновлений %d", area, len(found), len(updates))


async def run_scheduler(client: StationsClient):
    log.info("Планировщик запущен, интервал %d минут", SCAN_INTERVAL_MINUTES)
    while True:
        try:
            await _scan_cycle(client)
        except Exception:
            log.exception("Ошибка в цикле сканирования")
        await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)
