
# Контракты модулей SERPlux

ЖЁСТКОЕ ПРАВИЛО: каждый модуль реализует ровно эти сигнатуры.
Не менять имена функций, типы, ключи словарей. Не лезть в чужой модуль.

## Базовый тип данных: Row

Row — это обычный dict со строго этими ключами:

```python
Row = {
    "date": str,             # "2026-06-15" дата сбора (ISO)
    "searcher": str,         # "google" | "yandex_ru" | "yandex_com"
    "query": str,            # поисковый запрос (субъект)
    "geo": str,              # человекочитаемое гео, напр. "Москва"
    "region_index": int,     # region_index topvisor
    "position": int,         # позиция в выдаче, 1..N
    "url": str,              # найденный URL
    "domain": str,           # домен из URL
    "snippet": str,          # сниппет из выдачи (может быть "")
    "label": str | None,     # алиас последней sentiment (обратная совместимость)
    # --- версионирование (новые поля) ---
    "sentiment": str | None, # "positive" | "negative" | "neutral" | None
    "label_mode": str | None,# "auto" | "deep" (с версии 2026-07-10; старые domains/snippets/full deprecated)
    "label_version": int | None,  # версия разметки (1, 2, 3...)
    "confidence": str,       # "high" | "uncertain", дефолт "high"
    # --- мультитенантность (новые поля) ---
    "client_id": str,        # slug клиента, дефолт "default"
}
```

## storage.py

- `save(rows: list[Row], db_path: str = DB_PATH, client_id: str = "default") -> int`
  — INSERT OR IGNORE в `positions`. Возвращает кол-во вставленных.
  Не обновляет существующие строки.

- `insert_labels(rows: list[Row], db_path: str = DB_PATH) -> int`
  — INSERT в `labels`. Вычисляет `label_version = MAX(version) + 1`
  для каждой пары (position_id, label_mode). Строки с sentiment=None пропускаются.
  Возвращает кол-во вставленных меток.
  **Заменяет** `update_labels()` (которая делала UPDATE одной строки).

- `update_labels(rows: list[Row], db_path: str = DB_PATH) -> int`
  — **DEPRECATED**, оставлен для обратной совместимости. Вызывает `insert_labels()`.
  Будет удалён после миграции всех вызовов.

- `get_cached_label(url: str, query: str, db_path: str = DB_PATH) -> str | None`
  — Ищет последнюю не-NULL `sentiment` по паре (url, query) через JOIN positions+labels.
  Сортировка по labels.created_at DESC. Сигнатура НЕ меняется (обратная совместимость).

- `get_history(filters: dict | None = None, db_path: str = DB_PATH) -> list[Row]`
  — Возвращает строки из БД с JOIN labels (последняя метка на позицию).
  Новые фильтры: `client_id`, `label_version` ("all" = все версии).
  Row включает: sentiment, label_mode, label_version.

- `get_label_history(position_id: int, db_path: str = DB_PATH) -> list[dict]`
  — НОВАЯ функция. Возвращает все версии меток для позиции:
  `[{label_mode, label_version, sentiment, created_at}, ...]`.

- `get_domain_label(domain: str, query: str, geo: str, db_path: str = DB_PATH) -> str | None`
  — Возвращает `sentiment` из `domain_labels` по `(domain, query, geo)`,
  или `None` если записи нет. `query` нормализуется к lowercase.
  Справочник является источником истины для режима `domains`.

- `upsert_domain_label(domain: str, query: str, geo: str, sentiment: str,
                       source: str, db_path: str = DB_PATH) -> None`
  — INSERT или UPDATE записи в `domain_labels` по `PRIMARY KEY (domain, query, geo)`.
  При UPDATE обновляет `sentiment`, `source`, `updated_at`.
  Приоритет `source`: `manual_l1` не перезаписывается источниками `snippet`/`page`;
  `manual_l1` может перезаписать любую существующую запись.

- `bulk_upsert_domain_labels(items: list[dict], db_path: str = DB_PATH) -> None`
  — Массовый upsert списка записей `{domain, query, geo, sentiment, source}`.
  Применяются те же правила приоритета `source`, что и в `upsert_domain_label`.

