# Makefile — SERPlux: песочница, тесты, smoke, релиз (см. docs/serpctl.md).
#
# Быстрый старт:
#   make sandbox-up      # поднять песочницу (моки + seed + serplux)
#   make smoke           # прогнать pipeline в песочнице и проверить отчёт
#   make sandbox-down    # остановить и убрать контейнеры
#   make test            # pytest локально (PYTHONPATH venv)
#   make test-docker     # pytest в контейнере (как verify.sh)
#   make build           # собрать образ serplux:latest
#
# Секреты для песочницы не нужны. Для prod-деплоя см. docs/deploy.md.

COMPOSE      = docker compose -f docker-compose.yml -f docker-compose.sandbox.yml --profile sandbox
SANDBOX_SVC  = serplux-sandbox
SANDBOX_URL  = http://127.0.0.1:8001
PYTEST       = PYTHONPATH=venv/lib/python3.14/site-packages python3 -m pytest -q

.PHONY: help build sandbox-up sandbox-down sandbox-logs seed test test-docker smoke ps clean verify

help:
	@echo "SERPlux Makefile targets:"
	@echo "  make build         - собрать образ serplux:latest"
	@echo "  make sandbox-up    - поднять песочницу (моки + seed + serplux)"
	@echo "  make sandbox-down  - остановить песочницу (контейнеры+volumes не удаляются)"
	@echo "  make sandbox-logs  - логи песочницы (follow)"
	@echo "  make ps            - статус контейнеров"
	@echo "  make test          - pytest локально (PYTHONPATH venv)"
	@echo "  make test-docker   - pytest внутри контейнера serplux"
	@echo "  make smoke         - полный прогон pipeline в песочнице + проверка отчёта"
	@echo "  make verify        - ./verify.sh (серверная верификация, требует контейнер)"
	@echo "  make clean         - удалить sandbox/generated/*.db (seed создаст заново)"

build:
	docker build -t serplux:latest .

sandbox-up:
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Песочница поднимается. Health: $(SANDBOX_URL)/health"
	@echo "Smoke: make smoke"

sandbox-down:
	$(COMPOSE) --profile sandbox down

sandbox-logs:
	$(COMPOSE) logs -f $(SANDBOX_SVC)

ps:
	$(COMPOSE) ps

test:
	$(PYTEST)

test-docker:
	docker compose exec -T $(SANDBOX_SVC) python -m pytest -q -p no:cacheprovider --tb=short

smoke:
	@bash scripts/smoke.sh

verify:
	./verify.sh $(SANDBOX_SVC)

clean:
	PYTHONPATH=venv/lib/python3.14/site-packages python3 -c "from pathlib import Path; [p.unlink() for p in Path('sandbox/generated').glob('*.db*')]; print('sandbox/generated cleared')"
