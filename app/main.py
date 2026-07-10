import asyncio
import logging

from app import storage
from app.bot import bot, dp
from app.scheduler import run_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    storage.init_db()
    await asyncio.gather(
        dp.start_polling(bot),
        run_scheduler(),
    )


if __name__ == "__main__":
    asyncio.run(main())
