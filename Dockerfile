# Готовый образ с уже установленными браузерами Playwright — избавляет
# от возни с системными зависимостями для headless Chromium в контейнере.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Директория для SQLite-базы — монтируется как volume в docker-compose
RUN mkdir -p /app/data

CMD ["python", "-m", "app.main"]
