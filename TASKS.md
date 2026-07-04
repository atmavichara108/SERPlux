# TASKS — SERPlux

Трекер задач. Формат: T-XXX: описание.
Обновлять при создании новой задачи. Отмечать [x] при завершении.

## T-001: Тесты на новую схему БД

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Зависимость:** после миграции БД (ADR 2026-07-03)
**Затронутые файлы:** tests/test_storage_schema.py, storage.py

### Результат:
- 20 тестов в `test_storage_schema.py` (миграция, версионирование, гонка, фильтры, insert_labels, атомарность)
- Все 95 тестов зелёные
- Коммит в рамках T-002/T-003

---

## T-002: Режим domains разметки + справочник доменов

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** storage.py, labeler.py, migrate.py, tests/test_labeler_modes.py, docs/contracts.md, docs/progress.md

### Что сделано:

- **storage.py**: таблица `domain_labels` + `get_domain_label()` / `upsert_domain_label()`, поле `confidence` в `labels`
- **labeler.py**: режим `domains` (справочник, без LLM), параметр `client_id`, `confidence='high'`
- **migrate.py**: DDL-заплатки для `domain_labels` и `confidence`
- **tests/test_labeler_modes.py**: 11 тестов (справочник, domains без LLM, snippets не сломан)
- **docs/contracts.md**: обновлена сигнатура `label()`

### Результат:
- 95/95 тестов зелёные
- LLM не вызывается в режиме `domains` (подтверждено моком)
- Обратная совместимость `Row` (label=алиас) сохранена

---

## T-003: Идемпотентность migrate.py по схеме (domain_labels + confidence)

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** migrate.py, tests/test_migrate_idempotent.py

### Что сделано:

- **migrate.py**: перестроен поток — убран ранний `return` при отсутствии `results`. Теперь `_create_new_schema()`, `_apply_schema_patches()` и авто-клиент `'default'` выполняются **всегда**. Перенос данных из `results` — условно (только если таблица существует).
- **`_verify_schema()`**: новая финальная проверка — логирует таблицы/колонки, `raise RuntimeError` при отсутствии `domain_labels` или `labels.confidence`.
- **DDL `labels`**: колонка `confidence` включена в `_create_new_schema()` для свежих БД.
- **tests/test_migrate_idempotent.py**: 3 теста:
  1. БД после 1-й миграции (без `domain_labels`/`confidence`) → досоздаёт
  2. Полностью мигрированная БД → идемпотентна (двойной запуск)
  3. Legacy `results` → перенос + новая схема

### Результат:
- 111/111 тестов зелёные (3 новых + 108 старых)
- `migrate.py` на любой БД (чистая / частично мигрированная / полностью мигрированная / legacy) отрабатывает корректно
- Рабочее дерево чистое

---

## T-004: Расширение POST /run — client_id, label_mode, force_relabel

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** webhook.py, main.py, tests/test_webhook.py, tests/test_main.py, docs/contracts.md, docs/decisions.md, docs/progress.md

### Что сделано:

- **webhook.py**: схема `RunRequest` расширена полями `client_id` (default `"default"`), `label_mode` (default `"domains"`), `force_relabel` (default `False`). Добавлена валидация `label_mode ∈ {domains, snippets, full}` через Pydantic `field_validator` — невалидное значение возвращает `422` с пояснением. Проброс всех полей в пайплайн.
- **main.py**: `run(config)` извлекает и передаёт `client_id` в `save()`, а `label_mode`/`force_relabel`/`client_id` в `label()`.
- **tests/test_webhook.py**: 9 тестов — старый контракт, новые поля, дефолт `domains`, валидные/невалидные режимы, авторизация.
- **tests/test_main.py**: 4 теста — проброс `client_id` в `save`, параметров в `label`, дефолты, `with_labels=False`.
- **docs/decisions.md**: ADR обновлён (дефолт `label_mode` для `/run` → `"domains"`).
- **docs/contracts.md**: добавлено примечание о дефолте `/run` vs дефолте `labeler.label()`.
- **docs/progress.md**: отражено расширение `/run`.
- Bearer-авторизация `/run` не сломана.
- `/status`, `/clients`, `/providers` не затронуты; `date`, `report_*`, `provider_chain`, `report_only` не добавлены; миграция не запускалась.

### Результат:
- 108/108 тестов зелёные (9 + 4 новых, остальные старые)
- Reviewer PASS: изменения соответствуют DoD
- Коммит: `d5f6374 feat(api): расширить POST /run параметрами client_id, label_mode, force_relabel`

