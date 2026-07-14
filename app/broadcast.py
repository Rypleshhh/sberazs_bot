"""
Рассылка сообщения всем пользователям бота (лёгкий сервер) через консоль.

Список получателей — таблица `users` в bot.db: туда пишется chat_id
любого, кто хоть раз прислал боту сообщение или нажал кнопку (см.
UserTrackingMiddleware в light_bot.py). Список не связан с подпиской на
уведомления о топливе — это просто "все, кто когда-либо писал боту".

Запуск (на лёгком сервере, где крутится контейнер бота):

    docker compose -f docker-compose.bot.yml exec bot python -m app.broadcast "Текст сообщения"

Текст поддерживает HTML-разметку Telegram (<b>, <i>, <a href="...">…</a> и т.д.).
Если нужно отправить без разметки — флаг --plain.

Длинный текст удобнее держать в файле:

    docker compose -f docker-compose.bot.yml exec bot python -m app.broadcast --file /app/data/message.txt
"""

import argparse
import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import BOT_TOKEN
from app import bot_storage as storage

log = logging.getLogger(__name__)

DELAY_BETWEEN_SENDS_SEC = 0.05  # ~20 сообщений/сек, с запасом от лимитов Telegram


async def broadcast(text: str, parse_mode: str | None = "HTML"):
    storage.init_db()
    user_ids = storage.get_all_user_ids()
    if not user_ids:
        print("Нет ни одного известного пользователя (таблица users пуста).")
        return

    print(f"Отправляю {len(user_ids)} пользователям...")
    bot = Bot(token=BOT_TOKEN)
    sent = 0
    blocked = 0
    failed = 0
    try:
        for chat_id in user_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode=parse_mode)
                sent += 1
            except TelegramForbiddenError:
                # Пользователь заблокировал бота или удалил чат — тихо пропускаем.
                blocked += 1
            except TelegramBadRequest as exc:
                log.warning("Не удалось отправить chat_id=%s: %s", chat_id, exc)
                failed += 1
            await asyncio.sleep(DELAY_BETWEEN_SENDS_SEC)
    finally:
        await bot.session.close()

    print(
        f"Готово. Доставлено: {sent}, заблокировали бота: {blocked}, "
        f"прочие ошибки: {failed} (всего в базе: {len(user_ids)})"
    )


def main():
    parser = argparse.ArgumentParser(description="Рассылка сообщения всем пользователям бота")
    parser.add_argument("text", nargs="?", help="Текст сообщения (в кавычках)")
    parser.add_argument("--file", help="Прочитать текст сообщения из файла вместо аргумента")
    parser.add_argument("--plain", action="store_true", help="Отправить без HTML-разметки")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read().strip()
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    if not text:
        print("Текст сообщения пустой.")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(broadcast(text, parse_mode=None if args.plain else "HTML"))


if __name__ == "__main__":
    main()