- **Заполнение `domain_labels`:**
  Ручная эталонная разметка (source=`manual_l1`) обычно заполняется вне приложения.
  Для разовых импортов из Google Sheets предусмотрен изолированный
  `POST /labels/import` (см. ниже) и функция `importEtalonToDb()` в `apps_script.gs`,
  не привязанная к меню. Эндпоинт идемпотентен и устойчив к битым записям.

- **`POST /labels/import`**
  **Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`
  **Тело:** массив объектов `{domain, query, geo, sentiment, source}`.
  `source` по умолчанию `"manual_l1"`.
  **Поведение:**
  - Идемпотентный upsert по PK `(domain, query, geo)`.
  - Одна невалидная запись не прерывает обработку остального батча.
  - Возвращает сводку: `processed`, `imported`, `skipped`, `errors`.

- `_init_db(db_path: str = DB_PATH) -> None`
  — Создаёт таблицы: clients, positions, labels, domain_labels.
  Авто-клиент 'default' если таблица clients пуста.

- `list_clients(db_path: str = DB_PATH) -> list[dict]`
  — Возвращает список клиентов: `client_id`, `client_name`, `project_id`, `sheet_id`,
  `searchers`, `geos`, `regions_map`, `queries`.

- `get_client(client_id: str, db_path: str = DB_PATH) -> dict | None`
  — Возвращает одного клиента с полями `client_id`, `client_name`, `project_id`, `sheet_id`,
  `searchers`, `geos`, `regions_map`, `queries` или `None`, если клиент не найден.
  JSON-поля десериализуются; при пустом/невалидном значении возвращается `[]`.
  `regions_map`: если в БД хранится JSON-массив — возвращается `list[dict]`;
  если legacy-строка (имя файла) — возвращается исходная строка + WARNING.

- `create_client(client_id: str, client_name: str, project_id: int | None = None,
                 sheet_id: str | None = None, searchers: list[str] | None = None,
                 geos: list[str] | None = None, regions_map: list[dict] | str | None = None,
                 queries: list[dict] | None = None, db_path: str = DB_PATH) -> None`
  — Создаёт клиента. `searchers`, `geos`, `queries` сериализуются в JSON.
  `regions_map` может быть JSON-массивом (сериализуется) или legacy-строкой (сохраняется as-is).
  Выбрасывает `ValueError`, если `client_id` уже существует.

- `update_client(client_id: str, db_path: str = DB_PATH, **fields) -> None`
  — Обновляет поля `client_name`, `project_id`, `sheet_id`, `searchers`, `geos`,
  `regions_map`, `queries` и `updated_at`. `searchers`/`geos`/`queries` принимаются как списки
  и сериализуются; `regions_map` — JSON-массив или legacy-строка.
  Выбрасывает `ValueError`, если клиент не найден или переданы недопустимые поля.

- `get_dates(client_id: str | None = None, db_path: str = DB_PATH) -> list[str]`
  — Возвращает уникальные даты из `positions`, отсортированные по убыванию.
  Если `client_id` задан — фильтрует по клиенту.

## config.py — провайдеры LLM

Провайдеры LLM описываются словарём `PROVIDERS`, считываются **только** из `config.py`
(не из БД). Добавление нового провайдера = новая запись в `PROVIDERS`, без правок `labeler.py`.

```python
PROVIDERS: dict[str, dict] = {
    "opencode-zen": {
        "enabled": True,               # участвует в фолбек-цепочке
        "priority": 1,                 # порядок в цепочке (меньше = выше)
        "default_model": "deepseek-v4-flash-free",  # модель для API-вызова
        "models": ["deepseek-v4-flash-free"],        # список доступных моделей
        "endpoint": "https://opencode.ai/zen/v1/chat/completions",
        "api_key_env_var": "OPENCODE_API_KEY",
    },
}
DEFAULT_PROVIDER: str = "opencode-zen"
```

- `enabled`: `False` — провайдер исключается из цепочки labeler без удаления записи.
- `priority`: порядок фолбек-цепочки (1 → 2 → 3…). При ошибке первого пробуется следующий.
- `api_key_env_var`: имя переменной в `.env`, **не значение ключа** (безопасность).
- `endpoint`: OpenAI-совместимый URL.
- `models`: список строк-идентификаторов моделей; `default_model` — одна из них.

## webhook.py — GET /providers

**Метод:** `GET /providers`
**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`
**Ответ:** список провайдеров с полями `id`, `enabled`, `priority`, `default_model`, `models`.
Только чтение — POST/PUT/DELETE не предусмотрены.

