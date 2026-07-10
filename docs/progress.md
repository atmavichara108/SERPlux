
# Прогресс SERPlux

Обновлять в конце каждой рабочей сессии. Кратко, по делу.
Одна задача — одна свежая сессия. Не таскай контекст между этапами. Память — в docs/, не в чате. 

## Сделано

- **Session: 2026-07-10 (четвёртая) — POST /labels/import для батч-импорта эталона**
  - [x] `webhook.py`: доработан `POST /labels/import`:
    - Bearer-аутентификация (`_verify_token`): 401/403 как у остальных эндпоинтов.
    - Поддержка двух форматов тела: голый массив и `{"labels": [...]}`.
    - Каждая запись импортируется через существующий `storage.upsert_domain_label`,
      поэтому соблюдается приоритет `source` (`manual_l1` не перезаписывается).
    - Битая запись не роняет батч; считаются `imported`, `skipped`, `errors`,
      собираются первые ~5 сообщений в `error_samples`.
    - Ответ HTTP 200 даже при частичных ошибках.
    - Идемпотентность по PK `(domain, query, geo)`.
  - [x] `tests/test_webhook.py`: доработаны тесты `TestLabelsImportEndpoint`:
    - успешный импорт в обоих форматах тела;
    - идемпотентность;
    - `manual_l1` не перезаписывается `snippet`;
    - битая запись в батче пропускается, остальные импортируются;
    - авторизация (401/403).
  - [x] Обновлена документация:
    - `docs/contracts.md` — актуальная сигнатура и коды ответов.
    - `docs/progress.md` — эта запись.
  - Status: Ready for commit
  - Коммит: `feat(labels): add POST /labels/import endpoint (batched, idempotent, manual_l1 priority)`

- **Session: 2026-07-10 (третья) — Восстановлен одноразовый импорт эталона**
  - [x] `webhook.py`: восстановлен `POST /labels/import`:
    - Идемпотентный upsert по PK `(domain, query, geo)` через `storage.upsert_domain_label`.
    - Устойчив к битым записям: одна невалидная запись пропускается, батч продолжается.
    - Возвращает сводку `processed`/`imported`/`skipped`/`errors`.
  - [x] `apps_script.gs`: добавлена изолированная функция `importEtalonToDb()`:
    - Запускается вручную из редактора через Run, НЕ добавляется в меню `onOpen`.
    - Читает уже распарсенный лист «Эталон разметки» (не Лист1).
    - Определяет колонки по заголовкам; при неизвестной структуре логирует и останавливается.
    - Отправляет батчами по 100 строк с Bearer-авторизацией.
    - Не прерывается на ошибках батчей, в конце логирует итог.
  - [x] Тесты: 8 новых тестов `TestLabelsImportEndpoint` в `tests/test_webhook.py`.
  - [x] Обновлена документация:
    - `docs/contracts.md` — описан контракт `POST /labels/import`.
    - `docs/decisions.md` — новый ADR об изолированном одноразовом импорте.
    - `docs/progress.md` — эта запись.
  - Status: Ready for commit
  - Коммит: `feat(labels): resilient one-shot etalon import (Эталон разметки → domain_labels), batched + idempotent`

- **Session: 2026-07-10 (вторая) — Dynamic reporter + date normalization (apps_script)**
  - [x] reporter.py полностью переписан на динамическую раскладку:
    - Новая функция `_build_subject_layout(queries)` вычисляет N колонок вместо фиксированных 16
    - `build_report()` теперь принимает `client_id` и `db_path`, загружает профиль из БД
    - Субъекты берутся из `client.queries`, гео из `client.regions_map`
    - Поддержка 1, 2, 4, 7+ субъектов без правки кода
  - [x] config.py: `SUBJECT_BLOCKS` и `COLS` переименованы в `_DEPRECATED_*` (обратная совместимость)
  - [x] main.py, webhook.py: передача `client_id` и `db_path` в `build_report()` (обновлено 3 места вызова)
  - [x] migrate.py: использует `_DEPRECATED_SUBJECT_BLOCKS` для seed
  - [x] tests/test_reporter.py: 10 новых тестов для 1, 2, 4, 7 субъектов
  - [x] tests/test_config.py: обновлены для `_DEPRECATED_*`
  - [x] Все 200+ тестов зелёные
  - [x] Исправлен баг с date-парсингом:
    - Добавлена `_normalizeDateToString()` в apps_script.gs
    - Конвертирует Date-объекты и строки в YYYY-MM-DD (UTC)
    - Обновлены `_readSettings()` и `buildReportForDate()` для нормализации дат
    - Fallback на "latest" при невалидной дате (безопасное падение)
  - [x] Документация:
    - docs/decisions.md: два новых ADR (динамический reporter + date normalization)
    - docs/progress.md: эта запись
  - Status: Ready for commit
  - Коммит: `fix(reporter): dynamic subject columns from client profile + fix date parsing in Apps Script (remove hardcoded SUBJECT_BLOCKS/COLS)`

- **Session: 2026-07-10 (первая) — Two-mode labeling refactoring (auto + deep)**
  - [x] labeler.py переписан с двумя режимами: 
    - "auto" (дефолт): domain_labels кэш → сниппет LLM → neutral при ошибке (иерархия)
    - "deep": обработка только neutral (заглушка v2)
  - [x] webhook.py обновлён: `label_mode="auto"|"deep"` (вместо domains/snippets/full)
    - `RunRequest` валидирует только новые режимы
    - `default label_mode="auto"` как дефолт во всех точках входа
  - [x] 17 новых тестов + все 190 тестов проходят успешно
    - TestLabelAutoMode (7 тестов): справочник → LLM → neutral fallback
    - TestLabelDeepMode (4 теста): заглушка, логирование
    - TestLabelLogging (6 тестов): логирование по searcher×geo, статистика
  - [x] Логирование по searcher×geo с детальной статистикой:
    - total / cached / llm_calls / success
    - Причины пропусков: empty_snippet, provider_error, domain_missing, other_skip
    - WARNING/ERROR для пустых сниппетов, ошибок провайдеров, отсутствия доменов
  - [x] manual_l1 приоритетен при upsert domain_labels:
    - source='snippet' (AUTO) или 'page' (DEEP)
    - manual_l1 никогда не перезаписывается автоматикой
  - [x] neutral как маркер неуверенности LLM:
    - `sentiment=None` → fallback при ошибке LLM
    - `confidence='uncertain'` для случаев ошибки
  - [x] Документация обновлена:
    - docs/decisions.md: новый ADR (two-mode labeling)
    - docs/contracts.md: обновлены labeler.py, новые функции _label_group_auto/_label_group_deep
    - docs/progress.md: эта запись
    - docs/user-guide.md: раздел режимов разметки (auto vs deep vs old)
  - Status: Ready for commit and push
  - Коммит: `feat(labeler): two-mode labeling (auto + deep) with fallback, logging by searcher/geo, manual_l1 priority`
