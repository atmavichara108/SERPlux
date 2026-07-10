
# Лог архитектурных решений (ADR)

## 2026-07-10 — ADR: Восстановлен изолированный одноразовый импорт эталона

**Контекст:** Ранее удалённый разовый импорт эталонной разметки Михаила не
доехал до БД целиком: в `domain_labels` оказалось только 3 записи `source='manual_l1'`
из ~421. Нужно было дострелить оставшиеся записи, не оставляя одноразовый код
в production-меню/API и не ломая существующее меню SERPlux.

**Решение:**
1. Восстановлен `POST /labels/import` в `webhook.py` как строго изолированный,
   идемпотентный и устойчивый к битым записям эндпоинт:
   - принимает батч `{domain, query, geo, sentiment, source}`;
   - upsert по PK `(domain, query, geo)` через `storage.upsert_domain_label`;
   - невалидные записи пропускаются по одной, не роняя весь батч;
   - возвращает сводку `processed/imported/skipped/errors`.
2. В `apps_script.gs` добавлена функция `importEtalonToDb()`:
   - запускается вручную через Run в редакторе Apps Script;
   - НЕ добавляется в `onOpen` / меню SERPlux;
   - читает уже распарсенный лист «Эталон разметки»;
   - определяет колонки по заголовкам, при неизвестной структуре логирует и останавливается;
   - шлёт батчами по 100 строк, не прерывается на ошибках батчей.
3. После успешного импорта одноразовый код остаётся в репозитории как
   задокументированная возможность экстренного перелива эталона, но не
   попадает в регулярное меню/пайплайн.

**Почему:**
- Нужно было дострелить существующий эталон, не перепарсивая исходный Лист1.
- Функция запускается вручную и изолирована — не влияет на ежедневный UI.
- Идемпотентность позволяет безопасно перезапустить импорт при обрыве.
- Устойчивость к битым записям устраняет причину, по которой предыдущий импорт мог оборваться.

**Следствия:**
- `POST /labels/import` снова доступен, но не используется в регулярном UI.
- `importEtalonToDb()` — единственный «ручной» вход; при желании можно удалить
  после окончательного перелива всех эталонов.
- Обновлены `docs/contracts.md`, `docs/progress.md`.

**Затронутые файлы:**
- `webhook.py` — эндпоинт `POST /labels/import`
- `apps_script.gs` — функция `importEtalonToDb()`
- `tests/test_webhook.py` — тесты импорта
- `docs/contracts.md`, `docs/decisions.md`, `docs/progress.md`

---

## 2026-07-10 — ADR: Разовый импорт не хранится в коде репозитория

**Контекст:** Было реализовано одноразовое решение для импорта ручной разметки Михаила из Google Sheets (Лист1) в `domain_labels` через Apps Script + бэкенд `/labels/import`. После выполнения встал вопрос: оставить код в репозитории как артефакт или удалить.

**Решение:**
1. Одноразовые скрипты импорта не хранятся в production-коде репозитория.
2. `scripts/importManualLabels.gs`, пункт меню в `apps_script.gs` и `POST /labels/import` в `webhook.py` удалены из `main`.
3. Базовая таблица `domain_labels` и функции `storage.py` оставлены — они нужны для режима `domains` и двухрежимной разметки.
4. Ручная разметка `manual_l1` заполняется вне приложения (SQL/разовый скрипт/админка). В production-коде нет массового импортного эндпоинта.

**Почему:**
- Поддерживаемость: одноразовый код со временем превращается в мёртвый груз и путает будущих разработчиков.
- Чистота контрактов: API-эндпоинты отражают регулярные операции, а не разовые задачи.
- Безопасность: массовый импорт с записью в справочник-источник-истины не должен висеть в открытом API без явной необходимости.

**Следствия:**
- В `apps_script.gs` таблицы (вне репозитория) код временно остался как артефакт и затрётся при следующем обновлении скрипта.
- Повторные импорты ручных разметок выполняются вне приложения.

**Затронутые файлы:**
- `scripts/importManualLabels.gs` — удалён
- `apps_script.gs` — откачен пункт меню и функция
- `webhook.py` — удалён `POST /labels/import`
- `tests/test_webhook.py` — удалены тесты импорта
- `docs/contracts.md`, `docs/progress.md` — задокументировано

## 2026-07-10 — ADR: Динамический reporter с профилем клиента

**Контекст:** Reporter был захардкожен под 4 субъекта (SUBJECT_BLOCKS) с фиксированной сеткой из 16 колонок. Это блокировало масштабирование на фабрику: каждый новый клиент требовал правки config.py и пересборки контейнера.

**Решение:**
1. Reporter теперь читает список субъектов из профиля клиента (`client.queries`), а не из статического `config.SUBJECT_BLOCKS`
2. Раскладка колонок вычисляется динамически: N субъектов → N×2 колонки (pos|url) + (N-1) разделителей
3. Новая функция `_build_subject_layout(queries)` строит раскладку с индексами для каждого субъекта
4. Порядок географических регионов берётся из `client.regions_map`, fallback на глобальный `GEO_ORDER`
5. `build_report()` теперь принимает параметры `client_id` и `db_path`, загружает профиль из БД
6. `config.SUBJECT_BLOCKS` и `config.COLS` помечены как `_DEPRECATED_*` (сохранены для обратной совместимости)

**Почему:**
- Фабричное решение: код не содержит данных клиента, профиль хранится в БД
- Масштабируемость: клиент с 2 субъектами → 5 колонок, с 7 субъектами → 18+ колонок, без правки кода
- Поддерживаемость: новый субъект добавляется через API `/clients/{id}`, не через правку config.py
- Тестируемость: введены тесты для 2, 4, 7 субъектов — проверяют корректность раскладки и отсутствие падений

**Статус:** Принято и реализовано

**Затронутые файлы:**
- `reporter.py`: переписан, добавлена `_build_subject_layout()`, `build_report()` принимает `client_id` и `db_path`
- `config.py`: `SUBJECT_BLOCKS` и `COLS` переименованы в `_DEPRECATED_*`
- `main.py`: передача `client_id` и `db_path` в `build_report()`
- `webhook.py`: передача параметров в `build_report()` (2 места)
- `migrate.py`: использует `_DEPRECATED_SUBJECT_BLOCKS` для seed профиля
- `tests/test_reporter.py`: 10 новых тестов (все 200+ тестов проходят)
- `tests/test_config.py`: обновлены для использования `_DEPRECATED_*`

---

## 2026-07-10 — Исправление: Apps Script нормализует дату при вводе =TODAY()

