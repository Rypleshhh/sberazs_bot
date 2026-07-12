"""
Бот для лёгкого сервера (белый IP, за tgproxy). Ничего не парсит сам.
Держит HTTP API, в который стучится сервер-скрапер (тот — за NAT,
поэтому именно он инициирует соединения, а не наоборот):

  GET  /commands        — скрапер забирает накопившиеся команды пользователей
  POST /command-result   — скрапер присылает результат конкретной команды
  POST /notifications     — скрапер пушит новые данные для подписчиков
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

from app.config import API_HOST, API_PORT, API_TOKEN, BOT_TOKEN
from app.formatting import format_station, format_station_list
from app import bot_storage as storage

log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------------------------------------------------------------------
# Telegram-команды
# ---------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Команды:\n\n"
        "/scan_city <город> — запросить свежие станции по городу "
        "(ответ придёт, как только сервер-скрапер обработает запрос)\n"
        "/subscribe [город] — уведомления по мере обновления данных "
        "(без города — по всем областям)\n"
        "/unsubscribe — отписаться от всех уведомлений\n"
        "/my_subscriptions — на что вы подписаны",
        parse_mode="HTML",
    )


@dp.message(Command("scan_city"))
async def cmd_scan_city(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите город: /scan_city название")
        return

    area = parts[1].strip()
    storage.add_command(message.chat.id, area)
    await message.answer(f"Принял запрос по «{area}», жду ответа от сервера-скрапера...")


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


# ---------------------------------------------------------------------
# HTTP API — сюда стучится скрапер
# ---------------------------------------------------------------------

def _check_auth(request: web.Request) -> bool:
    if not API_TOKEN:
        return True
    return request.headers.get("Authorization") == f"Bearer {API_TOKEN}"


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def handle_get_commands(request: web.Request):
    commands = storage.pop_pending_commands()
    return web.json_response({"commands": commands})


async def handle_command_result(request: web.Request):
    data = await request.json()
    command_id = data.get("command_id")
    stations = data.get("stations", [])
    area = data.get("area", "")

    cmd = storage.get_command(command_id)
    if not cmd:
        return web.json_response({"error": "unknown command_id"}, status=404)

    chat_id = cmd["chat_id"]
    for chunk in format_station_list(stations, f"🔎 Результаты: {area}"):
        try:
            await bot.send_message(chat_id, chunk, parse_mode="HTML")
        except Exception:
            log.exception("Не удалось отправить результат команды chat_id=%s", chat_id)

    storage.mark_command_done(command_id)
    return web.json_response({"status": "ok"})


async def handle_notifications(request: web.Request):
    data = await request.json()
    updates = data.get("updates", [])

    for upd in updates:
        area = upd.get("area", "")
        chat_ids = storage.get_subscribers_for_area(area)
        if not chat_ids:
            continue
        text = f"🆕 Обновление ({area}):\n\n{format_station(upd)}"
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception:
                log.exception("Не удалось отправить уведомление chat_id=%s", chat_id)
            await asyncio.sleep(0.05)

    return web.json_response({"status": "ok"})


async def handle_health(request: web.Request):
    return web.json_response({"status": "ok"})


def build_api_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/health", handle_health)
    app.router.add_get("/commands", handle_get_commands)
    app.router.add_post("/command-result", handle_command_result)
    app.router.add_post("/notifications", handle_notifications)
    return app


async def run_api_server():
    app = build_api_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    log.info("API запущено на %s:%s (сюда стучится скрапер)", API_HOST, API_PORT)
    while True:
        await asyncio.sleep(3600)


async def main():
    storage.init_db()
    await asyncio.gather(
        dp.start_polling(bot),
        run_api_server(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
