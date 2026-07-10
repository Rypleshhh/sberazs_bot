# sberazs-bot

Личный Telegram-бот для мониторинга наличия топлива на АЗС через
неофициальный API sberazs.ru.

## Архитектура

Проект можно развернуть двумя способами:

### Вариант A (рекомендуется, если один сервер слабый): два сервера

- **Сервер-скрапер** (мощный, без доступа к Telegram API) — парсит
  sberazs.ru через Playwright/Chromium, хранит данные в SQLite, отдаёт
  их через маленькое HTTP API (`app/main_scraper.py`)
- **Сервер-бот** (слабый, 1 CPU / 1 GB, за tgproxy) — только
  Telegram-бот на aiogram, никакого браузера. Раз в
  `POLL_INTERVAL_SEC` опрашивает API скрапера за новыми данными и
  пересылает подписчикам (`app/light_bot.py`)

Так тяжёлый Chromium вообще не запускается на слабом сервере.

### Вариант B: всё на одном сервере (монорежим)

Использует `app/main.py`, `Dockerfile.monolith`,
`docker-compose.monolith.yml`. Подходит, если сервер достаточно
мощный (от ~2 GB RAM) и имеет доступ к Telegram API напрямую.

---

## Вариант A: установка на два сервера

### На мощном сервере (скрапер)

1. Установите Docker (см. раздел ниже, если ещё не установлен).

2. Клонируйте репозиторий:

   ```bash
   git clone https://github.com/ВАШ_ЮЗЕРНЕЙМ/ВАШ_РЕПО.git
   cd ВАШ_РЕПО
   ```

3. Настройте области:

   ```bash
   cp app/geoboxes_local.py.example app/geoboxes_local.py
   nano app/geoboxes_local.py
   ```

   ```python
   GEOBOXES = {
       "moscow": (37.3, 55.5, 37.9, 55.9),
   }
   ```

4. Настройте `.env.scraper`:

   ```bash
   cp .env.scraper.example .env.scraper
   nano .env.scraper
   ```

   Придумайте длинный случайный `API_TOKEN` — это общий секрет между
   двумя серверами (например: `openssl rand -hex 32`).

5. Запустите:

   ```bash
   docker compose -f docker-compose.scraper.yml up -d --build
   ```

6. **Ограничьте доступ к порту API** только IP лёгкого сервера
   (иначе кто угодно сможет читать ваши данные):

   ```bash
   ufw allow from IP_ЛЁГКОГО_СЕРВЕРА to any port 8080
   ufw deny 8080
   ```

   (если `ufw` ещё не установлен — `apt install -y ufw`, затем
   не забудьте разрешить SSH перед включением: `ufw allow OpenSSH`,
   потом `ufw enable`)

### На лёгком сервере (бот, с tgproxy)

1. Установите Docker.

2. Клонируйте тот же репозиторий:

   ```bash
   git clone https://github.com/ВАШ_ЮЗЕРНЕЙМ/ВАШ_РЕПО.git
   cd ВАШ_РЕПО
   ```

3. Настройте `.env.bot`:

   ```bash
   cp .env.bot.example .env.bot
   nano .env.bot
   ```

   Впишите `BOT_TOKEN`, тот же `API_TOKEN`, что и на скрапере, и
   `SCRAPER_API_URL=http://IP_МОЩНОГО_СЕРВЕРА:8080`.

4. Запустите:

   ```bash
   docker compose -f docker-compose.bot.yml up -d --build
   ```

   Этот образ маленький и лёгкий (`python:3.11-slim`, без браузера) —
   должен спокойно взлететь на 1 CPU / 1 GB даже вместе с tgproxy.

5. Проверка:

   ```bash
   docker compose -f docker-compose.bot.yml logs -f
   ```

   Найдите бота в Telegram, отправьте `/start`.

---

## Установка Docker с нуля (Ubuntu, на любом из серверов)

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
apt install -y docker-compose-plugin git
```

Проверка: `docker --version`, `docker compose version`.

---

## Команды бота

- `/scan_city <название>` — свежие станции по одному городу
- `/cities` — список доступных городов
- `/subscribe [город]` — подписаться на уведомления (без города — по
  всем областям). Присылаются только новые/изменившиеся оплаты
- `/unsubscribe` — отписаться от всех уведомлений
- `/my_subscriptions` — текущие подписки

В монорежиме (`app/bot.py`) дополнительно доступна `/scan` — сканирует
всё сразу, без разбивки по городам.

## Обслуживание

**Обновить после изменений в коде:**

```bash
git pull
docker compose -f docker-compose.scraper.yml up -d --build   # на мощном сервере
docker compose -f docker-compose.bot.yml up -d --build       # на лёгком сервере
```

**Логи:**

```bash
docker compose -f docker-compose.scraper.yml logs -f
docker compose -f docker-compose.bot.yml logs -f
```

## Важные нюансы

- **Первый скан каждой области** только наполняет базу, уведомления
  не рассылаются — иначе подписчики получат сразу весь массив данных.
- **Куки антибот-защиты** кэшируются в памяти процесса-скрапера и
  переполучаются только при ошибке 401/403 от API.
- `API_TOKEN` — обязательно длинный случайный, и порт API на скрапере
  должен быть закрыт файрволом от всех, кроме лёгкого сервера (см.
  выше про `ufw`). Без этого кто угодно из интернета сможет читать
  ваши данные и результаты подписок.
- Сервис sberazs.ru построен на данных банка — использование строго
  в личных целях, без публичного распространения результатов.
