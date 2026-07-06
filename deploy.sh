#!/usr/bin/env bash
# deploy.sh — автоматический деплой SERPlux на сервер
#
# Использование:
#   ./deploy.sh
#
# Скрипт выполняет:
# 1. Проверка наличия docker-compose.yml
# 2. git pull origin main
# 3. Бэкап БД (serplux.db.bak.YYYY-MM-DD-HHMMSS)
# 4. docker compose build
# 5. docker compose up -d
# 6. Health-check (поллинг до 3 раз)
# 7. Миграция БД (migrate.py — идемпотентный)
# 8. Финальный health-check
# 9. Итоговое сообщение
#
# Требования:
# - Запуск из каталога с docker-compose.yml
# - Пользователь в группе docker
# - .env, credentials.json, regions_map*.json на месте

set -euo pipefail

# Конфигурация
SERVICE="${SERVICE:-serplux}"
HEALTH_URL="http://127.0.0.1:8000/health"
HEALTH_RETRIES=3
HEALTH_DELAY=5

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка: запущен из каталога с docker-compose.yml
if [[ ! -f "docker-compose.yml" ]]; then
    log_error "Файл docker-compose.yml не найден в текущем каталоге"
    log_error "Запустите скрипт из корня проекта (где docker-compose.yml)"
    exit 1
fi

log_info "Начало деплоя сервиса: $SERVICE"
echo ""

# Шаг 1: git pull
log_info "Шаг 1/8: git pull origin main"
if git pull origin main; then
    log_info "Код обновлён из репозитория"
else
    log_error "Ошибка при git pull"
    exit 1
fi

# Получаем текущий коммит
COMMIT_HASH=$(git rev-parse --short HEAD)
log_info "Текущий коммит: $COMMIT_HASH"
echo ""

# Шаг 2: Бэкап БД
log_info "Шаг 2/8: Бэкап БД"
BACKUP_TIMESTAMP=$(date +%F-%H%M%S)
BACKUP_NAME="serplux.db.bak.${BACKUP_TIMESTAMP}"

if docker compose exec -T "$SERVICE" test -f /app/data/serplux.db; then
    log_info "БД существует, создаю бэкап: $BACKUP_NAME"
    if docker compose exec -T "$SERVICE" cp /app/data/serplux.db "/app/data/$BACKUP_NAME"; then
        log_info "Бэкап создан: /app/data/$BACKUP_NAME"
    else
        log_error "Ошибка при создании бэкапа"
        exit 1
    fi
else
    log_warn "БД не найдена (первый запуск?), пропускаю бэкап"
fi
echo ""

# Шаг 3: docker compose build
log_info "Шаг 3/8: docker compose build"
if docker compose build; then
    log_info "Образ собран успешно"
else
    log_error "Ошибка при сборке образа"
    exit 1
fi
echo ""

# Шаг 4: docker compose up -d
log_info "Шаг 4/8: docker compose up -d"
if docker compose up -d; then
    log_info "Контейнер запущен"
else
    log_error "Ошибка при запуске контейнера"
    exit 1
fi
echo ""

# Шаг 5: Health-check (поллинг)
log_info "Шаг 5/8: Health-check (поллинг до $HEALTH_RETRIES попыток)"
HEALTH_OK=false

for i in $(seq 1 $HEALTH_RETRIES); do
    log_info "Попытка $i/$HEALTH_RETRIES..."
    sleep "$HEALTH_DELAY"
    
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        log_info "Health-check пройден"
        HEALTH_OK=true
        break
    else
        log_warn "Health-check не пройден"
    fi
done

if [[ "$HEALTH_OK" != "true" ]]; then
    log_error "Контейнер не поднялся за $((HEALTH_RETRIES * HEALTH_DELAY)) секунд"
    log_error "Последние логи контейнера:"
    echo ""
    docker compose logs --tail 50 "$SERVICE"
    log_error "Миграция НЕ выполнена. Проверьте логи и исправьте ошибки."
    exit 1
fi
echo ""

# Шаг 6: Миграция БД
log_info "Шаг 6/8: Миграция БД (migrate.py)"
log_info "migrate.py идемпотентный — безопасен для повторного запуска"

if docker compose exec -T "$SERVICE" python migrate.py --db /app/data/serplux.db; then
    log_info "Миграция выполнена успешно"
else
    log_error "Ошибка при миграции БД"
    log_error "Бэкап доступен: /app/data/$BACKUP_NAME"
    exit 1
fi
echo ""

# Шаг 7: Финальный health-check
log_info "Шаг 7/8: Финальный health-check"
if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    log_info "Финальный health-check пройден"
else
    log_error "Финальный health-check не пройден"
    docker compose logs --tail 30 "$SERVICE"
    exit 1
fi
echo ""

# Шаг 8: Итоговое сообщение
log_info "Шаг 8/8: Итог"
echo ""
echo "=========================================="
echo -e "${GREEN}Деплой завершён успешно!${NC}"
echo "=========================================="
echo "Сервис: $SERVICE"
echo "Коммит: $COMMIT_HASH"
echo "Health: OK"
if [[ -n "${BACKUP_NAME:-}" ]]; then
    echo "Бэкап БД: /app/data/$BACKUP_NAME"
fi
echo ""
log_info "Проверка статуса:"
echo "  curl http://localhost:8000/health"
echo "  source .env && curl -H \"Authorization: Bearer \$WEBHOOK_SECRET\" http://localhost:8000/status"
echo ""
log_info "Последние логи:"
docker compose logs --tail 10 "$SERVICE"