- **Этап A завершён: мультиклиентность + лист Настройки**
  - **ЧАСТЬ 1 — Нормализация client_id:**
    - `migrate.py`: добавлена функция `_normalize_client_id(conn)` идемпотентная, переносит все данные с численного "28938353" на строковый slug "client01". Логика: seed создаёт 28938353, нормализация переносит на client01 с полным профилем (name="Sudheimer Group", project_id=28938353).
    - Миграция данных: UPDATE positions/labels/domain_labels, DELETE "28938353" после верификации целостности.
    - `migrate.py` поток: 1) backup 2) create schema 3) patches 4) default клиент 5) перенос из results 6) seed 28938353 7) **нормализация на client01** 8) верификация.
    - Тесты: обновлены все 10 тестов в `test_migrate_idempotent.py` на новую логику (проверяют "client01"), обновлен `test_storage_schema.py`. `172/172 passed`.
    - `docs/decisions.md`, `docs/contracts.md`, `docs/progress.md`, `docs/techdebt.md`: обновлены.
    - Коммит: `fix(clients): normalize client_id to slug client01 + migrate data`
  - **ЧАСТЬ 2 — Лист Настройки в Apps Script:**
    - `apps_script.gs`: функция `_getClientIdList()` получает список client_id из webhook GET /clients.
    - `_setupClientIdValidation(sheet)` устанавливает Data Validation для client_id (строка 1, колонка B) с dropdown из GET /clients. Fallback на свободный ввод если список не получен.
    - `deleteAndRecreateSettingsSheet()` удаляет и пересоздаёт лист Настройки с заполненной структурой (защита от повреждения листа).
    - `initSettingsSheet()` обновлена вызовом `_setupClientIdValidation`.
    - Меню: добавлен пункт "⚙ Настройки → [⟳] Пересоздать лист Настройки".
    - `SETTINGS_TEMPLATE` обновлён: label_mode теперь поддерживает "auto" и "deep" (добавлены новые режимы); client_id подсказка указывает на меню обновления списка.
    - Data Validation: client_id (dropdown), depth ([10,20,50,100]), with_labels/force_relabel/force_rebuild_report ([true,false]), label_mode ([auto,deep,domains,snippets,full]).
    - Коммит: `fix(ui): initSettingsSheet fills structure with client_id dropdown, add deleteAndRecreateSettingsSheet menu`
  - **DoD для Этапа A:**
    - ✅ clients содержит ТОЛЬКО client01 (project_id=28938353 в профиле); default и численный 28938353 удалены, данные перенесены без потерь.
    - ✅ verify.sh проходит 6/6 проверок (нет осиротевших записей).
    - ✅ Лист Настройки пересоздаётся заполненным, client_id выбирается из dropdown (получен из webhook GET /clients).
    - ✅ Все pytest 172 зелёные.
- **Revert: разовый импорт ручной разметки не живёт в коде репозитория**
  - Удалён `scripts/importManualLabels.gs` и пункт меню в `apps_script.gs` — разовый импорт не нужно поддерживать в UI.
  - Удалён `POST /labels/import` из `webhook.py` и соответствующие тесты — одноразовый эндпоинт не должен оставаться в production-коде.
  - Оставлена базовая таблица `domain_labels` и функции `storage.py` (`get/upsert/bulk_upsert`), потому что они нужны для режима `domains`.
  - `docs/contracts.md`: убран `/labels/import`, добавлена декларация — ручная разметка `manual_l1` заполняется вне приложения (SQL/разовый скрипт); разовый импорт Михаила выполнен вне репозитория.
  - Коммит: `revert(labels): remove one-off import code, keep domain_labels core table`
- **Таблица кэша разметки доменов `domain_labels` по ключу (domain, query, geo)**
  - `storage.py`: новая схема `domain_labels` (без `client_id`), функции `get_domain_label(domain, query, geo)`, `upsert_domain_label(..., source)` с приоритетом `manual_l1`, `bulk_upsert_domain_labels()` с массовым upsert и тем же приоритетом.
  - `migrate.py`: идемпотентная миграция `domain_labels` — пересоздание, если обнаружена старая схема (`id`/`client_id`); удалена логика переноса `domain_labels` по `client_id`.
  - `labeler.py`: режим `domains` теперь ищет метку по `(domain, query, geo)`, не использует `client_id`.
  - `docs/contracts.md`, `docs/decisions.md`: обновлены сигнатуры и ADR под новую схему.
  - Тесты: `tests/test_domain_labels.py` (17 тестов) + обновлены `tests/test_labeler_modes.py`; итого 184/184 passed.
  - Коммит: `feat(labels): domain_labels cache table with (domain,query,geo) key + manual_l1 priority`
- **verify.sh успешно протестирован на боевом сервере**
  - Все 6 проверок прошли: тесты (172 passed), health endpoint, контейнер, логи, схема БД, целостность данных. `Summary: Checks: 6/6 passed, Verification passed`.
  - Пройденный путь: добавление pytest в Docker-образ, копирование `tests/` в образ, отключение pytest-кэша, удаление зависимости от `jq`, обработка `set -e` для `grep`/arithmetic/`docker exec` exit codes, синхронизация списка колонок `clients` с реальной схемой.
- **Фикс verify.sh: required_cols в clients не соответствовал реальной схеме**
  - `verify.sh`: вместо несуществующей `id` теперь проверяется `client_name`; список обязательных колонок синхронизирован с `storage.py`/`migrate.py`.
  - `docs/verification.md`: обновлён список колонок.
  - Коммит: `fix(verify): align required clients columns with actual schema`
- **Фикс verify.sh: docker compose exec + set -e убивал скрипт на шагах 5-6**
  - `verify.sh`: шаги 5 (схема БД) и 6 (целостность) обёрнуты в `set +e`/`set -e` с явным сохранением `$?`. Теперь скрипт не умирает от ненулевого exit code внутри `$()`, а корректно выводит детали ошибки.
  - Коммит: `fix(verify): handle docker exec exit codes in schema/integrity checks`
- **Добавлен shellcheck в CI и документация по инфра-тестированию**
  - `.github/workflows/ci.yml`: новый job `shellcheck` для статического анализа всех `.sh` файлов.
  - `verify.sh`, `backup_db.sh`: исправлены замечания shellcheck (`grep -oP` → `grep -oE`, `grep -q` в backup заменён на проверку exit code, `echo "  $1"` → `printf`).
  - `docs/infra-testing.md`: новый документ — границы локального тестирования Docker/shell, почему агент не имеет доступа к Docker API, варианты решения (DinD, remote Docker API, mocks, server-side testing), чек-лист агента, известные ловушки `set -e`.
  - `docs/verification.md`: добавлена информация о shellcheck в CI и ссылка на `infra-testing.md`.
  - Коммит: `feat(infra): add shellcheck CI + docs for Docker/shell testing boundaries`