**Контекст:** При вводе `=TODAY()` в ячейку листа Настройки или диалог, Google Sheets парсит это как Date-объект JavaScript, а не строку "YYYY-MM-DD". Это приводило к криво передачи даты в webhook и неверной фильтрации отчётов.

**Решение:**
1. Добавлена функция `_normalizeDateToString(dateInput)` в `apps_script.gs`
2. Конвертирует Date-объекты, строки в других форматах в YYYY-MM-DD (UTC)
3. Обновлены функции:
   - `_readSettings()`: нормализует `date` и `report_date` при чтении из листа
   - `buildReportForDate()`: нормализует дату из диалога перед отправкой в API

**Почему:**
- Date-объекты из Google Sheets имеют разные строковые представления ("Fri Jul 10 2026", сериализованные форматы и т.д.)
- Reporter парсит дату как `datetime.strptime(date_str, "%Y-%m-%d")`, что падает на невалидные форматы
- Fallback на "latest" при ошибке парсинга, что вызывает получение старых данных (по дате последнего съёма)

**Статус:** Реализовано

**Затронутые файлы:**
- `apps_script.gs`: добавлена `_normalizeDateToString()`, обновлены `_readSettings()` и `buildReportForDate()`

---

## ADR-NNN: Two-mode labeling (auto + deep, с fallback на neutral)

**Контекст:** Переход от трёхрежимной (domains/snippets/full) к двухрежимной системе разметки тональности для упрощения логики и явного разделения источников размечки.

**Решение:**
- **Режим AUTO (дефолт):** domain_labels кэш (справочник доменов) → сниппет через LLM → neutral при ошибке
  - Нулевая стоимость если домен есть в справочнике
  - LLM вызывается только для новых/несизвестных доменов
  - neutral как fallback при ошибке провайдера (маркер неуверенности LLM)
  - Источник `domain_labels`: `snippet` для новых доменов, `manual_l1` для ручной разметки
- **Режим DEEP:** обработка только строк с `sentiment=='neutral'` (для v2 — заход на страницу)
  - Сейчас — заглушка, возвращает sentiment без изменений
  - Подготовка к разметке по полному контенту страницы в будущих версиях
  - Источник: `page` (зарезервирован для полного текста)
- **manual_l1 приоритетен:** при upsert в `domain_labels` — `manual_l1` не перезаписывается `snippet`/`page`
  - Ручная разметка L1 — источник истины, её величина сохраняется
  - Автоматические источники могут обновляться при переразметке (`force_relabel`)
- **neutral как маркер:** `sentiment=None` означает, что LLM не смог определить тональность или произошла ошибка
  - Пустой сниппет → пропускаем с log WARNING
  - Ошибка провайдера → fallback на neutral (заполняется с `confidence='uncertain'`)

**Почему:**
- Упрощение: вместо трёх режимов (domains/snippets/full) — два (auto/deep), понятная иерархия
- Иерархия источников: справочник (свободно) → LLM по сниппету → fallback на neutral
- Явность: neutral = маркер неуверенности или отсутствия данных для последующей разметки v2
- Безопасность: manual_l1 никогда не перезаписывается автоматикой, можно доверять эталонной разметке
- Масштабируемость: легко добавить режим `full` в v2 (обработка DEEP по контенту страницы)

**Статус:** Принято

---

## 2026-07-08 — ADR: Полный профиль клиента в БД заменяет hardcoded config + файловый свап

**Контекст:** Три параметра, специфичных для конкретного клиента, оставались
в коде/окружении: `project_id` из `.env`, `SUBJECT_BLOCKS` (список субъектов)
в `config.py`, `regions_map` — имя файла-свапа (`regions_map_client1.json`).
Для интерпрайз-мультиклиентности это неприемлемо: код не должен содержать
данных конкретного клиента, а переключение между клиентами не должно требовать
пересборки Docker или правки файлов.

**Решение:**
1. Таблица `clients` расширена полями профиля: `queries` (JSON-массив
   `{key, display}`), `regions_map` (JSON-массив регионов), `searchers`
   (JSON-список), `project_id`.
2. `migrate.py` делает идемпотентный `ADD COLUMN IF NOT EXISTS` и выполняет
   разовый seed клиента `28938353` (`Sudheimer Group`) из:
   - `queries` — `config.SUBJECT_BLOCKS` (только `key`/`display`, без `pos`/`url`);
   - `regions_map` — файла `regions_map_client1.json`;
   - `searchers` — уникальных `searcher` из `regions_map_client1.json`;
   - `project_id` — `TOPVISOR_PROJECT_ID` из `.env`.
3. Seed существующего клиента (`28938353` уже есть в БД с пустым профилем)
   выполняется через `UPDATE`, не создавая дубликат.
4. Боевые данные с мусорного `client_id='default'` переносятся на `28938353`
   через `UPDATE` с предварительным `GROUP BY client_id` и разрешением
   дубликатов. Перед `DELETE` мусорного клиента выполняется верификация,
   что у него 0 дочерних записей — каскадное удаление исключено.
5. `storage.get_client` возвращает распарсенные JSON-поля; defensive: пустое
   значение → `[]`. `regions_map` может быть JSON-массивом или legacy-строкой
   (имя файла) — для обратной совместимости.
6. `webhook._build_client_config` и `main.py` строят рабочий конфиг из профиля
   клиента (`get_client`), fallback на `DEFAULT_CONFIG`/`env` только если
   поле в профиле пустое.
7. `collector.py` получает `regions_map` как список напрямую из профиля; если
   передана строка (legacy) — читает файл, fallback на `REGIONS_MAP` env →
   `regions_map.json`.

**Почему:**
- Фабричное решение: код не содержит данных клиента, профиль хранится в БД.
- Идемпотентность: повторный `migrate` не плодит дубликатов и не ломает данные.
- Безопасность переноса: бэкап `.preseed` + проверка GROUP BY + верификация
  отсутствия дочерних записей перед DELETE исключают потерю боевых данных.
- Обратная совместимость: legacy-строка `regions_map` и пустой профиль
  продолжают работать.

**Последствия:**
- `config.SUBJECT_BLOCKS` остаётся в `config.py`, но теперь используется
  только как источник `queries` при seed и как раскладка отчёта (`pos`/`url`
  для `reporter.py`). Данные клиента (`key`/`display`) дублируются в профиле БД.
- `regions_map_client1.json` продолжает лежать в репо, но его содержимое
  копируется в БД при seed; файл остаётся эталоном/резервной копией.
- Новые клиенты добавляются через API `/clients` с полным профилем,
  без правки кода.

