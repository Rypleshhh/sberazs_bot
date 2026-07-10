"""
API, который отдаёт лёгкому серверу-боту данные, собранные скрапером.
Простая token-авторизация через заголовок Authorization: Bearer <API_TOKEN>.
"""

from aiohttp import web

from app.config import API_TOKEN, MAX_PAYMENT_AGE_MINUTES
from app import storage


def _check_auth(request: web.Request) -> bool:
    if not API_TOKEN:
        return True  # токен не задан — авторизация выключена (не рекомендуется в проде)
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {API_TOKEN}"


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def handle_cities(request: web.Request):
    return web.json_response({"cities": storage.get_areas()})


async def handle_fresh(request: web.Request):
    area = request.match_info["area"]
    stations = storage.get_fresh_stations(area, MAX_PAYMENT_AGE_MINUTES)
    return web.json_response({"stations": stations})


async def handle_updates(request: web.Request):
    after_id = int(request.query.get("after_id", "0"))
    updates = storage.get_notifications_after(after_id)
    max_id = max((u["id"] for u in updates), default=after_id)
    return web.json_response({"updates": updates, "last_id": max_id})


async def handle_health(request: web.Request):
    return web.json_response({"status": "ok"})


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/health", handle_health)
    app.router.add_get("/cities", handle_cities)
    app.router.add_get("/fresh/{area}", handle_fresh)
    app.router.add_get("/updates", handle_updates)
    return app