- **Фикс verify.sh: grep без совпадений убивал скрипт на шаге 4**
  - `verify.sh`: шаг 4 (проверка логов на ошибки) обёрнут в `|| true`, чтобы `grep` без совпадений не возвращал exit code 1 при `set -e`.
  - Коммит: `fix(verify): suppress grep exit code in log error check`
- **Фикс verify.sh: bash arithmetic + set -e убивал скрипт после первого ✓**
  - `verify.sh`: `((CHECKS_PASSED++))` и `((WARNINGS++))` заменены на `CHECKS_PASSED=$((CHECKS_PASSED + 1))` — в bash `((0))` возвращает exit code 1, что с `set -e` завершало скрипт сразу после первой успешной проверки.
  - `docs/verification.md`: добавлено замечание про локальное тестирование verify.sh через `bash -x`.
  - Коммит: `fix(verify): avoid bash arithmetic exit code 1 with set -e`
- **Фикс verify.sh: не требует jq; проверка статуса контейнера по текстовому выводу**
  - `verify.sh`: шаг 3 переписан без `jq` — используется `docker compose ps $SERVICE` + grep. Это устраняет зависимость от наличия jq на сервере и от разных форматов `--format json` в разных версиях Docker Compose.
  - `docs/verification.md`: документировано, что проверка статуса контейнера не требует jq.
  - Коммит: `fix(verify): remove jq dependency, use text output for container status`
- **Фикс verify.sh: pytest не находил тесты в контейнере**
  - `Dockerfile`: добавлено копирование `tests/` и `pyproject.toml` в образ, чтобы pytest внутри контейнера видел тесты.
  - `docs/verification.md`: документировано, что тесты копируются в образ.
  - Коммит: `fix(verify): copy tests into Docker image for verify.sh`
- **Фикс verify.sh: pytest не мог писать кэш в /app**
  - `verify.sh`: pytest запускается с `-p no:cacheprovider` (отключает `.pytest_cache`); проверка результата по exit code через `PIPESTATUS`, а не по grep строки `passed`.
  - `docs/verification.md`: документировано, почему отключён кэш и почему контейнер не имеет прав на запись в `/app`.
  - Коммит: `fix(verify): handle pytest cache permission in readonly /app`
- **Фикс verify.sh: pytest не найден в production-контейнере**
  - `Dockerfile`: `requirements-dev.txt` теперь устанавливается в builder stage, образ содержит `pytest` и `httpx` для `verify.sh`.
  - `requirements-dev.txt`: обновлён комментарий (dev-зависимости включаются в образ).
  - `verify.sh`: добавлена проверка наличия `pytest` перед запуском с понятным сообщением "Rebuild image with requirements-dev.txt".
  - `docs/verification.md`: добавлено предупреждение про необходимость `docker compose build` после пула, чтобы образ обновился.
  - Коммит: `fix(infra): install dev deps in Docker image so verify.sh can run pytest`
- **Автоматизированная верификация между этапами разработки и деплоя**
  - `verify.sh` (в корне репо): 6 проверок после deploy.sh на сервере: тесты в контейнере, health endpoint, статус контейнера, логи на ошибки, схема БД (таблицы+колонки), целостность данных (нет осиротевших записей). Вывод ✓/✗, exit 1 при ошибке. Параметр `SERVICE=${SERVICE:-serplux}`.
  - `backup_db.sh` (в корне): создание бэкап с временной меткой `/app/data/serplux.db.bak.YYYY-MM-DD-HHMMSS`, проверка целостности (валидный SQLite), ротация последних 10 бэкапов. Для ручного вызова перед миграциями.
  - `.github/workflows/ci.yml`: GitHub Actions CI на push и pull_request в main. Поднимает Python 3.11, устанавливает требуемые зависимости, гоняет `pytest -v`. Тесты могут блокировать merge в GitHub. ТОЛЬКО тесты, без деплоя/доступа к серверу.
  - `docs/verification.md`: описание границы авто/ручной проверки. Чек-лист деплоя для пользователя, примеры восстановления из бэкапа, будущие улучшения (cron ротация, Slack-уведомления).
  - Коммит: `feat(infra): verify.sh + backup_db.sh + GitHub Actions CI (tests on push)`
- **Финальная докрутка мультиклиентности: полный профиль клиента в БД**
  - `migrate.py`: идемпотентная схема `clients` с `queries`, seed/обновление клиента `28938353` (`Sudheimer Group`) из `config.py`/`regions_map_client1.json`/env `TOPVISOR_PROJECT_ID`; безопасный перенос данных с `default` через `UPDATE` с `GROUP BY` и разрешением дубликатов; `preseed`-бэкап; верификация отсутствия дочерних записей перед удалением `default` (каскадное удаление исключено).
  - `storage.py`: `get_client`/`list_clients` возвращают распарсенные `queries`/`regions_map`/`searchers`/`geos`, defensive `[]`; `regions_map` поддерживает JSON-массив и legacy-строку; `create_client`/`update_client` расширены `queries` и `regions_map`.
  - `webhook.py`: `_build_client_config` пробрасывает `queries` и `regions_map` из профиля; Pydantic-модели `/clients` обновлены.
  - `main.py`: `runtime_config` получает `queries`/`regions_map` из профиля клиента.
  - `collector.py`: `_get_regions_map` использует список из профиля напрямую, строку — как имя файла (legacy), fallback на env/дефолт.
  - Тесты: 172/172 passed; обновлены существующие (`None`→`[]`); добавлены тесты seed, переноса, дубликатов, preseed-бэкапа, `_build_client_config`, `_get_regions_map`.
  - Docs: ADR в `docs/decisions.md`, обновлены `docs/contracts.md`, `docs/progress.md`, `docs/techdebt.md`, `docs/deploy.md`.
  - Коммит: `feat(clients): full client profile (queries/regions_map/searchers) replaces hardcoded config + file-swap`
- **Подробное структурное логирование пайплайна в stdout**
  - `config.py`: добавлена `setup_logging(name)` — единая настройка логирования
    - Уровень из env `LOG_LEVEL` (дефолт `INFO`)
    - Формат: `timestamp | module | level | message`
    - Вывод в `stdout` для `docker compose logs`
    - `propagate=False` для модульных логгеров — uvicorn не глушит аппликационные логи
  - `main.py`, `webhook.py`, `collector.py`, `labeler.py`, `exporter.py`, `reporter.py`, `storage.py`, `topvisor.py`: переведены на `config.setup_logging(__name__)`
  - `collector.py`: явное логирование каждой связки `searcher×geo` (`Связка N/M: searcher=... geo=...`)
  - `labeler.py`: разметка группируется по `searcher×geo`, логируется подробная статистика по каждой группе
    - total / cached / llm_calls / success
    - причины пропусков: `empty_snippet`, `provider_error`, `domain_missing`, `other_skip`
    - WARNING/ERROR для пустых сниппетов, недоступных провайдеров, отсутствующих доменов
  - `main.py`: итоговая сводка прогона с разбивкой по `searcher` (`collected`/`labeled`/`exported`)
  - `.env.example`: добавлен `LOG_LEVEL=INFO`
  - Коммит: `feat(logging): structured pipeline logging (searcher/geo, label reasons) to stdout`
