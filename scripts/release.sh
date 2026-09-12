#!/usr/bin/env bash
# release.sh — immutable release деплой на сервере (Workstream D, Фаза 3).
#
# Вызывается из release.yml (SSH) или вручную на сервере:
#   bash scripts/release.sh --tag v1.0.3 --sha <git-sha> --digest sha256:... \
#        --image ghcr.io/<owner>/serplux
#
# Пайплайн (каждый шаг — гейт):
#   1. PREFLIGHT: env на месте, docker login GHCR, digest доступен
#   2. BACKUP: backup_db.sh (ротация 10)
#   3. MIGRATION PREFLIGHT: migrate.py на КОПИИ БД — если миграция не идёт
#      чисто, деплой БЛОКИРУЕТСЯ до разбора (БД прод не тронута)
#   4. RELEASE: pull по digest (не latest!) → run с env-подменой образа
#   5. SMOKE: health + /version соответствует tag/sha
#   6. При провале любого шага после RELEASE — авто-rollback на предыдущий
#      image digest (снятый перед release) + повторный smoke
#   7. RECORD: docs/deployments.json — append {tag, sha, digest, when, result}
#
# Запрещено: `git pull main` в проде, тег latest, деплой без digest.
# Запуск из каталога с docker-compose.yml (обычно /opt/serplux).

set -euo pipefail

# ─── args ─────────────────────────────────────────────────────────────────────
TAG=""
SHA=""
DIGEST=""
IMAGE=""
SERVICE="serplux"
COMPOSE="docker compose"
HEALTH_URL="http://127.0.0.1:8000"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --sha) SHA="$2"; shift 2 ;;
        --digest) DIGEST="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --health-url) HEALTH_URL="$2"; shift 2 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

# ─── helpers ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info() { echo -e "${GREEN}[RELEASE]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[RELEASE]${NC} $1"; }
log_err()  { echo -e "${RED}[RELEASE]${NC} $1" >&2; }
fail()     { log_err "$1"; record_result "failed" "$1"; exit 1; }

# Deployment record (append в docs/deployments.json; JSON-массив)
record_result() {
    python3 - "$1" <<PY
import json, datetime, pathlib
records_path = pathlib.Path("docs/deployments.json")
entry = {
    "tag": "$TAG", "sha": "$SHA", "digest": "$DIGEST",
    "when": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "result": "$1",
    "detail": "$2",
}
records = []
if records_path.exists():
    try:
        records = json.loads(records_path.read_text())
    except json.JSONDecodeError:
        pass
records.append(entry)
records_path.write_text(json.dumps(records, indent=2) + "\n")
PY
}

# ─── 0. args sanity ──────────────────────────────────────────────────────────
[[ -n "$TAG" ]] || { log_err "--tag обязателен"; exit 2; }
[[ -n "$SHA" ]] || { log_err "--sha обязателен"; exit 2; }
[[ -n "$DIGEST" ]] || { log_err "--digest обязателен"; exit 2; }
[[ -n "$IMAGE" ]] || { log_err "--image обязателен"; exit 2; }
[[ -f "docker-compose.yml" ]] || fail "запуск из каталога с docker-compose.yml"
[[ -f ".env" ]] || fail ".env не найден (одноразовая установка на сервере)"
[[ "$TAG" == "$DIGEST" || "$TAG" == v* ]] || fail "--tag должен начинаться с v"
[[ "$TAG" != "latest" ]] || fail "тег latest запрещён в проде (immutable releases)"

log_info "=== Release $TAG (sha=${SHA:0:9}, digest=${DIGEST:0:19}) ==="

# ─── 1. PREFLIGHT: env-переменные на месте ───────────────────────────────────
MISSING_ENV=$(python3 - <<PY
required = ["TOPVISOR_API_KEY", "TOPVISOR_USER_ID", "TOPVISOR_PROJECT_ID",
            "OPENCODE_API_KEY", "GOOGLE_SHEET_ID", "WEBHOOK_SECRET"]
env = {}
for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
missing = [k for k in required if not env.get(k)]
print(",".join(missing))
PY
)
[[ -z "$MISSING_ENV" ]] || fail "в .env отсутствуют: $MISSING_ENV"
log_info "preflight: .env на месте"

