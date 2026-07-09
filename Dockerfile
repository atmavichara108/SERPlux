# Dockerfile — SERPlux webhook сервис
#
# Многоэтапная сборка: builder устанавливает зависимости,
# runtime — минимальный образ без лишних инструментов.
#
# Сборка:
#   docker build -t serplux:latest .
#
# Запуск (для разработки):
#   docker run --env-file .env -v $(pwd)/credentials.json:/app/credentials.json:ro \
#              -v serplux_data:/app/data -p 8000:8000 serplux:latest

# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Устанавливаем зависимости (включая dev: pytest для verify.sh)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements-dev.txt

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Не запускаем от root
RUN groupadd --gid 1001 serplux && \
    useradd --uid 1001 --gid serplux --no-create-home --shell /sbin/nologin serplux

WORKDIR /app

# Копируем установленные пакеты из builder
COPY --from=builder /install /usr/local

# Копируем только исходный код (без .env, credentials.json, venv, БД)
COPY --chown=serplux:serplux \
    main.py \
    topvisor.py \
    collector.py \
    labeler.py \
    storage.py \
    exporter.py \
    reporter.py \
    config.py \
    webhook.py \
    migrate.py \
    ./

# Копируем карты регионов
COPY --chown=serplux:serplux regions_map*.json ./

# Каталог для SQLite БД — монтируется как volume
RUN mkdir -p /app/data && chown serplux:serplux /app/data

# Переменные окружения по умолчанию (переопределяются через --env-file или docker-compose)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEBHOOK_HOST=0.0.0.0 \
    WEBHOOK_PORT=8000 \
    DB_PATH=/app/data/serplux.db

USER serplux

EXPOSE 8000

# Health-check: GET /health каждые 30 сек
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["python", "-m", "uvicorn", "webhook:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
