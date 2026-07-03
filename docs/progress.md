
# Прогресс SERPlux

Обновлять в конце каждой рабочей сессии. Кратко, по делу.
Одна задача — одна свежая сессия. Не таскай контекст между этапами. Память — в docs/, не в чате. 

## Сделано
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
- Спецификация LLM-провайдеров (фолбек-цепочка, мониторинг, админ-управление) — docs/ui-spec.md §1.6, §5.5–5.6

## Заблокировано / ждёт
- Широкий формат exporter — переработать контракт Row под него (низкий приоритет)
- Риск timeout при сборе больших групп (5+ регионов одной ПС):
  timeout вынесен в config (дефолт 900 сек), при проблемах увеличить

## Дальше по порядку
1. Мультиклиентность: профили клиентов в SQLite, API /clients
2. Мультипровайдерность: фолбек-цепочка LLM, API /providers
3. Закрытие техдолга (docs/techdebt.md)

## Идеи на будущее (UI-этап)
- **Календарь версий**: каждый прогон собирает выдачу под датой, в отчёте копятся
  блоки-версии («Позиции Google на 21.6.2026» и т.д.). UI показывает даты как
  кликабельные точки в календаре (по аналогии с панелью Topvisor «Для автоматизации»),
  клик → открывает соответствующую версию-блок. Бэкенд готов: в БД каждая строка
  с полем `date`, в отчёте каждый блок подписан датой. Нужен только UI-слой поверх.
