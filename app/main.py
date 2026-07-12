"""Моно-режим: всё на одном сервере (старое поведение, для справки/тестов)."""

import asyncio
import logging

from app import storage
from app.bot import bot, dp, client
from app.formatting import format_station
from app.scheduler import run_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


async def notify_loop():
    """Раз в 30 сек проверяет очередь уведомлений и рассылает подписчикам."""
    last_id = 0
    while True:
        try:
            updates = storage.get_notifications_after(last_id)
            for upd in updates:
                last_id = max(last_id, upd["id"])
                chat_ids = storage.get_subscribers_for_area(upd["area"])
                text = f"🆕 Обновление ({upd['area']}):\n\n{format_station(upd)}"
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(chat_id, text, parse_mode="HTML")
                    except Exception:
                        log.exception("Не удалось отправить сообщение chat_id=%s", chat_id)
                    await asyncio.sleep(0.05)
        except Exception:
            log.exception("Ошибка в notify_loop")
        await asyncio.sleep(30)


async def main():
    storage.init_db()
    await asyncio.gather(
        dp.start_polling(bot),
        run_scheduler(client),
        notify_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