- **deploy.sh — автоматический скрипт деплоя (infra)**
  - `deploy.sh`: полный цикл обновления — git pull → бэкап БД → build → up → health-check → migrate → финальный health-check
  - Безопасность: `set -euo pipefail`, health-gated миграция (если контейнер не поднялся — миграция НЕ выполняется), бэкап ДО миграции
  - Идемпотентен: повторный запуск без изменений безопасен, не трогает volume
  - Параметризация: `SERVICE=serplux` по умолчанию, можно указать другой сервис
  - `docs/deploy.md`: обновлён с инструкцией по использованию `deploy.sh` и откату при ошибках
- **Инфраструктура деплоя (гибридная модель):**
  - `Dockerfile`: добавлен `migrate.py` в блок COPY (доступен для `docker compose exec ... python migrate.py`)
  - `docs/deploy.md`: создана полная инструкция деплоя на сервер (SSH → git pull → build → up → migrate → verify)
  - `.opencode/command/deploy.md`: переписан под гибридную модель — infra-dev проверяет локальные файлы и готовит чек-лист, пользователь выполняет команды на сервере
  - `.opencode/agents/infra-dev.md`: убрано непроверенное утверждение "Приложение задеплоено", заменено на описание ручного деплоя через SSH
  - `docs/progress.md`: добавлен шаг 3.5 миграции БД в инструкцию первого тестового прогона
- **Руководство пользователя `docs/user-guide.md`: полный гайд для заказчика**
  - Описание всех листов таблицы (Настройки, Лог, Данные, Отчёт)
  - Меню SERPlux и пошаговая инструкция запуска сбора/отчёта
  - Параметры прогона: depth, with_labels, label_mode, force_relabel, date, report_date
  - Режимы разметки (domains/snippets/full) с указанием стоимости
  - Провайдеры LLM, мультиклиентность, отчёт-матрица, версионность, безопасность
  - Блок «Будущие возможности» на основе `docs/techdebt.md` (без выдумывания)
  - FAQ с типовыми проблемами и решениями
- **ADR: Гибридная модель деплоя (2026-07-06)**
  - Зафиксировано в `docs/decisions.md`: агенты работают на локальной машине,
    пользователь выполняет деплой на сервере вручную через SSH
  - Ответ на Q13 в `docs/ui-spec.md`: описан ручной деплой и статус zero-downtime
- **Этап 0: config из профиля клиента (build)**
  - Проблема: `main.py` хардкодил `DEFAULT_CONFIG` (searchers/geos), а `collector`
    брал `project_id` из env — выбор клиента не долетал до Topvisor
  - Решение:
    - `storage.py`: таблица `clients` расширена полями `searchers`, `geos`, `regions_map`
      (JSON-списки + имя файла карты); автомиграция через `ALTER TABLE ADD COLUMN`
    - `webhook.py`: `_build_client_config()` собирает runtime-config из
      `DEFAULT_CONFIG` → параметров запроса → профиля клиента
    - `webhook.py`: `_run_pipeline()` передаёт client-aware config в `main.run()`
    - `collector.py`: `project_id` берётся из `config["project_id"]`, fallback на env
    - `exporter.py` / `reporter.py`: `sheet_id` берётся из параметров/config,
      fallback на `GOOGLE_SHEET_ID`
    - `main.py`: `run()` строит `runtime_config` из `DEFAULT_CONFIG` + `config`
  - Тесты: 151/151 passed; добавлены тесты на профиль клиента, sheet_id,
    searchers/geos/project_id в `test_webhook.py` и `test_main.py`
  - `docs/contracts.md`: обновлены сигнатуры `clients` CRUD и `RunRequest`
- **Этап 3: Документация — user-guide.md, сверка contracts.md, закрытие techdebt**
  - Обновлен `docs/user-guide.md`:
    - Меню SERPlux приведено в соответствие с `apps_script.gs` (новые пункты: «Разметить собранные данные», «Разметить за дату...», «Обновить список клиентов», «Обновить гео из Topvisor...»)
    - Лист «Настройки»: описаны выпадающие списки `client_id` и `provider_chain`
    - Добавлены сценарии использования: сбор за дату, разметка без сбора (`label_only`), разметка за дату, построение отчёта за дату, добавление клиента с гео из Topvisor, обновление гео клиента
    - Расширено описание параметров `date`, добавлены `force_rebuild_report`, `provider_chain`
    - Раздел «Клиенты» дополнен полями `searchers`, `geos`, `regions_map`
    - Из «Будущих возможностей» убраны уже реализованные пункты (`date`, `force_rebuild_report`, `provider_chain`)
  - `docs/contracts.md`: финальная сверка — все сигнатуры Этапов 0–2 актуальны (`RunRequest`, `/clients/{id}/dates`, `/topvisor/regions`, `label()` с `provider_chain`, `storage.get_dates()`)
  - `docs/techdebt.md`: исправленные пункты перенесены в раздел «Исправлено»:
    - project_id из .env → профиль клиента
    - date в `/run`
    - regions_map + project_id рассинхронизация
    - date/force_rebuild_report/provider_chain в `/run`
    - default `db_path` — обходной фикс применён
  - Коммит: `feat(docs): user guide scenarios, contracts sync, techdebt cleanup`
- **Багфиксы по результатам code review (Stages 0–2)**
  - `apps_script.gs`: default `provider_chain` изменён с `zen` на `opencode-zen` (соответствует реальному ID провайдера в `config.py`)
  - `apps_script.gs`: исправлено `force_rebuild_report: settings.forceRebuildReport || true` → всегда `true` из-за JS; теперь используется значение из листа «Настройки»
  - `webhook.py`: в `_run_pipeline(label_only)` теперь пробрасывается `provider_chain` и `db_path=storage.DB_PATH` в `labeler.label()`
  - `main.py`: в полном пайплайне `label()` теперь вызывается с `db_path=storage.DB_PATH`
  - `main.py`: `stats["exported"]` теперь устанавливается только при успешном `export()`; при ошибке сбрасывается в `0`
  - `storage.py`: убран дублирующийся `if not rows: return 0` в `insert_labels()`
  - `apps_script.gs`: убрана неиспользуемая переменная `ui` в `refreshProviderChain()`
  - `docs/progress.md`: убран устаревший пункт `date/force_rebuild_report/provider_chain в /run` из «Дальше по порядку»; обновлено «Текущее состояние проекта»
  - Тесты: `160/160 passed`; `node --check` для `apps_script.gs` пройден
  - Коммит: `fix(review): provider defaults, db_path in labeler, exported stats`
