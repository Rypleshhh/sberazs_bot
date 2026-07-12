# sberazs-bot

Личный Telegram-бот для мониторинга наличия топлива на АЗС через
неофициальный API sberazs.ru.

## Архитектура

Мощный сервер (парсит) находится **за NAT** и не может принимать
входящие соединения. Лёгкий арендованный сервер (с ботом) имеет белый
IP. Поэтому направление связи развёрнуто: **скрапер сам стучится к
боту**, а не наоборот.

- **Сервер-скрапер** (мощный, за NAT) — парсит sberazs.ru через
  Playwright/Chromium, хранит данные локально, и сам:
  - раз в `SCAN_INTERVAL_MINUTES` пушит найденные обновления на
    `POST {LIGHT_SERVER_URL}/notifications`
  - раз в `COMMAND_POLL_INTERVAL_SEC` спрашивает
    `GET {LIGHT_SERVER_URL}/commands` — нет ли новых запросов от
    пользователей (`/scan_city`), и если есть — сканирует и отправляет
    результат через `POST {LIGHT_SERVER_URL}/command-result`

- **Сервер-бот** (лёгкий, белый IP, за tgproxy) — Telegram-бот на
  aiogram, без Chromium вообще. Держит небольшое HTTP API, в которое
  стучится скрапер, и просто передаёт данные в Telegram.

Так лёгкому серверу не нужно ничего забирать самому — он только
слушает и отвечает, а вся инициатива (и вся тяжёлая работа) — на
стороне мощного сервера.

### Альтернатива: всё на одном сервере (монорежим)

Если когда-нибудь понадобится один сервер помощнее без NAT —
`app/main.py`, `Dockerfile.monolith`, `docker-compose.monolith.yml`.

---

## Установка

### 1. На лёгком сервере (белый IP, с tgproxy) — разворачиваем первым

Ему нужно начать слушать раньше, чем скрапер попробует достучаться.

```bash
git clone https://github.com/Rypleshhh/sberazs_bot
cd sberazs_bot

cp .env.bot.example .env.bot
nano .env.bot
```

Впишите `BOT_TOKEN` (от [@BotFather](https://t.me/BotFather)) и
придумайте `API_TOKEN` — длинную случайную строку (например,
`openssl rand -hex 32`), общий секрет с мощным сервером.

```bash
docker compose -f docker-compose.bot.yml up -d --build
```

Проверка:

```bash
docker compose -f docker-compose.bot.yml logs -f
curl http://localhost:8080/health
```

Должно ответить `{"status": "ok"}`.

**Откройте порт 8080 в файрволе этого сервера**, если он не открыт по
умолчанию:

```bash
apt install -y ufw   # если ещё не установлен
ufw allow OpenSSH
ufw allow 8080
ufw enable
```

### 2. На мощном сервере (за NAT) — разворачиваем вторым

```bash
git clone https://github.com/Rypleshhh/sberazs_bot
cd sberazs_bot

cp app/geoboxes_local.py.example app/geoboxes_local.py
nano app/geoboxes_local.py
```

```python
GEOBOXES = {
    "moscow": (37.3, 55.5, 37.9, 55.9),
}
```
Получить можно на сайте bboxfinder.com/geojson.io
```bash
cp .env.scraper.example .env.scraper
nano .env.scraper
```

Впишите:
- **тот же** `API_TOKEN`, что и на лёгком сервере
- `LIGHT_SERVER_URL=http://БЕЛЫЙ_IP_ЛЁГКОГО_СЕРВЕРА:8080`

```bash
docker compose -f docker-compose.scraper.yml up -d --build
```

Портов наружу открывать не нужно — этот сервер только сам ходит
наружу (к sberazs.ru и к лёгкому серверу), входящие соединения ему не
требуются, поэтому NAT ему не мешает.

Проверка:

```bash
docker compose -f docker-compose.scraper.yml logs -f
```

Ищите строки вида `Опрос команд запущен` и `Сканирую область`, без
трейсбеков вида `Connection refused`/`unauthorized` (это значило бы,
что `LIGHT_SERVER_URL` или `API_TOKEN` не совпадают).

### 3. Проверка в Telegram

Отправьте боту `/start`, затем `/scan_city moscow` (или ваш город) —
ответ придёт не мгновенно, а после того как скрапер заберёт команду
на очередном опросе (`COMMAND_POLL_INTERVAL_SEC`, по умолчанию 5 сек)
и просканирует область.

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

- `/scan_city <название>` — запросить свежие станции по городу (ответ
  придёт асинхронно, когда скрапер обработает запрос)
- `/subscribe [город]` — подписаться на уведомления (без города — по
  всем областям). Присылаются только новые/изменившиеся оплаты
- `/unsubscribe` — отписаться от всех уведомлений
- `/my_subscriptions` — текущие подписки

## Обслуживание

**Обновить после изменений в коде:**

```bash
git pull
# на лёгком сервере:
docker compose -f docker-compose.bot.yml up -d --build
# на мощном сервере:
docker compose -f docker-compose.scraper.yml up -d --build
```

**Логи:**

```bash
docker compose -f docker-compose.bot.yml logs -f       # лёгкий сервер
docker compose -f docker-compose.scraper.yml logs -f   # мощный сервер
```

## Важные нюансы

- **Порядок запуска важен**: сначала лёгкий сервер (он слушает), потом
  мощный (он стучится) — иначе скрапер первое время будет получать
  ошибки соединения, пока не поднимется цель.
- **Первый скан каждой области** только наполняет базу и не пушит
  уведомления — эта защита в `storage.py` (`is_first_scan`), она общая
  для всех режимов, отдельно ничего настраивать не нужно.
- `API_TOKEN` обязателен и должен быть длинным случайным — порт 8080
  на лёгком сервере открыт всему интернету, без токена кто угодно
  сможет слать фейковые команды/уведомления через ваш бот.
- Сервис sberazs.ru построен на данных банка — использование строго
  в личных целях, без публичного распространения результатов.
