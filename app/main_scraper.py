import asyncio
import logging

import aiohttp

from app.config import (
    GEOBOXES,
    MAX_PAYMENT_AGE_MINUTES,
    STEP_LON,
    STEP_LAT,
    LIGHT_SERVER_URL,
    API_TOKEN,
    COMMAND_POLL_INTERVAL_SEC,
)
from app import storage
from app.scraper import StationsClient
from app.scheduler import run_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


async def _fulfil_command(client: StationsClient, session: aiohttp.ClientSession, cmd: dict):
    area = cmd["area"]
    bbox = GEOBOXES.get(area)

    if bbox is None:
        stations = []
    else:
        try:
            found = await client.collect_area(bbox, STEP_LON, STEP_LAT)
            storage.upsert_stations_and_get_updates(area, found, MAX_PAYMENT_AGE_MINUTES)
            stations = storage.get_fresh_stations(area, MAX_PAYMENT_AGE_MINUTES)
        except Exception:
            log.exception("Ошибка выполнения команды для области %s", area)
            stations = []

    try:
        async with session.post(
            f"{LIGHT_SERVER_URL}/command-result",
            json={"command_id": cmd["id"], "area": area, "stations": stations},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
    except Exception:
        log.exception("Не удалось отправить результат команды %s на лёгкий сервер", cmd["id"])


async def poll_commands_loop(client: StationsClient):
    """Скрапер сам спрашивает лёгкий сервер: 'есть что сделать?' — он за
    NAT и не может принимать входящие соединения, поэтому опрашивает сам."""
    log.info("Опрос команд запущен, интервал %d сек", COMMAND_POLL_INTERVAL_SEC)
    async with aiohttp.ClientSession(headers=_headers) as session:
        while True:
            try:
                async with session.get(
                    f"{LIGHT_SERVER_URL}/commands", timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

                for cmd in data.get("commands", []):
                    await _fulfil_command(client, session, cmd)

            except Exception:
                log.exception("Ошибка опроса команд")

            await asyncio.sleep(COMMAND_POLL_INTERVAL_SEC)


async def main():
    if not LIGHT_SERVER_URL:
        log.error("LIGHT_SERVER_URL не задан — скрапер не сможет достучаться до бота")

    storage.init_db()
    client = StationsClient()

    await asyncio.gather(
        run_scheduler(client),
        poll_commands_loop(client),
    )


if __name__ == "__main__":
    asyncio.run(main())
