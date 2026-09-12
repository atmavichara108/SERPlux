# serpctl — кабина SERPlux: песочница, тесты, релиз, деплой

> Детерминированный cockpit для разработки SERPlux из OpenCode TUI (или руками).
> Все команды — JSON на stdout; секреты не печатаются.
> Источник правды по архитектуре release: `docs/specs/serp-factory-v1.1-roadmap.md` Workstream D.

## Быстрый старт (полный локальный цикл без внешних ключей)

```bash
make sandbox-up      # поднять песочницу: моки Topvisor/LLM + seed + serplux (порт 8001)
make smoke           # полный прогон pipeline на фикстурах + проверка отчёта
make sandbox-down    # остановить
```

Первый `sandbox-up` собирает образы (серплux + моки), поднимает seed-контейнер
(клиент + эталон из `sandbox/fixtures/`) и serplux-sandbox на `127.0.0.1:8001`.
После подъёма можно дёргать API вручную:

```bash
python3 scripts/serpctl.py health
python3 scripts/serpctl.py status
python3 scripts/serpctl.py smoke     # или make smoke
```

`export.json`/`report.json` появляются в `sandbox/generated/` (в контейнере —
`/app/data/`, монтируется в тот же volume).

## serpctl — справочник команд

```bash
python3 scripts/serpctl.py <cmd> [--sandbox-port 8001]
```

| Команда | Что делает |
|---------|-----------|
| `sandbox up` | docker compose (профиль sandbox): build+seed+up; первый запуск ~2 мин |
| `sandbox down` | остановить песочницу |
| `sandbox ps` / `sandbox logs` | статус/логи контейнеров |
| `health` | GET `/health` песочницы (без авторизации) |
| `status` | GET `/status` с bearer из `.env` (секрет не печатается) |
| `test` | pytest локально (PYTHONPATH venv авто-прописывается) |
| `test-docker` | pytest в контейнере (как verify.sh) |
| `db stats` | счётчики таблиц песочницы (clients/positions/labels/domain_labels) |
| `db backup` | обёртка backup_db.sh (ротация 10) |
| `smoke` | полный прогон pipeline в песочнице + проверка отчёта |
| `nvim <file>` | открыть файл в tmux-окне pb-SERPlux (ручная правка) |
| `release <tag>` | **RUN GATE**: тег+пуш → автодеплой; требует `--yes` (см. ниже) |
| `deploy-status` | последний deployment record (tag/sha/digest/result) |

## Песочница: как это устроено

- **мок Topvisor** (`sandbox/mock_topvisor.py`, порт 8011): детерминированные
  ответы из `sandbox/fixtures/snapshot.json` — форма эндпоинтов сверена с
  `topvisor.py` (projects/checker/go/snapshot/keywords).
- **мок LLM** (`sandbox/mock_llm.py`, 8012): OpenAI-compatible
  `/v1/chat/completions`, метки по доменам из `sandbox/fixtures/labels.json`
  (positive/negative/neutral).
- **seed** (`sandbox/seed.py`): тот же schema (`storage._init_db`), клиент
  `default` с субъектами из fixture, эталон `domain_labels` (source=manual_l1).
- **SANDBOX_MODE=1**: `exporter.export()`/`reporter.build_report()` пишут JSON
  вместо Google Sheets (пути: `SANDBOX_EXPORT_PATH`/`SANDBOX_REPORT_PATH`).
- **DI**: `TOPVISOR_API_BASE` (мок Topvisor), `LLM_API_BASE` (мок LLM) —
  без env работают канонические URL (прод не затронут).
- Фикстуры правятся в `sandbox/fixtures/*.json`; новый тест-кейс = новая запись
  в `keywords` (каждая позиция: `[pos, url, domain, title, body]`).

Прогон локально без docker (быстрая отладка pipeline):

