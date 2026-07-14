"""
Бот для лёгкого сервера (белый IP, за tgproxy). Ничего не парсит сам.
Держит HTTP API, в который стучится сервер-скрапер (тот — за NAT,
поэтому именно он инициирует соединения, а не наоборот):

  GET  /commands        — скрапер забирает накопившиеся команды пользователей
  POST /command-result   — скрапер присылает результат конкретной команды
  POST /notifications     — скрапер пушит новые данные для подписчиков
"""

import asyncio
import json
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from aiohttp import web

from app.config import API_HOST, API_PORT, API_TOKEN, BOT_TOKEN
from app.formatting import format_resumption, format_station_list
from app.storage import CATEGORY_LABELS
from app import bot_storage as storage

log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------------------------------------------------------------------
# Учёт пользователей — нужен только для рассылки через консоль
# (app/broadcast.py). Пишем chat_id при любом входящем апдейте.
# ---------------------------------------------------------------------

class UserTrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        chat_id = None
        username = None
        if isinstance(event, Message):
            chat_id = event.chat.id
            username = event.from_user.username if event.from_user else None
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id
            username = event.from_user.username if event.from_user else None
        if chat_id is not None:
            storage.register_user(chat_id, username)
        return await handler(event, data)


dp.message.middleware(UserTrackingMiddleware())
dp.callback_query.middleware(UserTrackingMiddleware())


# ---------------------------------------------------------------------
# Telegram-команды
# ---------------------------------------------------------------------

START_TEXT = (
    "⛽️ Бот следит за наличием топлива на АЗС по данным реальных оплат из открытых"
    "источников и присылает уведомление, как только оно снова появляется.\n\n"
    "⚙️ <b>Действия</b> — просканировать город сейчас или оформить подписку\n"
    "🔔 <b>Подписки</b> — посмотреть текущие подписки или отменить все"
)

_MENU_BUTTON_TEXT = "☰ Меню"

_PERSISTENT_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_MENU_BUTTON_TEXT)]],
    resize_keyboard=True,
)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Действия", callback_data="menu:actions")],
            [InlineKeyboardButton(text="🔔 Подписки", callback_data="menu:subs")],
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(START_TEXT, parse_mode="HTML", reply_markup=_PERSISTENT_KEYBOARD)
    await message.answer("Выберите раздел:", reply_markup=_main_menu_keyboard())


@dp.message(F.text == _MENU_BUTTON_TEXT)
async def cmd_menu_button(message: Message):
    """Кнопка на месте клавиатуры делает то же самое, что /start."""
    await cmd_start(message)


def _cities_keyboard(action: str) -> InlineKeyboardMarkup:
    """action: 'scan' (сразу сканирует) или 'subpick' (спросит категорию дальше)."""
    areas = storage.get_known_areas()
    buttons = [
        [InlineKeyboardButton(text=a["area"], callback_data=f"{action}:{a['id']}")]
        for a in areas
    ]
    if action == "subpick":
        buttons.append([InlineKeyboardButton(text="Все области", callback_data="subpick:all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _category_keyboard(area_token: str) -> InlineKeyboardMarkup:
    """area_token: либо 'all', либо id города — прокидывается дальше в callback_data."""
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"subcat:{area_token}:{cat}")]
        for cat, label in CATEGORY_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _send_actions_menu(message: Message):
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
        reply_markup=_cities_keyboard("subpick"),
    )


@dp.message(Command("cities"))
async def cmd_cities(message: Message):
    await _send_actions_menu(message)


@dp.callback_query(F.data == "menu:actions")
async def cb_menu_actions(callback: CallbackQuery):
    await callback.answer()
    await _send_actions_menu(callback.message)


def _subscriptions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отказаться от всех подписок", callback_data="unsub_all")]
        ]
    )


