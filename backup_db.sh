#!/bin/bash
# backup_db.sh — создание бэкапа БД с ротацией последних 10
# Запуск: ./backup_db.sh [SERVICE=serplux] [DB_PATH=/app/data/serplux.db]

set -euo pipefail

SERVICE="${SERVICE:-serplux}"
DB_PATH="${DB_PATH:-/app/data/serplux.db}"
BACKUP_DIR=$(dirname "$DB_PATH")
DB_NAME=$(basename "$DB_PATH")
MAX_BACKUPS=10

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}ℹ${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

echo "=== Database Backup ($SERVICE) ==="
echo ""

# Генерируем имя бэкапа с временной меткой
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
BACKUP_FILE="$BACKUP_DIR/$DB_NAME.bak.$TIMESTAMP"

log_info "Creating backup: $BACKUP_FILE"

# Создаём бэкап через docker compose
if docker compose exec -T "$SERVICE" cp "$DB_PATH" "$BACKUP_FILE"; then
    log_info "Backup created successfully"
    log_info "Path: $BACKUP_FILE"
else
    log_error "Failed to create backup"
    exit 1
fi

echo ""
log_info "Checking backup integrity..."

# Проверяем, что бэкап не пустой и валидный SQLite
set +e
VERIFY_OUTPUT=$(docker compose exec -T "$SERVICE" python3 -c "
import sqlite3
import os

backup = '$BACKUP_FILE'
if not os.path.exists(backup) or os.path.getsize(backup) == 0:
    print('Backup file is empty')
    exit(1)

try:
    conn = sqlite3.connect(backup)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM sqlite_master')
    count = cursor.fetchone()[0]
    conn.close()
    if count == 0:
        print('Backup appears to be empty')
        exit(1)
    print('OK')
except Exception as e:
    print(f'Invalid SQLite database: {e}')
    exit(1)
" 2>&1)
VERIFY_EXIT=$?
set -e

if [ "$VERIFY_EXIT" -eq 0 ] && [ "$VERIFY_OUTPUT" = "OK" ]; then
    log_info "Backup integrity verified"
else
    log_error "Backup verification failed: $VERIFY_OUTPUT"
    exit 1
fi

echo ""
log_info "Rotating old backups (keeping last $MAX_BACKUPS)..."

# Находим все бэкапы и оставляем только последние MAX_BACKUPS
BACKUP_COUNT=$(docker compose exec -T "$SERVICE" bash -c "
    ls -1 '$BACKUP_DIR/$DB_NAME.bak.'* 2>/dev/null | wc -l
" || echo "0")

if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    # Удаляем старые (оставляем последние MAX_BACKUPS)
    docker compose exec -T "$SERVICE" bash -c "
        ls -1tr '$BACKUP_DIR/$DB_NAME.bak.'* 2>/dev/null | head -n -$MAX_BACKUPS | xargs rm -f
    " || true
    log_info "Removed old backups (kept last $MAX_BACKUPS)"
fi

# Выводим список текущих бэкапов
echo ""
log_info "Current backups:"
docker compose exec -T "$SERVICE" bash -c "
    ls -lh '$BACKUP_DIR/$DB_NAME.bak.'* 2>/dev/null | awk '{print \"  \" \$9 \" (\" \$5 \")\"}' | tail -$MAX_BACKUPS
" || true

echo ""
echo -e "${GREEN}✓ Backup completed${NC}"
