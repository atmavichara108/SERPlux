
# Прогресс SERPlux

Обновлять в конце каждой рабочей сессии. Кратко, по делу.
Одна задача — одна свежая сессия. Не таскай контекст между этапами. Память — в docs/, не в чате. 

## Сделано
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
3. Мультиклиентность: расширение профиля (searchers, geos, subject_blocks в БД)
4. date/force_rebuild_report/provider_chain в /run — техдолг
5. CRUD /providers — техдолг

## Идеи на будущее (UI-этап)
- **Календарь версий**: каждый прогон собирает выдачу под датой, в отчёте копятся
  блоки-версии («Позиции Google на 21.6.2026» и т.д.). UI показывает даты как
  кликабельные точки в календаре (по аналогии с панелью Topvisor «Для автоматизации»),
  клик → открывает соответствующую версию-блок. Бэкенд готов: в БД каждая строка
  с полем `date`, в отчёте каждый блок подписан датой. Нужен только UI-слой поверх.

---

## Текущее состояние проекта (для тестового прогона на сервере)

**Дата:** 2026-07-04
**Версия API:** 1.0.0
**Тесты:** 144/144 passed
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