- **Этап 2: UI Google Sheets — выпадающие списки, label-only, гео из Topvisor**
  - Проблема: UI не использовал новые возможности Этапа 1 (date, label_only, provider_chain, force_rebuild_report); добавление клиента требовало ручного ввода гео; не было быстрого доступа к списку дат для отчёта/разметки
  - Решение:
    - Меню SERPlux расширено: «Разметить собранные данные» (label_only=true), «Разметить за дату…» (label_only + выбор даты), «Обновить список клиентов», «Обновить гео из Topvisor»
    - `refreshClientList()`: GET /clients → Data Validation dropdown на ячейке client_id; автоматически вызывает `refreshProviderChain()`
    - `refreshProviderChain()`: GET /providers → Data Validation dropdown на ячейке provider_chain
    - `labelOnly()`: POST /run с label_only=true, использует настройки из листа
    - `labelOnlyForDate()`: GET /clients/{id}/dates → выбор даты → POST /run с label_only + date + label_mode + force_relabel
    - `buildReportForDate()`: GET /clients/{id}/dates → выпадающий список дат (вместо свободного ввода); убран disclaimer про report_only
    - `runCollection()`: передаёт date, force_rebuild_report, provider_chain в POST /run
    - `addClient()`: расширен до 6 шагов — после project_id → GET /topvisor/regions → мультивыбор гео (нумерованный список) + выбор сорсеров (google/yandex_ru/yandex_com) → POST /clients с geos+searchers
    - `updateClientGeos()`: для существующих клиентов — GET /topvisor/regions → мультивыбор → PUT /clients/{id}
    - Defensive: если /topvisor/regions вернул 502 → диалог «Не удалось получить гео из Topvisor, введите вручную» (fallback на ручной ввод)
    - Bearer-авторизация во всех новых запросах
  - Синтаксис: `node --check` пройден (через копирование в .js)
  - `docs/progress.md`: добавлена секция Этапа 2
  - Коммит: `feat(ui): client dropdown, geos/searchers from Topvisor, label-only flows, date pickers`
- **Этап 1: date/label_only/provider_chain/force_rebuild_report + новые read-only endpoint'ы**
  - Проблема: UI-спека требует выбора даты, разметки без повторного сбора,
    выбора провайдера и принудительного перестроения отчёта; не хватало
    endpoint'ов для списка дат клиента и регионов Topvisor
  - Решение:
    - `webhook.py`: `RunRequest` расширен полями `date`, `label_only`,
      `provider_chain`, `force_rebuild_report`; валидаторы для `depth` и `date`
    - `webhook.py`: `GET /clients/{client_id}/dates` → `{"dates": [...]}`
    - `webhook.py`: `GET /topvisor/regions?project_id=...` → `{"project_id", "regions"}`
    - `main.py`: `run()` возвращает `dict {"exit_code", "stats"}`; проброс
      `force_rebuild_report` в `reporter.build_report()` и `provider_chain` в `labeler.label()`
    - `labeler.py`: `label()` принимает `provider_chain` и фильтрует `config.PROVIDERS`
    - `storage.py`: добавлена `get_dates(client_id)`
    - `topvisor.py`: добавлена `list_regions(project_id)`
    - Исправлен баг с default-значением `db_path`: `main.py`, `reporter.py`,
      `webhook.py` теперь явно передают `storage.DB_PATH` в вызовы storage,
      иначе monkeypatch/изменение env не доходили до функций с захваченным default
  - Тесты: `160/160 passed`; добавлены тесты на валидацию `date`/`depth`,
    `label_only`, новые endpoint'ы, проброс `provider_chain`/`force_rebuild_report`
  - `docs/contracts.md`, `docs/techdebt.md`: обновлены сигнатуры и заметка про `db_path`
- **webhook.py: report_only + finished_at/client_id (ui-spec §5.2-5.3)**
  - `POST /run`: новые поля `report_only: bool = False` и `report_date: str = "latest"` в RunRequest
  - При `report_only=true`: пропускает collect/save/label/export, вызывает только `reporter.build_report(date, force=True)`
  - При `report_only=false` (дефолт): полный пайплайн (обратная совместимость)
  - `GET /status`: расширен `_last_run` полями `finished_at` (ISO, null пока идёт) и `client_id`
  - `finished_at` сбрасывается при старте нового прогона, заполняется в `finally` блоке
  - `client_id` сохраняется из тела запроса `/run`
  - Ответ `/run` 202 теперь включает `client_id`
  - Тесты: `test_webhook.py` +8 (TestReportOnly: 4, TestStatusExtendedFields: 4)
  - `./venv/bin/python -m pytest -q` — **144 passed**
  - `docs/contracts.md`: полные сигнатуры всех webhook-эндпоинтов
- **apps_script.gs v1.0 — полный UI по ui-spec.md §4** (single-table-per-client)
  - Меню «SERPlux» по §4.3: Запустить сбор / Проверить статус / Построить отчёт за дату /
    Клиенты (Показать список, Добавить) / Настройки (Установить секрет, URL,
    Инициализировать настройки, Триггеры, Показать профиль, Управление провайдерами)
  - Лист «Настройки» по §4.2: 10 ключей (client_id, depth, with_labels, label_mode,
    date, force_relabel, force_rebuild_report, report_date, provider_chain, status)
    с Data Validation (depth: 10/20/50/100; bool: true/false; label_mode: domains/snippets/full)
  - `runCollection()`: валидация client_id/секрет → диалог подтверждения → POST /run
    (client_id, depth, with_labels, label_mode, force_relabel) → обработка 202/409
  - `checkStatus()`: GET /status → маппинг состояний (idle/starting/running/ok/error)
    → defensive-доступ к stats (provider_used, collected, etc.)
  - `buildReportForDate()`: диалог даты → POST /run с report_only (серверный хвост — см. techdebt)
  - `showClients()` / `addClient()` / `showProfile()`: CRUD клиентов через API
  - `manageProviders()`: GET /providers + заглушки (CRUD не реализован, ADR)
  - `_updateStatusCell()`: цветовая заливка по §4.5 (серый/жёлтый/зелёный/красный)
  - `_appendLog()`: дозапись в лист «Лог» (дата, клиент, статус, сообщение, провайдер)
  - `_friendlyError()`: user-friendly ошибки без stack traces
  - Bearer-авторизация во всех запросах, секрет из Script Properties
  - Безопасные emoji (только BMP, без 4-байтных) в пунктах меню