```bash
python3 sandbox/mock_topvisor.py & python3 sandbox/mock_llm.py &
PYTHONPATH=venv/lib/python3.14/site-packages python3 sandbox/seed.py
SANDBOX_MODE=1 TOPVISOR_API_BASE=http://127.0.0.1:8011/v2/json \
LLM_API_BASE=http://127.0.0.1:8012/v1/chat/completions \
OPENCODE_API_KEY=fake DB_PATH=sandbox/generated/serplux.db \
python3 -c "import main; main.run({'client_id':'default','date':'<today>'})"
```

## Release (автодеплой по тегу)

Пайплайн: `serpctl release v1.0.3 --yes` → тег + пуш → **Actions workflow
Release** → pytest+shellcheck+ruff → buildx `ghcr.io/<owner>/serplux:{tag,tag-sha}`
(build-args GIT_SHA/RELEASE_TAG) → SSH → `scripts/release.sh`:

1. preflight: `.env` на месте, digest доступен в registry
2. backup БД (ротация 10)
3. **migration preflight на копии** — миграция не идёт чисто → BLOCKED, прод не тронут
4. pull по digest → локальный тег → `compose up`
5. smoke: health + `/version` соответствует tag+sha
6. провал любого шага после release → авто-rollback на предыдущий digest
7. запись в `docs/deployments.json`

```bash
python3 scripts/serpctl.py release vX.Y.Z --dry-run   # показать шаги, ничего не менять
python3 scripts/serpctl.py release vX.Y.Z --yes       # РЕАЛЬНЫЙ деплой в прод
python3 scripts/serpctl.py deploy-status             # последний record
```

Гейты release: чистое дерево, тег начинается с `v`, тег не существует, явный
`--yes`. Агент не делает push сам — только через serpctl после твоего подтверждения.

### Одноразовая настройка (вносит владелец)

1. GitHub repo → Settings → Secrets → Actions:
   - `SSH_HOST` — ip/домен сервера
   - `SSH_USER` — пользователь деплоя (в группе docker)
   - `SSH_PRIVATE_KEY` — приватный ключ deploy-ключа (публичный — на сервер)
2. На сервере: `docker login ghcr.io` (PAT с read:packages) — раз.
3. GitHub repo → Settings → Branches → protection main: require CI (Tests);
   запрет force-push. (вручную, 2 галочки)
4. Сервер-каталог: `/opt/serplux` (клонируешь репо, `.env`+`credentials.json`
   туда же — как раньше), скрипт `scripts/release.sh` из репо.

## Что где живёт

```
sandbox/                      # песочница (моки+seed, fixture'ы)
  mock_topvisor.py            # мок Topvisor API (stdlib http.server)
  mock_llm.py                 # мок LLM (OpenAI-compatible)
  seed.py                     # детерминированный seed БД песочницы
  fixtures/snapshot.json      # выдача: 2 keywords × позиции (google+yandex)
  fixtures/labels.json        # эталон меток: positives/negatives/neutral
  generated/                  # export.json / report.json / serplux.db (gitignored)
scripts/
  serpctl.py                  # cockpit (JSON-вывод, секретов не печатает)
  smoke.sh                    # полный прогон pipeline в песочнице
  release.sh                  # серверный деплой (preflight/backup/rollback)
docker-compose.sandbox.yml    # профиль sandbox (override основного compose)
sandbox/Dockerfile.mock       # образ моков (stdlib-only)
.env.sandbox.example          # документация: песочница не требует секретов
.github/workflows/release.yml # пуш тега v* → build+push GHCR → SSH → release.sh
```

## Отладка частых проблем

- **sandbox не поднимается** → `make ps` + `make sandbox-logs`; чаще всего
  образ не собрался (`make build` отдельно).
- **health unreachable** → подожди 10-15 сек (start_period), проверь порт
  `--sandbox-port`; порт в compose-файле `127.0.0.1:8001:8000`.
- **seed: database locked** → другой процесс держит БД; `make sandbox-down`,
  удалить `sandbox/generated/serplux.db*`, поднять заново.
- **`/version` не соответствует тегу** → release.yml собрал образ без
  build-args (проверь secrets/workflow); деплой откатится сам.
- **ruff в CI красный на чужих файлах** — W293/F401 в `labeler/topvisor/webhook`
  приходят из их WIP; линтируй только свои файлы до их коммита.