async def _send_subscriptions_menu(message: Message, chat_id: int):
    subs = storage.get_subscriptions(chat_id)
    if not subs:
        await message.answer(
            "У вас нет активных подписок. Оформить можно через «⚙️ Действия»."
        )
        return

    lines = []
    for s in subs:
        area_label = "все области" if s["area"] == "all" else s["area"]
        cat_label = CATEGORY_LABELS.get(s["category"], s["category"])
        lines.append(f"• {area_label} — {cat_label}")

    await message.answer(
        "Ваши подписки:\n" + "\n".join(lines),
        reply_markup=_subscriptions_keyboard(),
    )


@dp.callback_query(F.data == "menu:subs")
async def cb_menu_subs(callback: CallbackQuery):
    await callback.answer()
    await _send_subscriptions_menu(callback.message, callback.message.chat.id)


@dp.callback_query(F.data == "unsub_all")
async def cb_unsub_all(callback: CallbackQuery):
    storage.remove_subscriber(callback.message.chat.id)
    await callback.answer("Отписал от всех уведомлений.")
    await callback.message.edit_text("Вы отписаны от всех уведомлений.", reply_markup=None)


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


@dp.callback_query(F.data.startswith("subpick:"))
async def cb_subpick(callback: CallbackQuery):
    """Первый шаг подписки: город выбран, теперь спрашиваем категорию."""
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value != "all":
        area = storage.get_area_by_id(int(value))
        if area is None:
            await callback.message.answer("Город не найден (возможно, список устарел — откройте /cities заново).")
            return

    await callback.message.answer(
        "Какая категория топлива интересует?",
        reply_markup=_category_keyboard(value),
    )


@dp.callback_query(F.data.startswith("subcat:"))
async def cb_subcat(callback: CallbackQuery):
    """Второй шаг подписки: категория выбрана — оформляем подписку."""
    _, area_token, category = callback.data.split(":", 2)
    await callback.answer()

    if area_token == "all":
        area = "all"
    else:
        area = storage.get_area_by_id(int(area_token))
        if area is None:
            await callback.message.answer("Город не найден (возможно, список устарел — откройте /cities заново).")
            return

    storage.add_subscriber(callback.message.chat.id, area, category)
    area_label = "все области" if area == "all" else area
    cat_label = CATEGORY_LABELS.get(category, category)
    await callback.message.answer(f"Подписал вас: {area_label}, категория: {cat_label}.")


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


CATEGORY_ALIASES = {
    "бензин": "petrol", "petrol": "petrol",
    "дизель": "diesel", "diesel": "diesel",
    "газ": "gas", "gas": "gas",
    "все": "all", "всё": "all", "all": "all",
}


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    parts = message.text.split(maxsplit=2)
    area = parts[1].strip() if len(parts) > 1 else "all"
    category = CATEGORY_ALIASES.get(parts[2].strip().lower(), "all") if len(parts) > 2 else "all"

    storage.add_subscriber(message.chat.id, area, category)
    area_label = "все области" if area == "all" else area
    cat_label = CATEGORY_LABELS.get(category, category)
    await message.answer(f"Подписал вас: {area_label}, категория: {cat_label}.")


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
    lines = []
    for s in subs:
        area_label = "все области" if s["area"] == "all" else s["area"]
        cat_label = CATEGORY_LABELS.get(s["category"], s["category"])
        lines.append(f"• {area_label} — {cat_label}")
    await message.answer("Ваши подписки:\n" + "\n".join(lines))


@dp.message(F.text.startswith("/"))
async def cmd_unknown(message: Message):
    """Ловит опечатки вроде /scan_citi вместо /scan_city — чтобы бот не
    молчал, а подсказывал, что команда не распознана."""
    await message.answer(
        "Не знаю такую команду. Нажмите «☰ Меню» ниже или отправьте /start, "
        "чтобы открыть кнопки «Действия» и «Подписки»."
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

        resumed = json.loads(upd.get("resumed_fuels_json") or "[]")
        categories = {f.get("category", "other") for f in resumed}
        if not categories:
            continue

        # Собираем получателей по каждой категории, что вернулась —
        # чтобы подписчик "только бензин" не получал уведомление о газе.
        chat_ids = set()
        for cat in categories:
            chat_ids.update(storage.get_subscribers_for_area(area, cat))
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