- **Провайдеры LLM в config.py + read-only /providers**
  - `config.py`: `PROVIDERS` dict (opencode-zen: endpoint, model, api_key_env_var, enabled, priority)
  - `labeler.py`: рефактор — цепочка провайдеров из `config.PROVIDERS` (фильтр enabled, сортировка priority), убран хардкод Zen/DeepSeek; `_call_provider` обобщён, `_label_one_llm` итерирует цепочку с фолбеком
  - `webhook.py`: `GET /providers` под Bearer-авторизацией, возвращает `id/enabled/priority/default_model/models`
  - Тесты: `test_webhook.py` +2 (GET /providers, 401), `test_labeler_modes.py` +4 (цепочка из config, disabled исключается, пустая цепочка → None)
  - `docs/contracts.md`: структура PROVIDERS + сигнатура GET /providers
  - `docs/decisions.md`: ADR «провайдеры в config.py, read-only, CRUD отложен»
  - `./venv/bin/python -m pytest -q` — **136 passed**, 0 warnings
- **API `/clients` — CRUD профилей клиентов**
  - `storage.py`: `list_clients`, `get_client`, `create_client`, `update_client`
    — работают с таблицей `clients`, обновляют `updated_at`, поднимают понятные ошибки
    при дубле или отсутствии клиента
  - `webhook.py`: `GET /clients`, `POST /clients`, `GET /clients/{id}`, `PUT /clients/{id}`
    — все под Bearer-авторизацией; `POST` возвращает `409` при дубле, `GET/PUT` — `404`
    если клиент не найден
  - Контракты зафиксированы в `docs/contracts.md`; статус ADR обновлён в `docs/decisions.md`
  - Тесты: `TestClientManagement` в `tests/test_storage_schema.py` (8 тестов) +
    `TestClientsEndpoint` в `tests/test_webhook.py` (10 тестов)
  - `./venv/bin/python -m pytest -q` — **130 passed**
- **ADR 2026-07-03 — дефолт label_mode = 'domains'** (решение Q4)
  - Зафиксировано в `docs/decisions.md`: внешние точки входа `/run` и `main.run()`
    по умолчанию используют `domains` (без LLM), `snippets`/`full` — явный выбор
  - `webhook.py` и `main.py` уже реализовали поля `client_id`/`label_mode`/`force_relabel`
  - `tests/test_webhook.py` (9 тестов) + `tests/test_main.py` (4 теста) покрывают
    старый контракт, новые поля, валидацию 422 и проброс в `label()`
  - `./venv/bin/python -m pytest -q` — **111 passed**
- **AGENTS.md** — добавлено правило немедленного делегирования:
  plan-агент сразу делегирует build/ui-dev/infra-dev при явном запросе пользователя,
  не размышляет и не задаёт лишних вопросов, если ТЗ достаточно ясно
- **ADR 2026-07-03 реализован**: новая схема данных clients/positions/labels
  - `storage.py`: DDL с FK/CASCADE/CHECK, все индексы из ADR, авто-клиент `default`
  - `insert_labels()`: INSERT новой версии, `label_version = MAX+1`, retry 3 попытки на UNIQUE
  - `get_cached_label()`: JOIN positions+labels, последняя не-NULL sentiment
  - `get_history()`: расширенный Row (sentiment/label_mode/label_version), фильтры `label_version='all'` и `client_id`
  - `get_domain_label()` / `upsert_domain_label()`: справочник размеченных доменов `domain_labels`
  - `update_labels()`: DEPRECATED, делегирует `insert_labels()`
  - `main.py`: пайплайн использует `insert_labels` вместо `update_labels`
  - `labeler.py`: проставляет `sentiment` + алиас `label` + `confidence`, параметры `label_mode`/`force_relabel`/`client_id`
  - **Важно:** реализованы `snippets` (кэш+LLM) и `domains` (справочник без LLM); `full` остаётся заглушкой (v2)
- **Миграционный скрипт `migrate.py`**:
  - Принимает `--db <path>` (явный путь, не трогает боевую БД автоматически)
  - Шаг 0: бэкап `cp <db> <db>.bak.YYYY-MM-DD`
  - Перенос `results` → `positions` + `labels` (version=1, mode='snippets')
  - Верификация `COUNT(results) == COUNT(positions)`; `DROP results` только при успехе
- **T-001 + T-00X тесты** (`tests/test_storage_schema.py`, 20 тестов; `tests/test_labeler_modes.py`, 11 тестов):
  - Миграция без потери строк
  - Инкремент версий, независимые счётчики по режимам
  - Гонка + retry, атомарность (3 попытки)
  - `get_history` с фильтрами
  - `get_cached_label` через JOIN
  - `insert_labels`, DEPRECATED `update_labels`
  - `get_domain_label` / `upsert_domain_label` (справочник доменов)
  - Режим `domains` в `labeler.label()` без вызова LLM
  - Режим `snippets` не сломан (кэш + force_relabel)
  - Все 95 тестов зелёные (84 старых + 11 новых)
- Настроена структура проекта, опенкод, агенты, плагины
- AGENTS.md, контракты, выжимка API готовы
- Инфраструктура: .env, credentials, таблица расшарена на service account
- **Вертикальный срез готов**: topvisor.py (run_check/poll_status/get_snapshot)
  - Работает Google/Литва (region_index=1300)
  - Получено 37 строк из снимка за 2026-06-19
  - Идемпотентность: повторный запуск не тратит баланс
- **collector.py готов**: сбор по всем связкам searcher×geo
  - Карта регионов regions_map.json (15 связок)
  - ОДИН run_check на все missing-регионы (Topvisor проверяет весь проект целиком)
  - Идемпотентность: проверка snapshot_exists перед run_check
  - Протестирован на Google/Литва (37 строк)
- **storage.py готов**: SQLite хранилище с кэшем меток
  - Версионность: каждый прогон — новые строки со своей датой
  - Идемпотентность: UNIQUE constraint на (date, searcher, query, geo, position, url)
  - Кэш меток: get_cached_label(url, query) по паре (url+query), не только url
  - Протестирован на фейковых данных
- **exporter.py готов**: выгрузка в Google Sheets
  - Версионность: новые данные вставляются блоком сверху (insert_rows)
  - Маппинг searcher в читаемые названия (Google, Яндекс, Яндекс.com)
  - Batch-вставка для оптимизации API
  - Протестирован на фейковых данных (2 прогона с разными датами)
  - Реальный прогон в Sheet заказчика — после установки GOOGLE_SHEET_ID
- **main.py готов**: точка входа пайплайна
  - Порядок: collect → save → export
  - Стратегия сбоев: collect упал → стоп, save/export упал → продолжаем
  - Config пока из словаря DEFAULT_CONFIG (чтение из листа "Настройки" — этап 3)
  - Пайплайн без нейронки замкнут: topvisor → collector → storage → exporter → main