**Затронутые файлы:** `migrate.py`, `storage.py`, `webhook.py`, `main.py`,
`collector.py`, `tests/test_migrate_idempotent.py`, `tests/test_storage_schema.py`,
`tests/test_webhook.py`, `tests/test_collector.py`, `docs/contracts.md`,
`docs/decisions.md`, `docs/progress.md`, `docs/techdebt.md`, `docs/deploy.md`.

## 2026-07-06 — ADR: Гибридная модель деплоя — агенты локально, пользователь через SSH

**Контекст:** Возникла путаница с деплоем: команда `/deploy` и агент `infra-dev`
были описаны так, будто агент работает на сервере и сам выполняет `docker compose`.
На практике агенты (plan, build, infra-dev) работают на локальной машине разработчика
и не имеют SSH-доступа к боевому серверу. Попытка агента выполнить `docker compose build`
локально привела к проверке локальной группы `docker`, а не к деплою на сервер.

**Решение:**
1. Деплой на сервер выполняется пользователем вручную через SSH: `git pull` →
   `docker compose build` → `docker compose up -d` → `docker compose exec ... migrate.py`.
2. Агент `infra-dev` проверяет локальные файлы (`Dockerfile`, `docker-compose.yml`,
   `.env.example`) на консистентность и готовит чек-лист команд для копипаста.
3. Документируем реальный процесс в `docs/deploy.md` и обновляем `.opencode/command/deploy.md`,
   `.opencode/agents/infra-dev.md`.

**Почему:**
- Безопасность: агент не получает доступ к боевому серверу и секретам.
- Прозрачность: пользователь видит каждую команду, выполняемую на сервере.
- Контроль: rollback, бэкап БД, паузы между шагами — под контролем пользователя.
- Простота: не нужно настраивать SSH-ключи/CI/CD для агента.

**Последствия:**
- Создан `docs/deploy.md` — полная инструкция деплоя.
- `.opencode/command/deploy.md` переписан под гибридную модель.
- `.opencode/agents/infra-dev.md` уточнён: агент готовит чек-листы, не деплоит.
- `Dockerfile` дополнен `migrate.py` для запуска миграции внутри контейнера.
- Zero-downtime deploy пока не реализован — сервис останавливается на время `build`/`up`.

**Затронутые файлы:** `docs/deploy.md`, `.opencode/command/deploy.md`,
`.opencode/agents/infra-dev.md`, `Dockerfile`, `docs/progress.md`, `docs/techdebt.md`

## 2026-07-03 — ADR: Провайдеры в config.py, read-only endpoint, CRUD отложен

**Контекст:** В проекте используется один LLM-провайдер — OpenCode Zen с бесплатной
DeepSeek-моделью. Ранее провайдер был хардкодом в `labeler.py` (константы `ZEN_MODEL`,
`ZEN_ENDPOINT`) — добавление второго провайдера требовало правки кода.

**Решение:**
1. Провайдеры описываются в `config.py` словарём `PROVIDERS` (ключ = id, значение =
   endpoint, model, api_key_env_var, enabled, priority, models).
2. `labeler.py` читает `config.PROVIDERS`, строит фолбек-цепочку из `enabled=True`,
   сортирует по `priority`. Хардкод Zen/DeepSeek удалён.
3. Реализован только `GET /providers` (read-only). CRUD-эндпоинты (`POST`/`PUT`/`DELETE`)
   отложены — при одном провайдере они не нужны, управление через `.env` + `config.py`.

**Почему:**
- Конфигурация провайдера — параметр деплоя (endpoint, ключ, модель), а не
  пользовательская настройка. Хранение в `config.py` (через `git`) даёт версионирование
  и code review изменений.
- CRUD через API добавит сложность без подтверждённой потребности: сейчас один
  провайдер, и конфигурация меняется только при деплое.
- `config.py` не зависит от БД = нет миграций при добавлении провайдера.

**Последствия:**
- Новый провайдер добавляется одной записью в `config.PROVIDERS` без правки `labeler.py`.
- Переменная окружения для ключа указывается в `api_key_env_var` — ключ не хранится
  в репозитории.
- `POST/PUT/DELETE /providers` могут быть добавлены позже, если появится админ-интерфейс.

**Затронутые файлы:** config.py, labeler.py, webhook.py, tests/test_webhook.py,
tests/test_labeler_modes.py, docs/contracts.md, docs/decisions.md, docs/progress.md

## 2026-07-03 — ADR: Дефолт label_mode — 'domains' (решение Q4)

**Контекст:** ADR «Схема данных разметки» (2026-07-03) зафиксировал дефолт
`label_mode = 'snippets'`. Решение заказчика Q4: первый уровень разметки
по умолчанию — `domains` (без LLM, нулевая стоимость), `snippets`/`full`
включаются только явно.

**Решение:** Дефолт `label_mode` в `POST /run` (`webhook.py`, `RunRequest`)
и в `main.run()` — `'domains'`. Контракт `labeler.label()` (docs/contracts.md)
сохраняет дефолт сигнатуры `'snippets'` для внутренних вызовов; внешние
точки входа (`/run`, `main.run`) переопределяют на `'domains'`.

**Почему:**
- `domains` — нулевая стоимость, подходит для массового прогона по умолчанию.
- `snippets`/`full` — осознанный выбор пользователя (включение LLM/захода на страницу).
- Соответствует трёхуровневой модели разметки (ADR 2026-07-02).

**Последствия:**
- Старый контракт `/run` без `label_mode` теперь по умолчанию запускает
  режим `domains` (ранее — `snippets`). Формат запроса сохранён, семантика
  дефолта изменилась сознательно.
- Параметры `client_id` и `force_relabel` пробрасываются в `storage.save()`
  и `labeler.label()` без изменения внешнего контракта.

**Затронутые файлы:** webhook.py, main.py, docs/decisions.md

## 2026-07-03 — ADR: Схема данных разметки — версионирование + мультитенантность

**Контекст:** Текущая схема хранит всё в одной таблице `results` с полем `label`.
Функция `update_labels()` делает UPDATE по UNIQUE-ключу — перезатирает предыдущую метку.
Повторная разметка той же выдачи теряет историю. Мультитенантность отсутствует.

Требования заказчика:
1. `label_mode` — один режим на прогон (domains/snippets/full), не конвейер
2. Версионирование: повторная разметка НЕ перезатирает, новая = новая версия
   с пометкой (режим + версия + timestamp)
