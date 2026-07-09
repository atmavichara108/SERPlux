#!/bin/bash
# verify.sh — автоматизированная верификация после deploy.sh
# Запуск: ./verify.sh [SERVICE=serplux]
# Проверяет: тесты, health, контейнер, логи, схема БД, целостность данных

set -euo pipefail

SERVICE="${SERVICE:-serplux}"
CHECKS_PASSED=0
CHECKS_TOTAL=6
WARNINGS=0

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_check() {
    local name="$1"
    local status="$2"
    if [ "$status" = "pass" ]; then
        echo -e "${GREEN}✓${NC} $name"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))
    elif [ "$status" = "warn" ]; then
        echo -e "${YELLOW}⚠${NC} $name (warning)"
        WARNINGS=$((WARNINGS + 1))
    else
        echo -e "${RED}✗${NC} $name"
    fi
}

log_error() {
    echo -e "${RED}Error:${NC} $1" >&2
}

log_detail() {
    printf '%s\n' "  $1"
}

echo "=== SERPlux Verification ($SERVICE) ==="
echo ""

# a) Тесты
echo "[1/6] Running tests..."

# Проверяем, что pytest доступен в контейнере
if ! docker compose exec -T "$SERVICE" python -m pytest --version > /dev/null 2>&1; then
    log_check "Tests" "fail"
    log_detail "pytest not installed in container. Rebuild image with requirements-dev.txt"
    exit 1
fi

# Запускаем pytest с отключённым кэшем (нет прав на запись в /app) и коротким tb
set +e
docker compose exec -T "$SERVICE" python -m pytest -q -p no:cacheprovider --tb=short 2>&1 | tee /tmp/pytest_output.txt
PYTEST_EXIT=${PIPESTATUS[0]}
set -e
PASSED=$(grep -oE '[0-9]+ passed' /tmp/pytest_output.txt | grep -oE '[0-9]+' | tail -1)

if [ "$PYTEST_EXIT" -eq 0 ]; then
    log_check "Tests" "pass"
    log_detail "$PASSED tests passed"
else
    log_check "Tests" "fail"
    log_detail "pytest exit code: $PYTEST_EXIT"
    exit 1
fi
echo ""

# b) Health check
echo "[2/6] Health check..."
if curl -sf http://127.0.0.1:8000/health > /tmp/health.json 2>&1; then
    STATUS=$(grep -o '"status":"[^"]*"' /tmp/health.json | cut -d'"' -f4)
    SERVICE_NAME=$(grep -o '"service":"[^"]*"' /tmp/health.json | cut -d'"' -f4)
    log_check "Health endpoint" "pass"
    log_detail "status=$STATUS, service=$SERVICE_NAME"
else
    log_check "Health endpoint" "fail"
    log_detail "curl failed or endpoint unreachable"
    exit 1
fi
echo ""

# c) Container status
echo "[3/6] Container status..."

# Проверяем статус без jq (jq может не быть на сервере; формат JSON различается между версиями compose)
# Используем текстовый вывод docker compose ps и ищем наш сервис + статус Up/running/healthy
PS_OUTPUT=$(docker compose ps "$SERVICE" 2>&1)
if echo "$PS_OUTPUT" | grep -E "^[[:space:]]*$SERVICE" | grep -qiE "Up|running|healthy"; then
    log_check "Container running" "pass"
    log_detail "container is up and healthy"
else
    log_check "Container running" "fail"
    log_detail "$PS_OUTPUT"
    exit 1
fi
echo ""

# d) Логи на ошибки
echo "[4/6] Checking logs for errors..."

# Собираем логи и считаем ошибки. Grep без совпадений возвращает 1 — подавляем,
# чтобы set -e не убил скрипт.
LOG_SNAPSHOT=$(docker compose logs --tail 100 "$SERVICE" 2>&1)
ERROR_COUNT=$(echo "$LOG_SNAPSHOT" | { grep -iE "error|traceback|exception|fatal" || true; } | wc -l)
if [ "$ERROR_COUNT" -gt 0 ]; then
    log_check "Error logs" "warn"
    log_detail "Found $ERROR_COUNT error/exception mentions in logs (not necessarily fatal)"
    echo "$LOG_SNAPSHOT" | { grep -iE "error|traceback|exception|fatal" || true; } | head -5
