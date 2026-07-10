"""
Логика сбора данных с sberazs.ru/api/stations.

Куки от антибот-защиты кэшируются в памяти процесса и обновляются
только когда API реально отвечает 401/403 — не гоняем браузер на
каждый цикл обновления, только когда сессия протухла.
"""

import asyncio
import logging
import time
from typing import Optional

import requests
from playwright.async_api import async_playwright

from app.config import REQUEST_DELAY_SEC

log = logging.getLogger(__name__)

BASE_URL = "https://sberazs.ru"
API_URL = "https://sberazs.ru/api/stations"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


async def get_cookies_via_browser() -> dict:
    """Открывает сайт в headless-браузере, дожидается антибот-кук."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_UA, locale="ru-RU")
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="networkidle")
        await page.wait_for_timeout(3000)
        cookies = await context.cookies()
        await browser.close()
    return {c["name"]: c["value"] for c in cookies}


def build_grid(bbox: tuple[float, float, float, float], step_lon: float, step_lat: float):
    min_lon, min_lat, max_lon, max_lat = bbox
    cells = []
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            cells.append((lon, lat, min(lon + step_lon, max_lon), min(lat + step_lat, max_lat)))
            lat += step_lat
        lon += step_lon
    return cells


def _fetch_stations_sync(session: requests.Session, bbox_cell) -> list:
    min_lon, min_lat, max_lon, max_lat = bbox_cell
    params = {"bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}"}
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en-US;q=0.9,en;q=0.8",
        "referer": BASE_URL + "/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    resp = session.get(API_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("stations", [])


class StationsClient:
    """Держит сессию с куками, умеет сама обновлять их при 401/403."""

    def __init__(self):
        self._session = requests.Session()
        self._cookies_ready = False

    async def _ensure_cookies(self):
        if not self._cookies_ready:
            log.info("Получаю антибот-куки через браузер...")
            cookies = await get_cookies_via_browser()
            self._session.cookies.clear()
            self._session.cookies.update(cookies)
            self._cookies_ready = True

    async def collect_area(self, bbox: tuple[float, float, float, float], step_lon: float, step_lat: float) -> dict:
        """
        Обходит bbox сеткой, возвращает словарь {api_id: station_dict}
        со всеми найденными станциями (без фильтрации по свежести —
        это решает вызывающий код).
        """
        await self._ensure_cookies()

        grid = build_grid(bbox, step_lon, step_lat)
        result: dict[str, dict] = {}

        for cell in grid:
            try:
                stations = await asyncio.to_thread(_fetch_stations_sync, self._session, cell)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (401, 403):
                    log.warning("Куки истекли, обновляю сессию...")
                    self._cookies_ready = False
                    await self._ensure_cookies()
                    try:
                        stations = await asyncio.to_thread(_fetch_stations_sync, self._session, cell)
                    except requests.HTTPError as e2:
                        log.error("Повторная ошибка на ячейке %s: %s", cell, e2)
                        continue
                else:
                    log.error("Ошибка на ячейке %s: %s", cell, e)
                    continue

            for st in stations:
                api_id = st.get("id")
                if api_id is not None:
                    result[str(api_id)] = st

            await asyncio.sleep(REQUEST_DELAY_SEC)

        return result