```json
[
  {
    "id": "opencode-zen",
    "enabled": true,
    "priority": 1,
    "default_model": "deepseek-v4-flash-free",
    "models": ["deepseek-v4-flash-free"]
  }
]
```

## labeler.py

- `label(
    rows: list[dict],
    db_path: str = storage.DB_PATH,
    label_mode: str = "auto",  # "auto" | "deep"
    force_relabel: bool = False,
    client_id: str = "default",
    provider_chain: str | None = None,
  ) -> list[dict]`
   — Проставляет `sentiment` (и алиас `label`), а также `confidence`, `label_mode` каждой строке.
   Параметры:
   - `label_mode`: режим разметки (**"auto"** | **"deep"**; дефолт "auto")
   - `force_relabel`: если True — игнорировать кэш, размечать всё заново
   - `client_id`: slug клиента (используется для `positions`/`labels`, не для `domain_labels`)
   - `provider_chain`: строка или список идентификаторов провайдеров через запятую;
     фильтрует `config.PROVIDERS` перед фолбек-цепочкой
   Возвращает список с заполненными `sentiment`/`label`/`confidence`/`label_mode`.

   **Режимы (двухрежимная система):**
   
   - **AUTO (дефолт):** иерархический режим с fallback на neutral
     - Шаг 1: ищет `sentiment` в справочнике `domain_labels(domain, query, geo)`
       - Если найдено → `sentiment` из справочника, `confidence='high'`, LLM не вызывается (нулевая стоимость)
     - Шаг 2: если в справочнике нет → вызывает LLM для сниппета (как режим "snippets")
       - Успех → `sentiment` из LLM, `confidence='high'`, сохраняет в `domain_labels` с `source='snippet'`
     - Шаг 3: при ошибке LLM (сеть, таймаут, провайдер недоступен) → `sentiment='neutral'`, `confidence='uncertain'`
       - neutral как маркер неуверенности (пропускаемые и ошибочные случаи помечаются)
     - Кэширование: пары (url, query) с `sentiment != None` берутся из `domain_labels` повторно
   
   - **DEEP (зарезервирован для v2):** обработка только `sentiment=='neutral'` по контенту страницы
     - Сейчас → заглушка, возвращает `sentiment` без изменений (проходит нейтральные без обработки)
     - В v2: HTTP-запрос к каждому URL, LLM по полному тексту страницы, сохранение с `source='page'`
   
   **Логирование:** Разметка группируется по `searcher×geo` (пример: "Связка 1/6: google×Литва").
   Статистика по группе:
   - total / cached / llm_calls / success
   - Причины пропусков: empty_snippet, provider_error, domain_missing, other_skip
   - WARNING/ERROR для пустых сниппетов, ошибок провайдеров, отсутствия доменов в справочнике

- `_label_group_auto(rows: list[dict], ...provider_chain...) -> list[dict]`
   — Вспомогательная функция для режима AUTO.
   - Группирует строки по `(domain, query, geo)`
   - Для каждой группы проверяет `domain_labels` → LLM → neutral
   - Логирует статистику по группе
   - Вызывает `storage.upsert_domain_label()` с приоритетом `source='snippet'`

- `_label_group_deep(rows: list[dict]) -> list[dict]`
   — Вспомогательная функция для режима DEEP (v2).
   - Фильтрует строки с `sentiment=='neutral'`
   - Заглушка: возвращает строки без изменений
   - В v2: добавить HTTP-запрос, LLM по контенту, `source='page'`

