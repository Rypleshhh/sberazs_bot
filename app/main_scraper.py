import asyncio
import logging

from aiohttp import web

from app.config import API_HOST, API_PORT
from app import storage
from app.api import build_app
from app.scraper import StationsClient
from app.scheduler import run_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


async def main():
    storage.init_db()
    client = StationsClient()

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    log.info("API запущено на %s:%s", API_HOST, API_PORT)

    await run_scheduler(client)


if __name__ == "__main__":
    asyncio.run(main())
