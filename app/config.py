import os

# Роль процесса: "scraper" (мощный сервер, парсит и отдаёт API) или
# "bot" (лёгкий сервер, только Telegram + опрос API мощного сервера).
ROLE = os.environ.get("ROLE", "monolith")  # "monolith" — старый режим всё-в-одном

# Общий секрет между сервером-скрапером и сервером-ботом.
API_TOKEN = os.environ.get("API_TOKEN", "")

# --- нужны только на сервере-скрапере (ROLE=scraper или monolith) ---
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "12"))
MAX_PAYMENT_AGE_MINUTES = int(os.environ.get("MAX_PAYMENT_AGE_MINUTES", "20"))
STEP_LON = float(os.environ.get("STEP_LON", "0.3"))
STEP_LAT = float(os.environ.get("STEP_LAT", "0.2"))
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "0.4"))
DB_PATH = os.environ.get("DB_PATH", "/app/data/stations.db")
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8080"))

# --- нужны только на сервере-боте (ROLE=bot или monolith) ---
if ROLE in ("bot", "monolith"):
    BOT_TOKEN = os.environ["BOT_TOKEN"]
else:
    BOT_TOKEN = ""

# URL API сервера-скрапера, например http://1.2.3.4:8080
SCRAPER_API_URL = os.environ.get("SCRAPER_API_URL", "")

# Как часто лёгкий бот опрашивает скрапер за новыми обновлениями, сек.
POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "30"))

# Путь к локальной базе подписчиков на сервере-боте.
BOT_DB_PATH = os.environ.get("BOT_DB_PATH", "/app/data/bot.db")

# ---------------------------------------------------------------------
# GEOBOXES: ваши области (актуально только на сервере-скрапере).
# Реальные координаты вынесены в app/geoboxes_local.py — этот файл в
# .gitignore и никогда не попадёт в публичный репозиторий.
# ---------------------------------------------------------------------
try:
    from app.geoboxes_local import GEOBOXES  # noqa: F401
except ImportError:
    GEOBOXES: dict[str, tuple[float, float, float, float]] = {}
