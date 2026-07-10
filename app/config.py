import os

BOT_TOKEN = os.environ["BOT_TOKEN"]  # обязателен, иначе падаем сразу при старте

# Интервал фонового обновления данных, минуты.
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "12"))

# Не уведомлять про оплаты старше этого порога (минуты) — защита от
# "спама" старыми данными при первом запуске / рестарте бота.
MAX_PAYMENT_AGE_MINUTES = int(os.environ.get("MAX_PAYMENT_AGE_MINUTES", "20"))

# Шаг сетки в градусах при обходе bbox (подберите под плотность станций
# в ваших областях — см. историю подбора в предыдущем скрипте).
STEP_LON = float(os.environ.get("STEP_LON", "0.3"))
STEP_LAT = float(os.environ.get("STEP_LAT", "0.2"))

# Пауза между запросами к API, секунды.
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "0.4"))

# Путь к SQLite-базе внутри контейнера (монтируется volume-ом наружу).
DB_PATH = os.environ.get("DB_PATH", "/app/data/stations.db")

# ---------------------------------------------------------------------
# GEOBOXES: ваши области. "all" — используется командой /scan (полный
# обход всех настроенных областей). Остальные ключи — для /scan_city.
#
# Реальные координаты вынесены в app/geoboxes_local.py — этот файл в
# .gitignore и никогда не попадёт в публичный репозиторий. Здесь только
# заглушка на случай, если локальный файл ещё не создан.
# ---------------------------------------------------------------------
try:
    from app.geoboxes_local import GEOBOXES  # noqa: F401
except ImportError:
    GEOBOXES: dict[str, tuple[float, float, float, float]] = {}
