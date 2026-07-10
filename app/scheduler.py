import asyncio
import logging

from app.config import GEOBOXES, MAX_PAYMENT_AGE_MINUTES, SCAN_INTERVAL_MINUTES, STEP_LON, STEP_LAT
from app.formatting import format_station
from app import storage
from app.bot import bot, client

log = logging.getLogger(__name__)


async def _notify_subscribers(area: str, updates: list[dict]):
    if not updates:
        return

    chat_ids = storage.get_subscribers_for_area(area)
    if not chat_ids:
        return

    for st in updates:
        text = f"🆕 Обновление ({area}):\n\n{format_station(st)}"
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception:
                log.exception("Не удалось отправить сообщение chat_id=%s", chat_id)
            await asyncio.sleep(0.05)  # не долбить Telegram API слишком быстро


async def _scan_cycle():
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

        await _notify_subscribers(area, updates)


async def run_scheduler():
    log.info("Планировщик запущен, интервал %d минут", SCAN_INTERVAL_MINUTES)
    while True:
        try:
            await _scan_cycle()
        except Exception:
            log.exception("Ошибка в цикле сканирования")
        await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)
