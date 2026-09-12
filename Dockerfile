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

# Immutable release-идентификация (Workstream D): release.yml передаёт их
# build-args, /version отдаёт в runtime для smoke-гейта release.sh.
ARG GIT_SHA=""
ARG RELEASE_TAG=""
ARG IMAGE_DIGEST=""
ARG BUILT_AT=""

WORKDIR /build

# Устанавливаем зависимости (включая dev: pytest для verify.sh)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements-dev.txt

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Не запускаем от root
RUN groupadd --gid 1001 serplux && \
    useradd --uid 1001 --gid serplux --no-create-home --shell /sbin/nologin serplux

# Пробрасываем build-args в env финального образа (webhook.py /version их отдаёт)
ARG GIT_SHA=""
ARG RELEASE_TAG=""
ARG IMAGE_DIGEST=""
ARG BUILT_AT=""
ENV GIT_SHA=${GIT_SHA} \
    RELEASE_TAG=${RELEASE_TAG} \
    IMAGE_DIGEST=${IMAGE_DIGEST} \
    BUILT_AT=${BUILT_AT}

WORKDIR /app

# Копируем установленные пакеты из builder
COPY --from=builder /install /usr/local

# Копируем все модули корня по glob (новый .py-модуль попадает в образ
# автоматически; .dockerignore исключает venv/.git/секреты/артефакты)
COPY --chown=serplux:serplux *.py ./
COPY --chown=serplux:serplux template/ ./template/

# Копируем тесты и конфиг pytest для verify.sh
COPY --chown=serplux:serplux tests/ ./tests/
COPY --chown=serplux:serplux pyproject.toml ./

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