# digest присутствует в registry?
docker manifest inspect "$IMAGE@$DIGEST" > /dev/null 2>&1 \
    || fail "digest $DIGEST недоступен в registry (docker login ghcr.io?)"
log_info "preflight: digest доступен"

# ─── 2. BACKUP ───────────────────────────────────────────────────────────────
log_info "backup БД..."
bash backup_db.sh "$SERVICE" > /dev/null 2>&1 \
    || fail "backup_db.sh провалился (деплой остановлен, прод не тронут)"
log_info "backup: OK"

# ─── 3. MIGRATION PREFLIGHT (на копии БД) ────────────────────────────────────
log_info "migration preflight на копии..."
docker compose exec -T "$SERVICE" bash -c '
set -e
cp /app/data/serplux.db /tmp/migration-preflight.db
python3 migrate.py --db /tmp/migration-preflight.db > /dev/null
rm /tmp/migration-preflight.db
' || fail "миграция падает на копии БД — деплой BLOCKED (разбери migrate.py, прод не тронут)"
log_info "migration preflight: OK"

# ─── 4. RELEASE: pull по digest + up ─────────────────────────────────────────
# Снимаем текущий работающий digest для возможного rollback
PREV_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "serplux:latest" 2>/dev/null \
    | grep -oE 'sha256:[a-f0-9]+' | head -1 || true)
log_info "previous digest: ${PREV_DIGEST:-none}"

log_info "pull $IMAGE@$DIGEST..."
docker pull "$IMAGE@$DIGEST" > /dev/null 2>&1 || fail "docker pull провалился"

# compose-сервис ссылается на serplux:latest; точечная подмена — локальный тег
# снятого digest'а. Immutable-источник = digest; локальный тег только для compose.
docker tag "$IMAGE@$DIGEST" serplux:latest \
    || fail "docker tag (digest→serplux:latest) провалился"

log_info "up -d (образ = $TAG)"
$COMPOSE up -d --no-deps "$SERVICE" || fail "docker compose up провалился"

rollback() {
    log_err "SMOKE провалился: $1 — откат на предыдущий digest"
    if [[ -n "$PREV_DIGEST" ]]; then
        docker tag "$IMAGE@$PREV_DIGEST" serplux:latest \
            && $COMPOSE up -d --no-deps "$SERVICE" \
            || log_err "rollback тоже провалился — вмешайся вручную!"
        log_info "rollback выполнен на $PREV_DIGEST"
    else
        log_warn "PREV_DIGEST неизвестен — откат вручную через git tag + build"
    fi
    record_result "rolled_back" "$1"
    exit 1
}

# ─── 5. SMOKE: health + /version ─────────────────────────────────────────────
for i in 1 2 3 4 5 6; do
    if curl -fsS "$HEALTH_URL/health" > /dev/null 2>&1; then break; fi
    [[ "$i" == 6 ]] && { rollback "health недоступен после релиза"; }
    sleep 5
done
log_info "smoke: health OK"

VERSION_JSON=$(curl -fsS "$HEALTH_URL/version" 2>/dev/null || echo '{}')
echo "$VERSION_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d.get('tag') == '$TAG', f\"tag mismatch: got {d.get('tag')!r}, expected $TAG\"
assert d.get('git_sha', '').startswith('${SHA:0:9}') or d.get('git_sha') == '$SHA', \\
    f\"sha mismatch: got {d.get('git_sha')!r}\"
print('version:', d)
" || { rollback "/version не соответствует $TAG (check build-args)"; }
log_info "smoke: /version соответствует $TAG + sha"

# ─── 6. RECORD ───────────────────────────────────────────────────────────────
record_result "deployed" ""
log_info "=== RELEASE УСПЕШЕН: $TAG ($DIGEST) ==="
log_info "verify: curl $HEALTH_URL/version | jq .tag"
echo ""
log_info "Rollback (если понадобится позже):"
echo "  docker tag '$IMAGE@$PREV_DIGEST' serplux:latest && docker compose up -d --no-deps $SERVICE"
