"""
Бот для лёгкого сервера (тот, что за tgproxy). Не парсит ничего сам —
только ходит за данными в API сервера-скрапера и общается с Telegram.
"""

import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from app.config import BOT_TOKEN, POLL_INTERVAL_SEC, SCRAPER_API_URL, API_TOKEN
from app.formatting import format_station, format_station_list
from app import bot_storage as storage

log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

_headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


async def _api_get(path: str, params: dict = None) -> dict:
    async with aiohttp.ClientSession(headers=_headers) as session:
        async with session.get(f"{SCRAPER_API_URL}{path}", params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _cities_text() -> str:
    data = await _api_get("/cities")
    cities = data.get("cities", [])
    if not cities:
        return "Города пока не появились (скрапер ещё не отсканировал ни одной области)."
    return "\n".join(f"• {c}" for c in cities)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Команды:\n\n"
        "/scan_city <город> — свежие станции по одному городу\n"
        "/cities — список доступных городов\n"
        "/subscribe [город] — уведомления по мере обновления данных "
        "(без города — по всем областям)\n"
        "/unsubscribe — отписаться от всех уведомлений\n"
        "/my_subscriptions — на что вы подписаны",
        parse_mode="HTML",
    )


@dp.message(Command("cities"))
async def cmd_cities(message: Message):
    try:
        await message.answer(await _cities_text())
    except Exception:
        log.exception("Ошибка запроса /cities к API скрапера")
        await message.answer("Не удалось связаться со скрапер-сервером, попробуйте позже.")


@dp.message(Command("scan_city"))
async def cmd_scan_city(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(f"Укажите город: /scan_city название\n\nДоступные:\n{await _cities_text()}")
        return

    area = parts[1].strip()
    try:
        data = await _api_get(f"/fresh/{area}")
    except Exception:
        log.exception("Ошибка запроса /fresh к API скрапера")
        await message.answer("Не удалось связаться со скрапер-сервером, попробуйте позже.")
        return

    stations = data.get("stations", [])
    for chunk in format_station_list(stations, f"🔎 Результаты: {area}"):
        await message.answer(chunk, parse_mode="HTML")


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    parts = message.text.split(maxsplit=1)
    area = parts[1].strip() if len(parts) > 1 else "all"
    storage.add_subscriber(message.chat.id, area)
    label = "все области" if area == "all" else area
    await message.answer(f"Подписал вас на уведомления: {label}.")


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    storage.remove_subscriber(message.chat.id)
    await message.answer("Отписал вас от всех уведомлений.")


@dp.message(Command("my_subscriptions"))
async def cmd_my_subscriptions(message: Message):
    subs = storage.get_subscriptions(message.chat.id)
    if not subs:
        await message.answer("У вас нет активных подписок.")
        return
    await message.answer("Ваши подписки:\n" + "\n".join(f"• {s}" for s in subs))


async def poll_updates_loop():
    """Раз в POLL_INTERVAL_SEC спрашивает скрапер о новых уведомлениях и рассылает их."""
    log.info("Опрос обновлений запущен, интервал %d сек", POLL_INTERVAL_SEC)
    while True:
        try:
            after_id = storage.get_last_notification_id()
            data = await _api_get("/updates", params={"after_id": after_id})
            updates = data.get("updates", [])
            last_id = data.get("last_id", after_id)

            for upd in updates:
                area = upd["area"]
                chat_ids = storage.get_subscribers_for_area(area)
                if not chat_ids:
                    continue
                text = f"🆕 Обновление ({area}):\n\n{format_station(upd)}"
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(chat_id, text, parse_mode="HTML")
                    except Exception:
                        log.exception("Не удалось отправить сообщение chat_id=%s", chat_id)
                    await asyncio.sleep(0.05)

            if last_id != after_id:
                storage.set_last_notification_id(last_id)

        except Exception:
            log.exception("Ошибка опроса скрапера")

        await asyncio.sleep(POLL_INTERVAL_SEC)


async def main():
    storage.init_db()
    await asyncio.gather(
        dp.start_polling(bot),
        poll_updates_loop(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