---

## T-005: CRUD /clients и storage client management

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** storage.py, webhook.py, tests/test_webhook.py, tests/test_storage_schema.py, docs/contracts.md, docs/decisions.md, docs/progress.md

### Что сделано:

- **storage.py**: `list_clients()`, `get_client()`, `create_client()`, `update_client()` — CRUD над таблицей `clients`, обновляют `updated_at`, поднимают ValueError при дубле/отсутствии.
- **webhook.py**: `GET /clients`, `POST /clients` (201, 409), `GET /clients/{id}` (200, 404), `PUT /clients/{id}` (200, 404) — все под Bearer-авторизацией.
- **Тесты**: `TestClientManagement` (8 тестов) в `test_storage_schema.py`, `TestClientsEndpoint` (10 тестов) в `test_webhook.py`.
- **requirements-dev.txt**: добавлен `httpx2>=2.5.0` для подавления StarletteDeprecationWarning.
- **docs/contracts.md**: зафиксированы сигнатуры новых функций.
- **docs/decisions.md**: обновлён статус ADR по `/clients`.

### Результат:
- 130/130 тестов зелёные, без warning
- Коммит: `59214b1 feat: /clients CRUD endpoints and storage client management; add httpx2 dev dep to silence TestClient warning`

---

## T-006: Apps Script UI по ui-spec.md §4 (single-table-per-client)

**Статус:** ✅ Done (2026-07-04)
**Приоритет:** высокий
**Затронутые файлы:** apps_script.gs, docs/progress.md, docs/techdebt.md

### Что сделано:

- **apps_script.gs v1.0** (1093 строки, 21 функция):
  - Меню «SERPlux» по §4.3: Запустить сбор / Проверить статус / Построить отчёт за дату / Клиенты (Показать список, Добавить) / Настройки (Установить секрет, URL, Инициализировать настройки, Триггеры, Показать профиль, Управление провайдерами)
  - Лист «Настройки» по §4.2: 10 ключей + Data Validation
  - `runCollection()`: валидация → подтверждение → POST /run (client_id, depth, with_labels, label_mode, force_relabel)
  - `checkStatus()`: GET /status → маппинг idle/starting/running/ok/error → defensive stats
  - `buildReportForDate()`: диалог даты → POST /run с report_only
  - Клиенты: showClients/addClient/showProfile
  - Провайдеры: manageProviders (GET + заглушки CRUD)
  - `_updateStatusCell()`, `_appendLog()`, `_friendlyError()`

### Результат:
- Синтаксис валиден (node --check)
- Тексты диалогов по §4.4
- Bearer-авторизация во всех запросах
- Defensive-обработка отсутствующих полей
- Нет 4-байтных emoji в меню
- Коммит: *(ожидает в текущей сессии вместе с T-007)*

---

## T-007: Серверные хвосты под UI-спеку (§5.2, §5.3) — report_only + finished_at/client_id

**Статус:** ✅ Done (2026-07-04)
**Приоритет:** высокий
**Затронутые файлы:** webhook.py, tests/test_webhook.py, docs/contracts.md, docs/progress.md, docs/techdebt.md

### Что сделано:

- **webhook.py:**
  - `POST /run`: новые поля `report_only: bool = False` и `report_date: str = "latest"` в `RunRequest`
  - При `report_only=True`: пропускает collect/save/label/export, вызывает только `reporter.build_report(date, force=True)`
  - `GET /status`: расширен `_last_run` полями `finished_at` (ISO, null пока идёт) и `client_id`
  - `finished_at` сбрасывается при старте нового прогона, заполняется в `finally` блоке
  - Ответ `/run` 202 теперь включает `client_id`
  - Обратная совместимость с телами без `report_only`/`report_date`

- **tests/test_webhook.py**: +8 тестов
  - TestReportOnly (4): проброс report_only, дефолт false, вызов reporter vs collector
  - TestStatusExtendedFields (4): finished_at/client_id в ответе, null во время прогона, начальное состояние

- **docs/contracts.md**: полные сигнатуры всех webhook-эндпоинтов
- **docs/techdebt.md**: удалена запись report_only (реализовано), обновлены записи stats и date/provider_chain
- **docs/progress.md**: запись о реализации

### Результат:
- 144/144 тестов зелёные (8 новых)
- report_only работает и тестирован
- finished_at/client_id в /status работают и тестированы
- Коммит: *(в текущей сессии, объединён с T-006)*