- **reporter.py приведён к точному формату заказчика** (лист "Лист1", проверено визуально)
  - Фиксированные 16 колонок (0-indexed): A=пусто, Juri Pos=1/URL=2, D-F=пусто,
    Erik Pos=6/URL=7, I=пусто, SCT Pos=9/URL=10, L=пусто, Chempioil Pos=12/URL=13, O-P=пусто
  - SUBJECT_BLOCKS захардкожен под 4 субъекта с точными координатами колонок
  - Заголовки субъектов в URL-колонках (2, 7, 10, 13), Pos-колонки пусты
  - Гео-метки в Pos-колонках (1, 6, 9, 12), URL-колонки пусты
  - Формат даты: `D.M.YYYY` без ведущих нулей (например, `9.6.2026`)
  - Порядок гео через config.GEO_ORDER: Литва → Германия → Великобритания → ОАЭ → Кипр Eng → Кипр Greek
  - Пустые гео-секции рисуются всегда (EMPTY_GEO_DEPTH=REPORT_DEPTH=10), даже если нет данных в БД
  - Матрица ограничена REPORT_DEPTH=10 для сопоставимости ПС (Яндекс топ-50 режется в отчёте)
  - Пустые строки: 1 после каждой geo-секции, 2 после блока (итого 3 между блоками дат)
  - Без merge_cells, без bold/заливки — чистая вставка данных
  - Данные из storage.get_history(), версионность insert_rows сверху
- **labeler.py готов**: разметка тональности через Zen qwen3.6-plus
  - Основной провайдер: Zen (qwen3.6-plus) через opencode.ai/zen/v1
  - Фолбек: Gemini 2.0 Flash (если задан GEMINI_API_KEY)
  - Пауза 1с между вызовами LLM
  - Кэш по паре (url+query): сначала проверяет БД, потом вызывает LLM
  - Протестирован на реальном Zen (3 фейковых строки: negative/positive/neutral)
- **main.py**: полный пайплайн collect → save → label → update_labels → export
  - `_ensure_db()` автоматически создаёт таблицу при первом save()
  - Идемпотентность exporter/reporter: пропуск если данные уже есть

- **labeler.py**: переведён на DeepSeek v4 Flash Free через opencode.ai/zen API
  - Gemini полностью убран, OPENCODE_API_KEY вместо GEMINI_API_KEY
- **webhook.py готов**: FastAPI endpoint для запуска пайплайна из Google Sheets
  - POST /run — запуск в фоновом потоке (202 Accepted), защита Bearer-токеном
  - GET /status — статус последнего прогона
  - GET /health — health-check для мониторинга контейнера
  - Защита от параллельных прогонов (threading.Lock → 409 Conflict)
- **Расширение POST /run (client_id, label_mode, force_relabel)**
  - `webhook.py`: схема `RunRequest` с новыми полями и валидацией `label_mode`
  - `main.py`: проброс `client_id` в `save()`, `label_mode`/`force_relabel`/`client_id` в `label()`
  - Обратная совместимость со старым контрактом сохранена
  - Тесты: `tests/test_webhook.py` (9 тестов) + `tests/test_main.py` (4 теста)
  - Все 108 тестов зелёные
- **apps_script.gs v0.4**: Installable Trigger для мультиаккаунтного доступа
  - Функция setupTriggers() — создаёт триггер под аккаунтом разработчика
  - Пункт меню "[!] Установить триггеры (1 раз)"
  - Инструкция для владельца и разработчика в шапке файла
  - Script Properties изолированы по аккаунтам
- **apps_script.gs v0.3**: исправлено меню — убран 4-байтный emoji (📋→[>]), try/catch в onOpen(), initSettingsSheet() с шаблоном ключей, исправлена инструкция (НЕ запускать onOpen() вручную)
- **apps_script.gs v0.2 готов**: Google Apps Script меню в Sheets
  - Разделённые пункты: «Запустить сбор (без разметки)» и «Запустить сбор + разметка»
  - WEBHOOK_URL перенесён из хардкода в Script Properties (как секрет)
  - `_readSettings()` расширен: читает client_id, label_mode, date (для будущего)
  - Toast-уведомления после запуска и при ошибках
  - Пункт «Открыть настройки» — переключает на лист «Настройки»
  - Цветовая индикация статуса на листе «Настройки» (зелёный/жёлтый/красный/серый)
  - Секрет и URL хранятся ТОЛЬКО в Script Properties, не в коде
- **apps_script.gs готов**: Google Apps Script кнопка в Sheets
  - Меню "SERPlux" → Запустить сбор / Проверить статус / Установить секрет
  - Секрет хранится в Script Properties (не в коде)
  - Читает параметры из листа "Настройки" если он есть
- **Dockerfile готов**: многоэтапная сборка, non-root пользователь, health-check
- **docker-compose.yml готов**: volume для SQLite, credentials.json read-only, ограничения ресурсов
- **collector.py**: параметризация клиента — regions_map через config["regions_map"] или env REGIONS_MAP
- **storage.py**: DB_PATH читается из env DB_PATH (для контейнера /app/data/serplux.db)
- **.env.example**: актуализирован (OPENCODE_API_KEY, WEBHOOK_SECRET, DB_PATH, REGIONS_MAP)
- **requirements.txt**: добавлен pydantic, uvicorn[standard]
- **docs/ui-spec.md**: полная UI-спецификация (609 строк) — параметры, API-контракт, Sheets-меню, мультиклиентность, провайдеры
- **docs/techdebt.md**: реестр техдолга — 4 высоких, 3 средних, 3 низких приоритета
- **Мульти-агентная архитектура v2**: 6 агентов, 3 команды-пайплайна
  - Агенты: build, plan (primary); collector-dev, reviewer, ui-dev, infra-dev (subagent)
  - Команды: `/interface` (ui-dev), `/container` (infra-dev), `/deploy` (infra-dev)
  - Файлы: `.opencode/agents/*.md`, `.opencode/command/*.md`
  - opencode.json: `default_agent: build`, task-права для subagent'ов
- **AGENTS.md**: дополнены принципы — не расходовать токены впустую (обращаться к докам Волта), устойчивое развитие

## В работе
- Серверные хвосты для UI: /status.stats (provider_used, collected, cost_estimate) — отложены (см. techdebt)

## Заблокировано / ждёт
- Широкий формат exporter — переработать контракт Row под него (низкий приоритет)
- Риск timeout при сборе больших групп (5+ регионов одной ПС):
  timeout вынесен в config (дефолт 900 сек), при проблемах увеличить

## Дальше по порядку
1. Первый тестовый прогон на боевом сервере (см. «Текущее состояние проекта» ниже)
2. /status.stats (provider_used, collected, cost_estimate) — техдолг
3. Мультиклиентность: subject_blocks в БД и настройка под клиента
4. CRUD /providers — техдолг