**Конкретные изменения от трёхрежимной системы:**
- Старое: `label_mode` ∈ {domains, snippets, full}
- Новое: `label_mode` ∈ {auto, deep}
- **auto** заменил `domains` + `snippets` (auto выполняет оба с fallback)
- **deep** заменил `full` (зарезервирован для страницы)
- **neutral** стал явным маркером неуверенности (вместо None в ошибках)

## webhook.py — API-эндпоинты

### POST /run

Запускает пайплайн сбора → разметки → выгрузки или только построение отчёта.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Сборка config:**
`webhook.py` по `client_id` загружает профиль клиента из БД (`storage.get_client`).
Runtime-config собирается как `DEFAULT_CONFIG` → параметры запроса → профиль клиента
(`project_id`, `sheet_id`, `searchers`, `geos`, `regions_map`) → runtime-параметры
запроса (`with_labels`, `depth`, `label_mode`, `force_relabel`).

**Тело запроса:**
```python
{
    "regions_map": str | list = "regions_map.json",  # legacy-имя файла или JSON-массив из профиля
    "with_labels": bool = True,                       # включить разметку
    "depth": int = 10,                                # глубина сбора (10/20/50/100)
    "client_id": str = "default",                     # ID клиента
    "label_mode": str = "auto",                       # режим разметки: "auto" (дефолт) | "deep" (v2)
    "force_relabel": bool = False,                    # принудительная переразметка
    "report_only": bool = False,                      # если True — только построить отчёт
    "report_date": str = "latest",                    # дата для отчёта (YYYY-MM-DD или "latest")
    "date": str = "today",                            # дата сбора/разметки (YYYY-MM-DD или "today")
    "label_only": bool = False,                       # если True — только разметить существующие данные
    "force_rebuild_report": bool = False,             # перестроить отчёт с нуля
    "provider_chain": str | None = None,              # фильтр провайдеров LLM (через запятую)
}
```

**Примечание:** с версии 2026-07-10 поддерживаются только режимы `"auto"` и `"deep"`.  
Старые значения `"domains"`, `"snippets"`, `"full"` больше не принимаются (валидация 422).

**Ответ 202 Accepted:**
```json
{
    "accepted": true,
    "started_at": "2026-07-04T10:00:00.123456+00:00",
    "client_id": "sudheimer"
}
```

**Ответ 409 Conflict:**
```json
{
    "detail": "Прогон уже выполняется, подождите завершения"
}
```

**Логика `report_only`:**
- При `report_only=true`: пропускает сбор (topvisor/collector), разметку (labeler),
  выгрузку (exporter) и вызывает только `reporter.build_report(date, force=True)`.
- При `report_only=false` (дефолт): полный пайплайн collect → save → label → export → report.

### GET /status

Возвращает статус последнего прогона.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Ответ:**
```json
{
    "status": "ok",
    "started_at": "2026-07-04T10:00:00.123456+00:00",
    "finished_at": "2026-07-04T10:05:32.654321+00:00",
    "client_id": "sudheimer",
    "message": "Прогон завершён успешно"
}
```

**Поля:**
- `status`: `"idle"` | `"starting"` | `"running"` | `"ok"` | `"error"`
- `started_at`: ISO-формат времени старта прогона (null если не было прогонов)
- `finished_at`: ISO-формат времени завершения (null пока прогон идёт)
- `client_id`: ID клиента из последнего/текущего прогона (null если не было прогонов)
- `message`: текстовое сообщение о результате или ошибке

### GET /health

Health-check для мониторинга контейнера (без авторизации).

**Ответ:**
```json
{
    "status": "ok",
    "service": "serplux-webhook"
}
```

### GET /clients

Возвращает список зарегистрированных клиентов.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Ответ:**
```json
[
    {
        "client_id": "default",
        "client_name": "Default Client",
        "project_id": null,
        "sheet_id": null
    },
    {
        "client_id": "sudheimer",
        "client_name": "Sudheimer Group",
        "project_id": 12345,
        "sheet_id": "1BxiMVs0XRA5nFMdKvZdBZqggm8A8k4"
    }
]
```

