import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from app.config import BOT_TOKEN, GEOBOXES, MAX_PAYMENT_AGE_MINUTES, STEP_LON, STEP_LAT
from app.formatting import format_station_list
from app import storage
from app.scraper import StationsClient

log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Общий клиент с кэшированными куками — используется и ботом (ручной
# /scan), и планировщиком (фоновые обновления), см. main.py
client = StationsClient()


def _city_list_text() -> str:
    if not GEOBOXES:
        return "Города пока не настроены (заполните GEOBOXES в config.py)."
    return "\n".join(f"• {name}" for name in GEOBOXES)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Команды:\n\n"
        "/scan — просканировать все настроенные области и показать свежие станции\n"
        "/scan_city &lt;город&gt; — просканировать только один город\n"
        "/cities — список доступных городов\n"
        "/subscribe [город] — получать уведомления по мере обновления данных "
        "(без города — по всем областям)\n"
        "/unsubscribe — отписаться от всех уведомлений\n"
        "/my_subscriptions — на что вы подписаны",
        parse_mode="HTML",
    )


@dp.message(Command("cities"))
async def cmd_cities(message: Message):
    await message.answer(_city_list_text())


@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    if not GEOBOXES:
        await message.answer("Не настроено ни одной области (GEOBOXES пуст).")
        return

    await message.answer("Сканирую все области, это может занять некоторое время...")

    all_stations = []
    for area, bbox in GEOBOXES.items():
        found = await client.collect_area(bbox, STEP_LON, STEP_LAT)
        storage.upsert_stations_and_get_updates(area, found, MAX_PAYMENT_AGE_MINUTES)
        all_stations.extend(storage.get_fresh_stations(area, MAX_PAYMENT_AGE_MINUTES))

    for chunk in format_station_list(all_stations, "🔎 Результаты по всем областям:"):
        await message.answer(chunk, parse_mode="HTML")


@dp.message(Command("scan_city"))
async def cmd_scan_city(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(f"Укажите город: /scan_city название\n\nДоступные:\n{_city_list_text()}")
        return

    area = parts[1].strip()
    bbox = GEOBOXES.get(area)
    if bbox is None:
        await message.answer(f"Город «{area}» не настроен.\n\nДоступные:\n{_city_list_text()}")
        return

    await message.answer(f"Сканирую «{area}»...")
    found = await client.collect_area(bbox, STEP_LON, STEP_LAT)
    storage.upsert_stations_and_get_updates(area, found, MAX_PAYMENT_AGE_MINUTES)
    fresh = storage.get_fresh_stations(area, MAX_PAYMENT_AGE_MINUTES)

    for chunk in format_station_list(fresh, f"🔎 Результаты: {area}"):
        await message.answer(chunk, parse_mode="HTML")


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    parts = message.text.split(maxsplit=1)
    area = parts[1].strip() if len(parts) > 1 else "all"

    if area != "all" and area not in GEOBOXES:
        await message.answer(f"Город «{area}» не настроен.\n\nДоступные:\n{_city_list_text()}")
        return

    storage.add_subscriber(message.chat.id, area)
    label = "все области" if area == "all" else area
    await message.answer(f"Подписал вас на уведомления: {label}. Буду присылать данные по мере обновления.")


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
