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

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web

from app.config import API_HOST, API_PORT, API_TOKEN, BOT_TOKEN
from app.formatting import format_resumption, format_station_list
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
        "/cities — выбрать город кнопками (сканировать или подписаться)\n"
        "/scan_city &lt;город&gt; — запросить свежие станции по городу текстом\n"
        "/subscribe [город] — уведомления по мере обновления данных "
        "(без города — по всем областям)\n"
        "/unsubscribe — отписаться от всех уведомлений\n"
        "/my_subscriptions — на что вы подписаны",
        parse_mode="HTML",
    )


def _cities_keyboard(action: str) -> InlineKeyboardMarkup:
    """action: 'scan' или 'sub' — какое действие выполнит нажатие кнопки."""
    areas = storage.get_known_areas()
    buttons = [
        [InlineKeyboardButton(text=a["area"], callback_data=f"{action}:{a['id']}")]
        for a in areas
    ]
    if action == "sub":
        buttons.append([InlineKeyboardButton(text="Все области", callback_data="sub:all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("cities"))
async def cmd_cities(message: Message):
    areas = storage.get_known_areas()
    if not areas:
        await message.answer(
            "Список городов пока пуст — скрапер ещё не присылал данные. "
            "Подождите первого цикла сканирования или используйте /scan_city вручную."
        )
        return

    await message.answer(
        "Выберите город, чтобы просканировать (данные придут по готовности):",
        reply_markup=_cities_keyboard("scan"),
    )
    await message.answer(
        "Или выберите город, чтобы подписаться на уведомления:",
        reply_markup=_cities_keyboard("sub"),
    )


async def _start_scan(chat_id: int, area: str):
    command_id = storage.add_command(chat_id, area)
    log.info("Создана команда id=%s chat_id=%s area=%s", command_id, chat_id, area)
    await bot.send_message(chat_id, f"Принял запрос по «{area}», жду ответа от сервера-скрапера...")
    asyncio.create_task(_watch_command_timeout(command_id, chat_id, area))


@dp.callback_query(F.data.startswith("scan:"))
async def cb_scan(callback: CallbackQuery):
    area_id = int(callback.data.split(":", 1)[1])
    area = storage.get_area_by_id(area_id)
    await callback.answer()
    if area is None:
        await callback.message.answer("Город не найден (возможно, список устарел — откройте /cities заново).")
        return
    await _start_scan(callback.message.chat.id, area)


@dp.callback_query(F.data.startswith("sub:"))
async def cb_subscribe(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "all":
        storage.add_subscriber(callback.message.chat.id, "all")
        await callback.message.answer("Подписал вас на уведомления: все области.")
        return

    area = storage.get_area_by_id(int(value))
    if area is None:
        await callback.message.answer("Город не найден (возможно, список устарел — откройте /cities заново).")
        return
    storage.add_subscriber(callback.message.chat.id, area)
    await callback.message.answer(f"Подписал вас на уведомления: {area}.")


@dp.message(Command("scan_city"))
async def cmd_scan_city(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите город: /scan_city название (или используйте /cities для выбора кнопкой)")
        return

    area = parts[1].strip()
    await _start_scan(message.chat.id, area)


TIMEOUT_SEC = 40


async def _watch_command_timeout(command_id: int, chat_id: int, area: str):
    """Если скрапер не отчитался за TIMEOUT_SEC — сообщаем пользователю,
    что что-то пошло не так, вместо тишины."""
    await asyncio.sleep(TIMEOUT_SEC)
    cmd = storage.get_command(command_id)
    if cmd is None:
        return
    if cmd["status"] != "done":
        log.warning("Команда id=%s не выполнена за %d сек (статус=%s)", command_id, TIMEOUT_SEC, cmd["status"])
        try:
            await bot.send_message(
                chat_id,
                f"⚠️ Долго нет ответа от сервера-скрапера по «{area}».\n"
                f"Возможные причины: скрапер сейчас не в сети, город не настроен "
                f"в GEOBOXES, либо сканирование занимает дольше обычного.",
            )
        except Exception:
            log.exception("Не удалось отправить сообщение о таймауте chat_id=%s", chat_id)


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


@dp.message(F.text.startswith("/"))
async def cmd_unknown(message: Message):
    """Ловит опечатки вроде /scan_citi вместо /scan_city — чтобы бот не
    молчал, а подсказывал, что команда не распознана."""
    await message.answer(
        "Не знаю такую команду. Проверьте написание — доступные команды:\n"
        "/cities, /scan_city, /subscribe, /unsubscribe, /my_subscriptions"
    )


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
    if commands:
        log.info("Отдаю скраперу %d команд(ы): %s", len(commands), [c["id"] for c in commands])
    return web.json_response({"commands": commands})


async def handle_command_result(request: web.Request):
    data = await request.json()
    command_id = data.get("command_id")
    stations = data.get("stations", [])
    area = data.get("area", "")
    log.info("Получен результат команды id=%s area=%s станций=%d", command_id, area, len(stations))

    cmd = storage.get_command(command_id)
    if not cmd:
        log.warning("Команда id=%s не найдена в БД", command_id)
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
        text = f"🆕 Топливо снова в наличии ({area}):\n\n{format_resumption(upd)}"
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception:
                log.exception("Не удалось отправить уведомление chat_id=%s", chat_id)
            await asyncio.sleep(0.05)

    return web.json_response({"status": "ok"})


async def handle_areas(request: web.Request):
    data = await request.json()
    areas = data.get("areas", [])
    storage.sync_known_areas(areas)
    return web.json_response({"status": "ok", "count": len(areas)})


async def handle_health(request: web.Request):
    return web.json_response({"status": "ok"})


def build_api_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/health", handle_health)
    app.router.add_get("/commands", handle_get_commands)
    app.router.add_post("/command-result", handle_command_result)
    app.router.add_post("/notifications", handle_notifications)
    app.router.add_post("/areas", handle_areas)
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