### POST /clients

Создаёт нового клиента.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Тело запроса:**
```python
{
    "client_id": str,                          # обязательный, уникальный
    "client_name": str,                        # обязательный
    "project_id": int | None,                  # опциональный
    "sheet_id": str | None,                    # опциональный
    "searchers": list[str] | None,             # опциональный, напр. ["google", "yandex_ru"]
    "geos": list[str] | None,                  # опциональный, напр. ["Литва", "Германия"]
    "regions_map": list[dict] | str | None,    # опциональный, JSON-массив регионов или имя файла (legacy)
    "queries": list[dict] | None,              # опциональный, субъекты [{key, display}]
}
```

**Ответ 201 Created:** возвращает созданный профиль клиента.

**Ответ 409 Conflict:** если `client_id` уже занят.

### GET /clients/{client_id}

Возвращает профиль конкретного клиента или 404.

### PUT /clients/{client_id}

Обновляет профиль клиента. Возвращает 404, если клиент не найден.

**Тело запроса:**
```python
{
    "client_name": str | None,
    "project_id": int | None,
    "sheet_id": str | None,
    "searchers": list[str] | None,
    "geos": list[str] | None,
    "regions_map": str | None,
}
```

### GET /clients/{client_id}/dates

Возвращает список дат, за которые есть данные для клиента.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Ответ 200 OK:**
```json
{
    "dates": ["2026-07-03", "2026-07-01"]
}
```

**Ответ 404 Not Found:** если клиент не найден.

### GET /topvisor/regions

Возвращает доступные регионы проекта Topvisor.

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Query-параметры:**
```python
{
    "project_id": int  # обязательный
}
```

**Ответ 200 OK:**
```json
{
    "project_id": 12345,
    "regions": [
        {"index": 1300, "name": "Литва"},
        {"index": 1301, "name": "Вильнюс"}
    ]
}
```

**Ответ 404 Not Found:** если регионы для проекта не найдены.

**Ответ 502 Bad Gateway:** при ошибке связи с Topvisor.

### GET /providers

Возвращает список зарегистрированных провайдеров LLM (только чтение).

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Ответ:**
```json
[
    {
        "id": "opencode-zen",
        "enabled": true,
        "priority": 1,
        "default_model": "deepseek-v4-flash-free",
        "models": ["deepseek-v4-flash-free"]
    }
]
```

**Примечание:** POST/PUT/DELETE /providers не реализованы (ADR 2026-07-03: провайдеры в config.py, read-only).

---

## Важно

- `label` в Row — алиас для `sentiment` (обратная совместимость с exporter, reporter, main.py)
- `client_id` по умолчанию = "default" (для миграции с одноклиентной модели)
- `update_labels()` → `insert_labels()`: INSERT новой версии, не UPDATE существующей
- Таблица `labels` получила поле `confidence` (`'high' | 'uncertain'`), пока всегда `'high'`
- Режим `domains` работает через справочник `domain_labels` (без LLM); режим `snippets` — через кэш + LLM

## Миграция схемы (domain_labels + confidence)

Для существующих БД, уже перенесённых на схему `clients/positions/labels`,
необходимо выполнить:

```sql
ALTER TABLE labels
ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high';

-- Актуальная схема domain_labels (мультиклиентная через ключ domain/query/geo)
CREATE TABLE domain_labels (
    domain      TEXT NOT NULL,
    query       TEXT NOT NULL,           -- нормализованный key субъекта, lowercase
    geo         TEXT NOT NULL,           -- geo_name как в regions_map
    sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
    source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain, query, geo)
);

CREATE INDEX idx_domlbl_domain_query ON domain_labels(domain, query);
CREATE INDEX idx_domlbl_geo ON domain_labels(geo);
```

- `migrate.py` выполняет эти DDL-шаги идемпотентно
- Если существует старая схема `domain_labels` (с `id`/`client_id`) — таблица пересоздаётся
- На боевой БД запускать **только после бэкапа** и проверки на копии
