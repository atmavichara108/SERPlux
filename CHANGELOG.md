# Changelog

Все значимые изменения проекта SERPlux.

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

---

## [1.0.0] - 2026-07-11

### Added
- **Мультиклиентность:** профиль клиента в БД (queries, regions_map, searchers, geos), CRUD API `/clients`, dropdown client_id в UI
- **Накопительный отчёт:** новый блок вставляется СВЕРХУ листа «Отчёт», хранится 10 версий, старые обрезаются автоматически
- **Эталон разметки:** парсер Лист1 → «Эталон разметки» (`parseList1ToEtalon`), импорт в БД через `/labels/import`
- **CANON.md:** источник истины по геометрии Лист1 и контракту эталона
- **report_layout.md:** канон раскладки отчёта (буферы 3+1, заливка на pos-колонке)
- **Тесты изоляции:** фикстура `mock_gspread` для всех тестов `build_report`, защита от случайной записи в боевую таблицу
- **Тесты раскладки:** `TestLayoutBuffers`, `TestSentimentFillCoordinates` (координаты B/G/J/M, буферы пустые)
- **Тесты накопления:** `TestAccumulativeReport` (insertDimension, нет clear())
- **verify.sh:** исключение INFO-метрик из grep, warning не роняет проверку (exit 0)
- **docs/release-1.0.md:** release notes v1.0
- **docs/roadmap-2.0.md:** roadmap v2.0 (7 кандидатов)

### Fixed
- **Демо-данные в exporter.py:** удалены test_rows_1/test_rows_2 и `__main__` блок, создан `scripts/demo_export.py` за `SERPLUX_DEMO=1`
- **Раскладка отчёта:** имя субъекта перенесено в url-колонку (правая), гео в pos-колонку (левая) — соответствует эталону Лист1
- **Заливка sentiment:** строго на ячейках номеров позиций (B/G/J/M), буферные колонки пустые
- **verify.sh IndentationError:** убран лишний отступ в inline-Python heredoc (блок [5/6] Database schema)
- **Тесты build_report:** добавлен мок gspread, убраны хрупкие try/except с проверкой GOOGLE_SHEET_ID

### Changed
- **reporter.py:** убран `worksheet.clear()`, добавлен накопительный режим через `insertDimension`
- **README.md:** исправлено число тестов (64 → 224), добавлен полный список эндпоинтов API (11 вместо 3), добавлены разделы мультиклиентности/накопительного отчёта/эталона
- **docs/user-guide.md:** переработана структура (сначала для пользователя, потом технические детали)
- **docs/progress.md:** добавлена запись о релизной фиксации v1.0

### Security
- **Изоляция тестов:** ни один тест не читает/пишет боевую БД (`/app/data/serplux.db`) или боевую таблицу
- **Grep-проверка:** ноль `example.com` в боевом коде (exporter.py, reporter.py)

---

## [0.9.0] - 2026-07-10

### Added
- **Двухрежимная разметка:** auto (справочник → LLM → neutral) + deep (заглушка для v2)
- **Разделение кэша и отчёта:** exporter пишет на лист «Лист2», reporter — на лист «Отчёт»
- **labeling_canon.md:** единый источник истины по разметке (sentiment, source, label_mode)
- **POST /labels/import:** батч-импорт эталонной разметки (идемпотентный, устойчивый к битым записям)
- **Run status persistence:** таблица `run_status`, статус переживает рестарт контейнера
- **HTTP-таймаут:** `gc.http_client.timeout = (10, 60)` в exporter.py и reporter.py
- **Neutral confidence:** `confidence='uncertain'` для neutral fallback (пустой сниппет, ошибка провайдера)
- **OpenAPI:** сгенерирован `openapi.json` из FastAPI

### Fixed
- **label_mode CHECK:** расширен до `auto/deep/domains/snippets/full` (соответствие контракту)
- **initSettingsSheet:** resilient версия (`initSettingsSheetSafe`), не падает на боевом документе
- **Date parsing:** `_normalizeDateToString()` в apps_script.gs (конвертация Date-объектов в YYYY-MM-DD)

### Changed
- **storage.py:** дефолт `label_mode` в `insert_labels()` исправлен с `snippets` на `auto`
- **verify.sh:** добавлена проверка таблицы `run_status`

---

## [0.8.0] - 2026-07-08

### Added
- **Динамический reporter:** раскладка из профиля клиента (N субъектов → N×2 колонок + буферы)
- **Date normalization:** Apps Script конвертирует `=TODAY()` в YYYY-MM-DD
- **Этап 0:** config из профиля клиента (project_id, sheet_id, searchers, geos, regions_map из БД)

### Fixed
- **SUBJECT_BLOCKS:** данные клиента перенесены из config.py в профиль БД
- **regions_map:** файловый свап заменён на JSON-массив в профиле клиента

---

## [0.7.0] - 2026-07-04

### Added
- **CRUD /clients:** профили клиентов в БД
- **GET /providers:** read-only список LLM-провайдеров
- **GET /clients/{id}/dates:** список дат с данными
- **GET /topvisor/regions:** регионы Topvisor
- **report_only:** пропуск сбора/разметки, только построение отчёта
- **finished_at/client_id:** в ответе `/status`

### Fixed
- **Provider defaults:** `opencode-zen` вместо `zen` в apps_script.gs
- **force_rebuild_report:** исправлен JS-баг (всегда true)
- **db_path:** явная передача `storage.DB_PATH` во всех вызовах

---

## [0.6.0] - 2026-07-02

### Added
- **UI Google Sheets:** меню SERPlux, лист «Настройки», лист «Лог»
- **Трёхуровневая модель параметров:** конфиг/профиль → Sheets-меню → системные
- **Трёхрежимная разметка:** domains/snippets/full (позже сокращена до auto/deep)
- **Мультиклиентность (стратегия):** одна таблица на клиента

### Fixed
- **Apps Script triggers:** Installable Trigger для мультиаккаунтного доступа
- **Script Properties:** общие для всех пользователей (не индивидуальные)
- **Emoji ограничения:** только BMP (U+0000–U+FFFF) в строках меню

---

## [0.5.0] - 2026-07-03

### Added
- **Схема данных:** clients, positions, labels, domain_labels (версионирование, мультитенантность)
- **Миграционный скрипт:** `migrate.py` (бэкап → перенос results → positions/labels)
- **Версионирование меток:** label_version = MAX+1, retry на UNIQUE violation
- **domain_labels:** справочник размеченных доменов (источник истины для режима domains)
- **Тесты:** 64 теста (storage_schema, labeler_modes, migrate_idempotent)

### Fixed
- **Gemini-код:** полностью выпилен из репозитория

---

## [0.1.0] - 2026-06-15

### Added
- **Базовый пайплайн:** topvisor → collector → labeler → exporter → reporter
- **FastAPI webhook:** `/run`, `/status`, `/health`
- **Google Sheets интеграция:** Apps Script меню, выгрузка в листы
- **Docker:** Dockerfile + docker-compose.yml
- **CI:** GitHub Actions (pytest на push/PR)
