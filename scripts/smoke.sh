#!/usr/bin/env bash
# smoke.sh — полный прогон pipeline в песочнице + проверка отчёта.
# Использование: make smoke   (или bash scripts/smoke.sh)
# Ожидает поднятую песочницу (make sandbox-up): serplux-sandbox на 127.0.0.1:8001.

set -euo pipefail

SANDBOX_SVC="${SANDBOX_SVC:-serplux-sandbox}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.sandbox.yml --profile sandbox"
BASE="${SANDBOX_URL:-http://127.0.0.1:8001}"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

echo "=== SERPlux sandbox smoke ==="

# 1) Контейнер жив?
docker compose ps --status running "$SANDBOX_SVC" | grep -q "$SANDBOX_SVC" \
    || log_fail "контейнер $SANDBOX_SVC не запущен (make sandbox-up)"
log_ok "контейнер запущен"

# 2) Health
for i in 1 2 3 4 5; do
    if python3 -c "import urllib.request; urllib.request.urlopen('$BASE/health', timeout=3)" 2>/dev/null; then
        break
    fi
    [ "$i" = 5 ] && log_fail "health недоступен: $BASE/health"
    sleep 2
done
log_ok "health OK"

# 3) Запуск прогона (bearer из webhook; секрет песочницы фиксированный)
RUN_RESPONSE=$(python3 - <<PY
import json, urllib.request
req = urllib.request.Request(
    "$BASE/run",
    data=json.dumps({"with_labels": True}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer sandbox-secret-not-production"},
)
print(urllib.request.urlopen(req, timeout=180).read().decode())
PY
)
echo "$RUN_RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("stats", d), ensure_ascii=False))'

# 4) Статус прогона: exit_code = 0
echo "$RUN_RESPONSE" | python3 -c '
import json, sys
d = json.load(sys.stdin)
exit_code = d.get("exit_code", 1)
sys.exit(0 if exit_code == 0 else 1)
' || log_fail "прогон вернул не exit_code=0"
log_ok "pipeline exit_code=0"

# 5) Проверка отчёта в контейнере: report.json существует и непустой
docker compose exec -T "$SANDBOX_SVC" python3 -c "
import json, sys
data = json.load(open('/app/data/report.json'))
assert data['rows_count'] > 0, 'пустой отчёт'
print(f\"report: {data['rows_count']} строк, date={data['date']}\")
"
log_ok "report.json валиден"

echo ""
log_ok "smoke пройден"
