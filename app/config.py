import os

# Роль процесса: "scraper" (мощный сервер за NAT, парсит и стучится
# наружу), "bot" (лёгкий сервер с белым IP, слушает + Telegram),
# "monolith" (всё в одном процессе, для теста на одной машине).
ROLE = os.environ.get("ROLE", "monolith")

# Общий секрет между сервером-скрапером и сервером-ботом.
API_TOKEN = os.environ.get("API_TOKEN", "")

# --- нужны только на сервере-скрапере (ROLE=scraper или monolith) ---
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "12"))
MAX_PAYMENT_AGE_MINUTES = int(os.environ.get("MAX_PAYMENT_AGE_MINUTES", "20"))
STEP_LON = float(os.environ.get("STEP_LON", "0.3"))
STEP_LAT = float(os.environ.get("STEP_LAT", "0.2"))
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "0.4"))
DB_PATH = os.environ.get("DB_PATH", "/app/data/stations.db")

# Публичный адрес лёгкого сервера (белый IP) — сюда скрапер стучится
# сам, чтобы отправить уведомления и забрать команды пользователя.
# Обязателен для ROLE=scraper.
LIGHT_SERVER_URL = os.environ.get("LIGHT_SERVER_URL", "")

# Станции с меньшим числом недавних оплат считаем шумом/погрешностью —
# по ним недостаточно данных, чтобы доверять статусу топлива.
MIN_OPERATIONS_COUNT = int(os.environ.get("MIN_OPERATIONS_COUNT", "3"))

# Как часто скрапер спрашивает лёгкий сервер про новые команды, сек.
COMMAND_POLL_INTERVAL_SEC = int(os.environ.get("COMMAND_POLL_INTERVAL_SEC", "5"))

# --- нужны только на сервере-боте (ROLE=bot или monolith) ---
if ROLE in ("bot", "monolith"):
    BOT_TOKEN = os.environ["BOT_TOKEN"]
else:
    BOT_TOKEN = ""

# На чём слушает HTTP API лёгкого сервера (сюда стучится скрапер).
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8080"))

# Путь к локальной базе подписчиков/команд на сервере-боте.
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