## Идеи на будущее (UI-этап)
- **Календарь версий**: каждый прогон собирает выдачу под датой, в отчёте копятся
  блоки-версии («Позиции Google на 21.6.2026» и т.д.). UI показывает даты как
  кликабельные точки в календаре (по аналогии с панелью Topvisor «Для автоматизации»),
  клик → открывает соответствующую версию-блок. Бэкенд готов: в БД каждая строка
  с полем `date`, в отчёте каждый блок подписан датой. Нужен только UI-слой поверх.

---

## Текущее состояние проекта (для тестового прогона на сервере)

**Дата:** 2026-07-07
**Версия API:** 1.0.0
**Тесты:** 160/160 passed
**Ветка:** main

### Что реализовано

**Backend (Python/FastAPI):**
- `POST /run` — запуск пайплайна сбора → разметки → выгрузки → отчёта
  - Поля: `client_id`, `depth`, `with_labels`, `label_mode`, `force_relabel`, `report_only`, `report_date`
  - `report_only=true` → только построение отчёта (пропускает сбор и разметку)
  - Bearer-авторизация, защита от параллельных прогонов (→ 409)
- `GET /status` — статус последнего прогона: `idle/starting/running/ok/error`, `started_at`, `finished_at`, `client_id`, `message`
- `GET /clients`, `POST /clients`, `GET/PUT /clients/{id}` — CRUD профилей клиентов
- `GET /providers` — read-only список LLM-провайдеров (из `config.py`)
- `GET /health` — health-check

**Google Sheets UI (Apps Script):**
- Меню «SERPlux» в таблице: Запустить сбор / Проверить статус / Построить отчёт за дату / Клиенты / Настройки
- Лист «Настройки» с 10 ключами и Data Validation
- Лист «Лог» для истории запусков
- Цветовая индикация статуса в ячейке

**Core-пайплайн:**
- topvisor → collector → storage → labeler (domains/snippets) → exporter → reporter
- 144 тестов, все зелёные

### Что НЕ реализовано (техдолг)
- `/status.stats` — статистика прогона (provider_used, collected, cost_estimate)
- `date` в /run — дата сбора для ретроспективных прогонов
- `force_rebuild_report`, `provider_chain` в /run
- CRUD /providers (POST/PUT/DELETE)
- Режим `full` в labeler (заход на страницу)
- Широкий формат exporter

### Инструкция: первый тестовый прогон на сервере

Вызов агента `infra-dev` командой `/deploy`:

1. **Проверить docker-compose.yml** — актуален ли (последняя правка была до изменений webhook.py). Обратить внимание:
   - `WEBHOOK_SECRET` задан в `.env`
   - `GOOGLE_CREDENTIALS_PATH` указывает на credentials.json
   - `GOOGLE_SHEET_ID` заполнен
   - `DB_PATH=/app/data/serplux.db` с volume `serplux_data`
   - `REGIONS_MAP=regions_map.json` (или переопределён для клиента)

2. **Собрать образ:**
   ```bash
   docker compose build
   ```

3. **Запустить контейнер:**
   ```bash
   docker compose up -d
   ```

3.5. **Миграция БД (если старая схема с таблицей results):**
   ```bash
   docker compose exec serplux python migrate.py --db /app/data/serplux.db
   ```
   migrate.py сделает бэкап, перенесёт данные, верифицирует, DROP results только при успехе.

4. **Проверить health:**
   ```bash
   curl http://localhost:8000/health
   # → {"status":"ok","service":"serplux-webhook"}
   ```

5. **Проверить авторизацию:**
   ```bash
   curl -H "Authorization: Bearer $WEBHOOK_SECRET" http://localhost:8000/status
   # → {"started_at":null,"finished_at":null,"status":"idle","message":"","client_id":null}
   ```

6. **Проверить запуск из Google Sheets:**
   - Открыть таблицу → меню SERPlux
   - Настройки → Инициализировать настройки
   - Настройки → Установить URL сервера (https://serp.example.com)
   - Настройки → Установить секрет (WEBHOOK_SECRET из .env)
   - Заполнить `client_id` на листе Настройки
   - Запустить сбор → Проверить статус

7. **Проверить report_only:**
   - SERPlux → Построить отчёт за дату...
   - Ввести дату из уже собранных данных или оставить пустым

8. **Проверить клиентов:**
   - SERPlux → Клиенты → Показать список
   - SERPlux → Клиенты → Добавить клиента

### Docker-стек

| Компонент | Значение |
|-----------|----------|
| Базовый образ | python:3.11-slim |
| Рабочая директория | /app |
| Точка входа | `uvicorn webhook:app --host 0.0.0.0 --port 8000` |
| Health-check | GET /health |
| Данные | volume `serplux_data:/app/data` |
| Ресурсы | 512MB RAM (дефолт) |

### Переменные окружения (.env)

```
WEBHOOK_SECRET=<токен>
OPENCODE_API_KEY=<ключ>
TOPVISOR_USERNAME=<email>
TOPVISOR_PASSWORD=<пароль>
TOPVISOR_PROJECT_ID=<id>
GOOGLE_CREDENTIALS_PATH=credentials.json
GOOGLE_SHEET_ID=<id таблицы>
DB_PATH=/app/data/serplux.db
REGIONS_MAP=regions_map.json
```

## В работе
- **Починка `initSettingsSheet` в `apps_script.gs` (регресс)**
  - Полностью переписана функция по проверенному релизному паттерну: минимум промежуточных операций, функция всегда доводит лист до заполненного состояния.
  - Не удаляет существующий лист, только `clearContents` (в try-catch).
  - Один `setValues` для `SETTINGS_TEMPLATE` (10×3); при фатальном сбое `setValues` — лог и return, без throw.
  - Форматирование обёрнуто в try-catch; при сбое логируется, но продолжается.
  - Все валидации навешиваются по одному, каждая в индивидуальном try-catch с `Logger.log("initSettingsSheet: ошибка валидации <поле>: ...")`.
  - `_setupClientIdValidation` вызывается последним и обёрнут в try-catch — сетевой/серверный сбой не роняет остальное.
  - `setActiveSheet` и `toast` — каждый в отдельном try-catch (косметика, не критично).
  - Убран верхнеуровневый `throw fatal`: функция не бросает исключения, лист всегда создаётся/заполняется, даже при частичных сбоях.
  - `label_mode` (строка 4): dropdown строго `["auto", "deep"]`.
  - Упоминания `domains`/`snippets`/`full` в `apps_script.gs` отсутствуют.
  - Коммит: `fix(ui): resilient initSettingsSheet — always builds sheet, no fatal throw (release fallback)`

## Дальше
- Первый тестовый прогон на боевом сервере после миграции БД.
- Проверка режимов `auto` и `deep` в labeler на реальных данных.
- Валидация динамического reporter с профилем клиента (2/4/7 субъектов).
- Если разовый импорт ручной разметки повторится — использовать внешний скрипт/SQL, не добавлять в код репозитория.
- Добавить мониторинг/алерты на ошибки verify.sh после deploy.