3. Тональность — явное поле
4. Мультитенантность: таблица `clients`, `client_id` как FK во всех таблицах
5. 64 pytest-теста не должны сломаться

**Решение:**

### Схема таблиц

**`clients`** — профили клиентов:
```sql
CREATE TABLE clients (
    client_id   TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    project_id  INTEGER,
    sheet_id    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**`positions`** — выдача (сырые данные из Topvisor):
```sql
CREATE TABLE positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    date          TEXT NOT NULL,
    searcher      TEXT NOT NULL,
    query         TEXT NOT NULL,
    geo           TEXT NOT NULL,
    region_index  INTEGER NOT NULL,
    position      INTEGER NOT NULL,
    url           TEXT NOT NULL,
    domain        TEXT NOT NULL,
    snippet       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(client_id, date, searcher, query, geo, position, url)
);
```

**`labels`** — разметка (версионированная):
```sql
CREATE TABLE labels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    client_id      TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    label_mode     TEXT NOT NULL CHECK(label_mode IN ('domains','snippets','full')),
    label_version  INTEGER NOT NULL,
    sentiment      TEXT CHECK(sentiment IN ('positive','negative','neutral')),
    confidence     TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(position_id, label_mode, label_version)
);
```

**`domain_labels`** — справочник размеченных доменов (источник истины для режима `domains`).
Мультиклиентность через ключ `(domain, query, geo)` без `client_id`: один и тот же домен
для разных клиентов в одном geo и по одному субъекту разделяет метку.
```sql
CREATE TABLE domain_labels (
    domain      TEXT NOT NULL,
    query       TEXT NOT NULL,           -- нормализованный key субъекта, lowercase
    geo         TEXT NOT NULL,           -- geo_name как в regions_map
    sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
    source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain, query, geo)
);
```

**Индексы:**
```sql
CREATE INDEX idx_pos_client_date ON positions(client_id, date);
CREATE INDEX idx_pos_url_query   ON positions(url, query);
CREATE INDEX idx_pos_client_url  ON positions(client_id, url);
CREATE INDEX idx_lbl_position    ON labels(position_id);
CREATE INDEX idx_lbl_client_mode ON labels(client_id, label_mode);
CREATE INDEX idx_lbl_latest      ON labels(position_id, label_mode, label_version DESC);
CREATE INDEX idx_domlbl_domain_query ON domain_labels(domain, query);
CREATE INDEX idx_domlbl_geo ON domain_labels(geo);
```

### Версионирование

Ключ: `(position_id, label_mode, label_version)` — составной UNIQUE.
- `label_version` — монотонно растущий integer, вычисляется как `MAX(label_version) + 1`
  для пары `(position_id, label_mode)` при вставке
- Разные режимы = независимые ветки версий (snippets v2 не отменяет domains v1)
- **label_version не сквозной между датами:** т.к. position_id включает `date`
  в UNIQUE-ключ таблицы positions, при новом прогоне за новую дату создаётся
  новый position_id и версии начинаются с 1. Это соответствует требованию
  заказчика «версия за ту же дату».
- «Текущая» метка = последняя по `created_at DESC`
- История: `SELECT label_mode, label_version, sentiment, created_at FROM labels WHERE position_id = ?`

### Атомарность версии

`MAX(label_version)+1` подвержен гонке при конкурентных вставках (асинхронный /run).
Два потока могут одновременно прочитать MAX=3 и оба вставить version=4 → UNIQUE violation.

**Решение:** INSERT с retry на нарушение UNIQUE.
- Перед вставкой: `SELECT COALESCE(MAX(label_version),0)+1 FROM labels WHERE position_id=? AND label_mode=?`
- INSERT с вычисленным version. При `UNIQUE constraint failed` → повторить SELECT+INSERT (макс. 3 попытки).
- Почему не BEGIN IMMEDIATE: SQLite в режиме WAL (docker-compose) допускает параллельные чтения;
  IMMEDIATE блокирует всех читателей на время транзакции, что замедляет /status и get_history().
  Retry на UNIQUE — дешевле и не блокирует.
- При 3 неудачах — лог ERROR, метка пропускается (частичный сбой, не роняем прогон).

### Тональность

Поле `sentiment` в `labels` — явное, с CHECK-ограничением.
NULL = «разметка запущена, но LLM вернул ошибку».
В Row-дикте `label` сохраняется как алиас для обратной совместимости.

### Режим `domains`: справочник `domain_labels`

Режим `domains` берёт метку из справочника `domain_labels`, не вызывая LLM.
Для каждой позиции labeler ищет запись по `(domain, query, geo)`;
если запись есть — используется её `sentiment`.
Это источник истины для доменов, размеченных вручную, и позволяет не тратить
токены на уже достоверно классифицированные домены.

Поле `source` различает происхождение записи и задаёт приоритет обновления:
- `manual_l1` — ручная разметка (уровень L1). Не перезаписывается автоматическими
  источниками `snippet`/`page`; может перезаписать любую существующую запись.
- `snippet` — разметка на основе сниппета (LLM или эвристика).
- `page` — разметка на основе полного текста страницы.

`query` хранится в lowercase для нормализации; `geo` — geo_name из `regions_map`.

### Confidence

Поле `confidence` в `labels` — задел под микрофичу «нейронка не уверена».
Пока заполняется значением `'high'` по умолчанию.
В будущем labeler сможет проставлять `'uncertain'` для пограничных ответов LLM,
не требуя повторной миграции схемы.

### Миграция

Новая БД → сразу три таблицы + `domain_labels` + авто-клиент `'default'`.
Существующая БД:
**Шаг 0:** `cp serplux.db serplux.db.bak.YYYY-MM-DD`. DROP results только после
успешного переноса и верификации: `COUNT(*) results == COUNT(*) positions`.
1. INSERT INTO clients ('default', 'Default')
2. Создать positions, перенести данные из results
3. Перенести метки в labels (version=1, mode='snippets')
4. DROP TABLE results
5. Добавить поле `confidence`:
   ```sql
   ALTER TABLE labels
   ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high';
   ```
6. Создать справочник доменов (актуальная схема):
   ```sql
   CREATE TABLE domain_labels (
       domain      TEXT NOT NULL,
       query       TEXT NOT NULL,
       geo         TEXT NOT NULL,
       sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
       source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
       updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
       PRIMARY KEY (domain, query, geo)
   );
   CREATE INDEX idx_domlbl_domain_query ON domain_labels(domain, query);
   CREATE INDEX idx_domlbl_geo ON domain_labels(geo);
   ```
   Если существует старая схема (с `id`/`client_id`) — таблица пересоздаётся.
   Данные `domain_labels` — кэш, приоритет имеют ручные разметки L1.

`migrate.py` следует дополнить шагами 5–6, но **НЕ запускать на бою** без бэкапа
и проверки на копии БД.

64 теста не ломаются: test_imports (импорт), test_config (константы),
test_regions_map (JSON), test_parse_label (чистая логика) — не трогают схему БД.
__main__-блоки storage.py/labeler.py используют изолированные тестовые БД —
получат новую схему автоматически.

### Влияние на /run

Новые поля: `client_id` (дефолт 'default'), `label_mode` (дефолт 'snippets'),
`force_relabel` (дефолт false). Обратная совместимость сохранена.

### Влияние на get_history()

Расширенный Row: добавлены `sentiment`, `label_mode`, `label_version`.
По умолчанию — последняя метка на позицию (JOIN + MAX(created_at)).
Фильтр `label_version='all'` — все версии. Фильтр `client_id` — по клиенту.

**Последствия:**
- (+) История разметки сохраняется, сравнение версий
- (+) Мультитенантность заложена в схему
- (+) Разделение positions/labels — чистая архитектура
- (-) Все запросы к меткам требуют JOIN
- (-) `update_labels()` → `insert_labels()` (INSERT, не UPDATE)
- (-) Миграция существующей БД требует бэкапа

**Альтернативы отвергнуты:**
- Единая таблица с `label_version` — дублирование данных, нарушение нормализации
- Единый счётчик версий (без label_mode) — режимы независимы, нельзя сравнивать параллельно
- JSON-поле в `label_versions` — нет CHECK, сложно индексировать

**Примечание по реализации (2026-07-03):**
В рамках текущей задачи реализована только схема хранения и базовый режим `snippets`.
Режимы `domains` и `full` остаются заглушками: `labeler.label()` возвращает для них
`sentiment=None` и не вызывает LLM. Функционально продукт после миграции находится
на том же уровне, что и раньше, — на чистой версионированной схеме. Полноценная
реализация `domains`/`full` — отдельные задачи; открытые вопросы к заказчику
по этим режимам остаются (см. ui-spec.md Q4–Q6).

**Открытые вопросы:**
- Q1: UI для сравнения версий в Sheets?
- Q2: force_relabel — все строки или только дельта?
- Q3: Дефолтный клиент 'default' — приемлемо?
- Q4: Хранить стоимость разметки в labels?

**Затронутые файлы:** storage.py, labeler.py, webhook.py, main.py, migrate.py, docs/contracts.md

## 2026-07-02 — Apps Script: мультиаккаунтный доступ через Installable Trigger

Проблема: bound-скрипт авторизуется под аккаунтом владельца таблицы.
Разработчики с правами редактора не видят меню — onOpen() Simple Trigger
не срабатывает под их аккаунтом.

Решение: Installable Trigger создаётся программно через setupTriggers().
Каждый пользователь (владелец + разработчики) запускает setupTriggers()
ОДИН РАЗ под своим аккаунтом. Trigger работает под аккаунтом создателя,
меню появляется при открытии таблицы.

Для разработки: рекомендуется работать на копии таблицы под своим аккаунтом,
а не на боевой таблице заказчика. Копия → полный контроль, нет риска
сломать боевые данные.

## 2026-07-02 — Script Properties ОБЩИЕ для всех пользователей (факт, проверено)

Факт (проверено на живом деплое): PropertiesService.getScriptProperties() =
Script Properties = ОБЩИЕ для всех пользователей таблицы. Хранятся в проекте
скрипта в облаке Google. Устанавливаются ОДИН РАЗ.

НЕVERНО (ранее утверждалось): "Script Properties изолированы по аккаунтам,
у каждого свои secrets". Это описание User Properties (getUserProperties()),
которых в коде НЕТ.

Что индивидуально у каждого пользователя: только OAuth-разрешения. При первом
нажатии пункта меню, дёргающего UrlFetchApp (Запустить/Проверить статус),
Google один раз спросит согласие. Это происходит ИЗ таблицы, лезть в редактор
Apps Script и запускать функции вручную пользователю НЕ нужно.

onOpen разрешений не требует — меню появляется автоматически при открытии
таблицы (подтверждено: заказчик открыл с телефона, меню появилось без действий).

Действие для заказчика: ничего настраивать не нужно. При первом запуске
нажать «Разрешить». Секреты и URL настроены один раз разработчиком.

## 2026-07-02 — Apps Script: Simple Trigger onOpen, emoji-ограничения, инициализация настроек

Решение: onOpen() — Simple Trigger, срабатывает автоматически при открытии таблицы.
НЕ запускать вручную через кнопку Run в редакторе Apps Script — в этом контексте
SpreadsheetApp.getUi() не работает, меню не создаётся.

4-байтные emoji (U+10000+) в строках addItem могут искажаться при копировании
через браузер → синтаксическая ошибка → silent failure. Использовать только
ASCII или двухбайтные Unicode (U+0000–U+FFFF) в строках меню.

Лист "Настройки" инициализируется функцией initSettingsSheet() — создаёт шаблон
с ключами на латинице (regions_map, depth, label_mode, client_id, date, status).
Без шаблона пользователь не знает формат ключей.

Следствие: все addItem строки — без 4-байтных emoji. Инструкция по установке
явно говорит "НЕ запускать onOpen() вручную".

## 2026-07-02 — Архитектура LLM-провайдеров: фолбек-цепочка, мониторинг, админ-управление

Решение: система LLM-провайдеров параметризуется через профили в БД (таблица `providers`).
Каждый провайдер: endpoint, model, api_key_env_var, enabled, priority, cost_per_1k.

Фолбек-цепочка: список provider_id по приоритету (priority=1, 2, 3…). При ошибке
первого провайдера (сеть, 429, таймаут) — автоматически пробуется следующий.
Цепочка настраивается на уровне клиента (provider_chain в ClientProfile).

API-ключи хранятся ТОЛЬКО в .env. В профиле провайдера — имя переменной
(api_key_env_var), не значение. Это исключает утечку ключей через API.

Мониторинг: таблица `provider_stats` — total_requests, successful, failed,
total_cost_usd, avg_latency_ms, last_used. Доступ через GET /providers/{id}/stats.

Управление: добавление/удаление/изменение провайдеров — только через API
с админ-токеном (отдельный WEBHOOK_ADMIN_SECRET или проверка роли).

Текущий Zen мигрирует в профиль: provider_id=zen, priority=1, enabled=true,
endpoint=https://opencode.ai/zen/v1/chat/completions, model=deepseek-v4-flash-free,
api_key_env_var=OPENCODE_API_KEY.

Альтернатива отвергнута: хардкод провайдеров в labeler.py — не масштабируется,
невозможно A/B тестирование, нет мониторинга.
Следствие: labeler.py требует рефакторинга — абстракция провайдера,
фолбек-цепочка, запись статистики.

## 2026-07-02 — UI-спецификация: трёхуровневая модель параметров

Решение: параметры системы разделены на три слоя по частоте изменения:
- **Конфиг/профиль клиента** (редко): project_id, regions_map, searchers, geos, subject_blocks, cols_total, geo_order, geo_display, sheet_*_name, timeout_sec, report_depth.
- **Sheets-меню + API** (каждый прогон или при необходимости): client_id, depth, date, with_labels, label_mode, force_relabel, force_rebuild_report, report_date.
- **Системные** (один раз при деплое): WEBHOOK_SECRET, OPENCODE_API_KEY, DB_PATH.

Почему: смешивание слоёв в одном месте (всё в .env или всё в Sheets) либо перегружает интерфейс, либо требует пересборки Docker при смене клиента. Трёхуровневая модель даёт заказчику контроль над нужными параметрами без доступа к серверу.

## 2026-07-02 — UI-спецификация: трёхрежимная модель разметки (label_mode)

Решение: разметка тональности параметризуется через `label_mode` с тремя значениями:
- `domains` — по спискам доменов, без LLM, нулевая стоимость.
- `snippets` — по сниппету из Topvisor, LLM (текущий дефолт).
- `full` — заход на страницу, LLM с полным текстом, высокая стоимость.

Почему: заказчик упомянул трёхэтапную модель анализа. Фиксируем её как enum глубины анализа, а не как три отдельных флага — это проще для интерфейса и API.
Статус: режимы `domains` и `full` не реализованы. Реализация — после согласования с заказчиком (вопросы Q4–Q6 в ui-spec.md).
Следствие: текущий labeler.py реализует только `snippets`. Добавление `domains` и `full` — отдельные задачи.

## 2026-07-02 — UI-спецификация: мультиклиентность через профили в БД

Решение: каждый клиент описывается профилем (ClientProfile) — записью в таблице `clients` SQLite. Профиль содержит все параметры, специфичные для клиента: project_id, regions_map_file, searchers, geos, subject_blocks, cols_total, geo_order, geo_display, sheet_id, sheet_*_name, timeout_sec, report_depth.

Почему: текущая модель (параметры в config.py + regions_map.json) не масштабируется на несколько клиентов без правки кода. Профили в БД позволяют добавлять клиентов через API без пересборки Docker.
Альтернатива отвергнута: JSON-файлы профилей (`profiles/<client_id>.json`) — проще, но нет API для управления, нет транзакционности.
Статус: частично реализовано. Таблица `clients` и CRUD-эндпоинты `/clients` готовы; остальные поля профиля (searchers, geos, subject_blocks и др.) будут добавляться по мере перехода main.py/config.py на профили.
Следствие: API `/clients` позволяет управлять базовым профилем (client_id, client_name, project_id, sheet_id); config.py пока остаётся источником раскладки отчёта.

## 2026-07-02 — UI-спецификация: стратегия мультиклиентности в Sheets

Решение: рекомендуемая стратегия — «одна таблица на клиента» (не единая таблица с выпадающим списком). Каждая таблица имеет свой Apps Script с захардкоженным `client_id` в листе «Настройки».

Почему: проще в настройке, изоляция данных клиентов, нет риска случайного переключения. Единая таблица с выпадающим списком — следующий этап при 3+ клиентах.
Следствие: при добавлении клиента нужно скопировать шаблон таблицы и настроить Apps Script. Процесс описан в ui-spec.md §3.4.

## 2026-07-02 — UI-спецификация: расширение API /run и /status

Решение: целевой контракт `/run` добавляет поля: `client_id`, `date`, `label_mode`, `force_relabel`, `force_rebuild_report`, `report_date`, `report_only`. Поле `regions_map` сохраняется для обратной совместимости, но устаревает при наличии `client_id`.
Целевой `/status` добавляет: `finished_at`, `client_id`, `stats` (collected/saved_new/labeled/exported).
Новые эндпоинты: `GET /clients`, `POST /clients`, `GET /clients/{id}`, `PUT /clients/{id}`.

Почему: текущий API минимален (regions_map + with_labels + depth). Мультиклиентность и расширенные параметры требуют расширения контракта.
Статус: частично реализовано. Эндпоинты `/clients` (GET/POST/GET/{id}/PUT/{id}) реализованы в `webhook.py` с Bearer-авторизацией; базовые поля профиля хранятся в `clients`. Остальные поля `/run` и `/status` — в работе.

Каждое решение: дата, что решили, почему, какие альтернативы отвергли.
Дописывать сверху (новые решения вверху). Не удалять старые.

## 2026-06-22 — Фикс ревью: labeler в пайплайне, lazy credentials, идемпотентность
Решение (по результатам ревью):
- main.py: пайплайн collect → save → label → update_labels → export
- storage.py: `_ensure_db()` вызывается перед `save()` — БД инициализируется автоматически
- labeler.py: убран двойной `get_cached_label()` — кэш проверяется только в `label()`
- topvisor.py: credentials загружаются лениво через `_get_credentials()`, не при импорте
- exporter.py: идемпотентность — пропуск если даты уже есть в Sheet
- reporter.py: идемпотентность — пропуск если отчёт за дату уже на листе
- reporter.py: `assert` заменён на `raise ValueError`
- Убран `logging.basicConfig()` из модулей (только в main.py)

## 2026-06-22 — Метки сохраняются через update_labels (UPDATE), а не save (INSERT OR IGNORE)
Решение: новая функция update_labels(rows) делает UPDATE поля label для существующих
строк по UNIQUE-ключу (date, searcher, query, geo, position, url). Строки с
label=None пропускаются — не затирают существующие метки.
Почему: save() использует INSERT OR IGNORE для идемпотентности сырых данных.
Это правильно для первичной записи, но labeler проставляет label УЖЕ существующим
строкам — INSERT OR IGNORE их игнорирует, метки не сохранялись (2177 строк с NULL).
Следствие: пайплайн = save(сырые данные) → labeler → update_labels(размеченные строки).
save — только сырые данные, update_labels — только метки.

## 2026-06-22 — Разметка через Zen qwen3.6-plus основной, Gemini опциональный фолбек
Решение: labeler.py использует Zen (qwen3.6-plus) как основной провайдер,
Gemini — опциональный фолбек. PROVIDER_CHAIN = ["zen", "gemini"].
Эндпоинт: https://opencode.ai/zen/v1/chat/completions, OpenAI-совместимый формат.
Модель: "qwen3.6-plus" (без префикса opencode/). Auth: Bearer OPENCODE_API_KEY.
Парсинг ответа: регэксп \b(positive|negative|neutral)\b из content (не reasoning_content),
т.к. qwen думает вслух в отдельном поле. LLM_PAUSE = 1с (Zen rate limit мягче).
Почему: Gemini free у клиента не активен (429 limit:0), Zen работает стабильно
и дёшево (~0.4 цента/запрос).
Следствие: Gemini требует GEMINI_API_KEY в .env; если ключа нет — Gemini пропускается.

## 2026-06-22 — Матрица отчёта режется по REPORT_DEPTH=10 для сопоставимости ПС
Решение: reporter.py ограничивает диапазон позиций значением REPORT_DEPTH=10
при построении матрицы. Сырые данные в БД хранятся полностью (Яндекс топ-50,
Google топ-10). EMPTY_GEO_DEPTH привязан к REPORT_DEPTH.
Почему: Яндекс собирает топ-50 (200 строк/регион), Google топ-10. Без ограничения
матрица становится несопоставимой — разные ПС показывают разную глубину.
Следствие: глубина отображения — будущая опция интерфейса serplux; пока захардкожена 10.

## 2026-06-22 — UK называется 'Великобритания' в yandex_ru и 'Лондон' в google/yandex_com
Решение: оба ключа мапятся на "United Kingdom" через GEO_DISPLAY в config.py.
GEO_ORDER содержит оба русских ключа ("Великобритания", "Лондон"), reporter
группирует по _get_geo_display(), поэтому в отчёте будет одна секция "United Kingdom"
с данными из обоих searcher.
Почему: yandex_ru использует регион "Великобритания" (region_key=102), а google
и yandex_com — "Лондон" (region_key=10393). Это разные регионы в Topvisor,
но одна страна. Раздельный показ в отчёте не нужен.
Следствие: GEO_DISPLAY обновлён, GEO_ORDER = ["Литва", "Германия", "Великобритания",
"Лондон", "Объединённые Арабские Эмираты", "Кипр"]. Legacy-ключи ("ОАЭ",
"Объединённые Эмираты", "Кипр Eng", "Кипр Greek") сохранены для совместимости.

## 2026-06-21 — Тесты работают на изолированной БД, боевую serplux.db не трогают
Решение: все функции storage.py (_init_db, save, get_cached_label, get_history)
принимают опциональный параметр db_path с дефолтом serplux.db.
Тесты в __main__ создают и удаляют test_serplux.db, боевую БД не трогают.
test_serplux.db добавлен в .gitignore (покрывается маской *.db).
Почему: раньше тесты удаляли и перезаписывали боевую serplux.db с реальными
данными заказчика. Это опасно — можно потерять данные.
Следствие: при запуске python storage.py или python labeler.py тестовая БД
создаётся, используется и удаляется автоматически.

## 2026-06-21 — Кэш меток по паре (url+query), не по url
Решение: get_cached_label(url, query) ищет метку по комбинации URL + query,
а не только по URL.
Почему: один и тот же URL для разных субъектов (query) может иметь разную
тональность. Например, news.example.com/article может быть негативным для
субъекта A (статья про скандал) и нейтральным для субъекта B (упоминание
в списке компаний).
Следствие: все вызовы get_cached_label обновлены на новый сигнатуру.

## 2026-06-21 — Разметка: Gemini free основной, Zen дешёвый фолбек, пауза 4с
Решение: labeler.py использует Gemini 2.0 Flash как основной провайдер,
при ошибке (429, таймаут, сеть) — фолбек на Zen-модель через OpenAI-совместимый
API. Пауза 4 секунды между реальными вызовами LLM для защиты от rate limit
(Gemini free ~15 req/min). Строки из кэша не вызывают LLM и не ждут паузу.
Почему: бесплатный лимит Gemini ограничен, нужна защита от 429. Фолбек
гарантирует, что разметка не упадёт целиком при сбое основного провайдера.
Следствие: при массовом прогоне на больших данных учитывать паузу 4с × кол-во
строк без кэша. Кэш критически важен для экономии лимитов.

## 2026-06-21 — reporter захардкожен под 4 субъекта и 16-колоночную сетку клиента
Решение: reporter.py использует фиксированный SUBJECT_BLOCKS с точными индексами колонок
(16 колонок, 0-indexed) и GEO_ORDER из config. Динамика subjects/geo отключена.
Почему: эталон заказчика (лист "Лист1") имеет жёсткую раскладку колонок с пустыми
разделителями между субъектами (4/1/1/1). Динамический расчёт колонок ломает визуальное
совпадение с эталоном. Пустые гео-секции рисуются всегда (EMPTY_GEO_DEPTH=10).
Следствие: добавить нового субъекта или изменить порядок гео — править SUBJECT_BLOCKS/GEO_ORDER
в коде. Вернуть динамику на этапе продуктизации, когда формат стабилизируется.

## 2026-06-20 — Двухслойная архитектура вывода: Данные + Отчёт
Решение: два листа в Google Sheet с разными форматами.
Лист "Данные" (Sheet1): плоский формат, источник истины, для машины/нейронки.
Лист "Отчёт": матрица-pivot для человека, формат заказчика (обязательный вид результата).
Почему: плоский формат удобен для обработки (фильтры, сортировка, LLM),
но заказчик работает с матрицей (позиции × субъекты по гео).
reporter.py строит отчёт из storage.get_history(), не из топвизора напрямую.
Сниппеты: парсим всегда (хранятся в базе), но в отчёте-матрице не показываем
(там только URL, как в референсе заказчика). Сниппеты видны в плоском листе.
Следствие: exporter пишет в "Данные", reporter пишет в "Отчёт".

## 2026-06-20 — main.py: порядок шагов и стратегия сбоев
Решение: пайплайн collect → save → export с частичной отказоустойчивостью.
Порядок: collect (обязателен) → save (желателен) → export (желателен).
Стратегия сбоев:
- collect упал → стоп (return 1), нечего сохранять
- save упал → продолжаем export (данные на руках)
- export упал → данные уже в БД, логируем ошибку
Почему: на VPS без человека прогон не должен падать с трейсбеком.
Если collect сработал — данные не теряем, даже если save/export частично упали.
Config пока из словаря DEFAULT_CONFIG в main.py (чтение из листа "Настройки" — этап 3).

## 2026-06-20 — Версионность exporter: новый прогон сверху
Решение: новая выдача вставляется блоком сверху (insert_rows на позицию после заголовка),
старые прогоны уезжают вниз = история прямо в таблице.
Почему: заказчик видит свежий результат сразу, история доступна скроллом вниз.
Не нужно отдельное версионирование или архивные листы.
Колонка названа "Субъект/Запрос" (не просто "Запрос"), т.к. query — это имя
персоны или компании (субъект мониторинга), а не поисковый запрос в обычном смысле.
Следствие: заголовок всегда на строке 1, данные начинаются со строки 2.

## 2026-06-20 — Идемпотентность collect(): подтверждено живым прогоном
Факт: run_check топвизора проверяет ВЕСЬ проект (все ПС × гео), не подмножество.
Подтверждено живым прогоном 2026-06-20: при запуске проверки с region_indexes=[1300]
топвизор всё равно пересобрал все регионы проекта.
Следствие: config searchers/geos экономит на чтении снимков (get_snapshot) и LLM-разметке,
но НЕ экономит на проверке топвизора — она всегда полная.
Дополнительно: сниппеты удорожают проверку (~8₽ vs 7₽ без сниппетов),
заказчик включил сниппеты сам в настройках проекта.

## 2026-06-19 — Структура snapshotsData: позиция в ключе
Решение: позиция извлекается из ключа snapshotsData формата "дата:позиция:region_index".
Почему: API возвращает позицию не как отдельное поле, а как часть ключа словаря.
Пример: "2026-06-19:1:1300" → позиция=1, region_index=1300.
Следствие: парсинг через split(":")[1], валидация через try/except.

## 2026-06-19 — region_key != region_index
Решение: region_key и region_index — разные параметры API, нельзя путать.
Почему: region_index (например 1300) — это идентификатор региона в проекте.
region_key (например 117) — это внутренний ключ региона в topvisor.
Для Google/Литва: region_index=1300, region_key=117, region_lang="lt", region_device=0.
Следствие: collector должен хранить полную карту параметров для каждого гео.

## 2026-06-19 — Сниппеты пустые, нужна карта параметров
Решение: сниппеты в снимке Google/Литва пустые (snippet_title="", snippet_body="").
Почему: возможно, для этого региона/поисковика topvisor не возвращает сниппеты.
Следствие: перед масштабированием на все гео собрать карту параметров снимка
(какие поля заполняются для каждого searcher_key × region_key).
Альтернатива: парсить сниппеты из другого API endpoint (отложено).

## 2026-06-19 — Источник данных: topvisor Snapshots
Решение: выдачу берём только через topvisor Snapshots API, двухшаговый сбор.
Почему: единый источник для Google и Яндекс, не воюем с капчей и прокси.
Отвергли: прямой парсинг (война с антиботом), SerpAPI/DataForSEO (запасной).

## 2026-06-19 — Формат результата: широкий, как референс заказчика
Решение: пишем в формате референса (субъект = блок колонок, новый прогон сверху).
Почему: заказчик уже так работает, сбор раз в неделю = новая версия блоком.
Отвергли: плоский формат — версионирование решается само, не нужен.

## 2026-06-19 — Интерфейс: Google Sheets, бэкенд на VPS
Решение: управление и результат в Sheets, Python-бэкенд на сервере, триггер кнопкой.
Почему: ноль инфраструктуры для заказчика, результат и управление в одном месте.
Отвергли: отдельный веб-фронт — переинжиниринг для текущего сценария.

## 2026-07-02 — ADR: Интерфейс SERPlux — только Google Sheets
**Контекст:** Слово «интерфейс» ошибочно интерпретировано как веб-фронт. Были созданы
агент `ui-dev` и команда `/interface` для реализации Web UI (FastAPI + Jinja2 + Tailwind).

**Решение:** Единственный UI — Google Sheets (Apps Script меню + лист «Настройки»).
`webhook.py` остаётся триггер-endpoint для Apps Script, не основа дашборда.
Веб-UI не строим без явного запроса заказчика и отдельного ADR.

**Почему:**
- Заказчик работает через Google Sheets — это его привычная среда
- Sheets уже реализован (Apps Script, меню SERPlux, лист Настройки)
- Веб-фронт = дополнительная сложность без подтверждённой потребности
- webhook.py — API-триггер, а не UI-фреймворк

**Затронутые файлы:** AGENTS.md, docs/ui-spec.md (Q20), docs/progress.md,
.opencode/agents/ui-dev.md, .opencode/command/interface.md

**Статус агентов:** ui-dev и `/interface` — приостановлены (не удалены, на случай если заказчик попросит).

## 2026-07-10 — ADR: Минимальная рабочая инициализация листа «Настройки» в Apps Script
**Контекст:** `initSettingsSheet()` в `apps_script.gs` стабильно падает с ошибкой
«Сервису Таблицы недоступен» на боевом документе. Попытки лечения
(try-catch, guard, `_normalizeDateToString`, пересоздание листа) не помогли.
При этом экспериментальная последовательность из `testFindCrash`
(шаблон + `setDataValidation` по блокам + `setActiveSheet`) на том же документе
отрабатывает успешно.

**Решение:** Ввести боевую функцию `initSettingsSheetSafe()`, которая выполняет
только доказанно рабочий минимум:
1. `clearContents` (или `insertSheet`, если листа нет);
2. `setValues(SETTINGS_TEMPLATE)`;
3. `setDataValidation` только для `client_id`, `depth`, `with_labels`, `label_mode`;
4. `setActiveSheet` + `toast`.

Все операции — в отдельных `try-catch`. Старая `initSettingsSheet()` оставлена
без изменений для обратной совместимости, но убрана из UI-путей.

**Почему:**
- Дополнительные валидации (`date`, `force_relabel`, `force_rebuild_report`,
  `report_date`, `provider_chain`) и форматирование (ширины колонок, жирный шрифт)
  предположительно и вызывают падение сервиса на боевом документе.
- Лист остаётся полностью функциональным без этих украшений — пользователь
  может редактировать значения вручную.
- Частичный сбой не должен ломать весь процесс: каждая операция изолирована.

**Что НЕ делаем:**
- Не возвращаем «полный» набор валидаций в `initSettingsSheetSafe`.
- Не удаляем старую `initSettingsSheet()` — она может вызываться извне/вручную.
- Не добавляем в меню тестовые обёртки.

**Затронутые файлы:** `apps_script.gs`, `docs/progress.md`.