else
    log_check "Error logs" "pass"
    log_detail "No obvious errors in recent logs"
fi
echo ""

# e) Схема БД
echo "[5/6] Database schema..."

set +e
SCHEMA_CHECK=$(docker compose exec -T "$SERVICE" python3 -c "
import sqlite3
import sys

db_path = '/app/data/serplux.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Проверяем таблицы
required_tables = ['clients', 'positions', 'labels', 'domain_labels']
cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
existing_tables = set(row[0] for row in cursor.fetchall())

missing_tables = [t for t in required_tables if t not in existing_tables]
if missing_tables:
    print(f'Missing tables: {missing_tables}')
    sys.exit(1)

# Проверяем колонки в clients
cursor.execute(\"PRAGMA table_info(clients)\")
columns = set(row[1] for row in cursor.fetchall())
required_cols = ['client_id', 'client_name', 'queries', 'regions_map', 'searchers', 'project_id']
missing_cols = [c for c in required_cols if c not in columns]
if missing_cols:
    print(f'Missing columns in clients: {missing_cols}')
    sys.exit(1)

print('OK')
conn.close()
" 2>&1)
SCHEMA_EXIT=$?
set -e

if [ "$SCHEMA_EXIT" -eq 0 ] && [ "$SCHEMA_CHECK" = "OK" ]; then
    log_check "Database schema" "pass"
    log_detail "All required tables and columns present"
else
    log_check "Database schema" "fail"
    log_detail "$SCHEMA_CHECK"
    exit 1
fi
echo ""

# f) Целостность — осиротевшие записи
echo "[6/6] Data integrity..."

set +e
ORPHANS=$(docker compose exec -T "$SERVICE" python3 -c "
import sqlite3

db_path = '/app/data/serplux.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Проверяем позиции без client_id в clients
cursor.execute('''
    SELECT COUNT(*) FROM positions p
    LEFT JOIN clients c ON p.client_id = c.client_id
    WHERE c.client_id IS NULL AND p.client_id IS NOT NULL
''')
orphan_positions = cursor.fetchone()[0]

# Проверяем метки без client_id
cursor.execute('''
    SELECT COUNT(*) FROM labels l
    LEFT JOIN clients c ON l.client_id = c.client_id
    WHERE c.client_id IS NULL AND l.client_id IS NOT NULL
''')
orphan_labels = cursor.fetchone()[0]

conn.close()

print(f'{orphan_positions},{orphan_labels}')
" 2>&1)
ORPHANS_EXIT=$?
set -e

if [ "$ORPHANS_EXIT" -ne 0 ]; then
    log_check "Data integrity" "fail"
    log_detail "Failed to query database: $ORPHANS"
    exit 1
fi

ORPHAN_POS=$(echo "$ORPHANS" | cut -d',' -f1)
ORPHAN_LABELS=$(echo "$ORPHANS" | cut -d',' -f2)

if [ "$ORPHAN_POS" -eq 0 ] && [ "$ORPHAN_LABELS" -eq 0 ]; then
    log_check "Data integrity" "pass"
    log_detail "No orphaned records found"
else
    log_check "Data integrity" "fail"
    log_detail "Orphaned positions: $ORPHAN_POS, labels: $ORPHAN_LABELS"
    exit 1
fi
echo ""

# Итоговая сводка
echo "=== Summary ==="
echo "Checks: $CHECKS_PASSED/$CHECKS_TOTAL passed"
if [ "$WARNINGS" -gt 0 ]; then
    echo "Warnings: $WARNINGS (check logs)"
fi

if [ "$CHECKS_PASSED" -eq "$CHECKS_TOTAL" ]; then
    echo -e "${GREEN}✓ Verification passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Verification failed${NC}"
    exit 1
fi
