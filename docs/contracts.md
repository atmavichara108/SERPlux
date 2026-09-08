
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
                               # Подробнее: docs/labeling_canon.md
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

- `get_domain_label(domain_or_url: str, query: str, db_path: str = DB_PATH) -> str | None`
  — Возвращает `sentiment` из `domain_labels` по `(domain, query)`,
  или `None` если записи нет. Домен нормализуется через `storage.normalize_domain`;
  `query` нормализуется к lowercase. `geo` в эталон не входит.

- `get_domain_label_record(domain_or_url: str, query: str, db_path: str = DB_PATH) -> dict | None`
  — Возвращает полную запись эталона `{domain, query, sentiment, source, updated_at}`
  по ключу `(domain, query)`, или `None`, если записи нет или ключ пуст.
  Нужна валидатору (v1.1), чтобы отличать `manual_l1` от legacy `snippet`/`page`
  (`get_domain_label()` возвращает только sentiment, без `source`).

- `upsert_domain_label(domain_or_url: str, query: str, sentiment: str,
                        source: str, db_path: str = DB_PATH) -> None`
  — INSERT или UPDATE записи в `domain_labels` по `PRIMARY KEY (domain, query)`.
  Домен нормализуется перед записью. Колонка в БД называется `domain`.
  При UPDATE обновляет `sentiment`, `source`, `updated_at`.
  Приоритет `source`: `manual_l1` не перезаписывается источниками `snippet`/`page`;
  `manual_l1` может перезаписать любую существующую запись.
  **Конфликт эталона (v1.1):** `manual_l1` → `manual_l1` с **другим** sentiment —
  блокирующая ошибка записи `ValueError "manual_l1 conflict"`
  (last-write-wins запрещён); тот же sentiment — идемпотентно
  (обновляется только `updated_at`).
  Полный канон разметки: `docs/labeling_canon.md`.

- `bulk_upsert_domain_labels(items: list[dict], db_path: str = DB_PATH) -> None`
  — Массовый upsert списка записей `{domain, query, sentiment, source}`.
  `domain` нормализуется. Применяются те же правила приоритета `source`
  и конфликта `manual_l1` (`ValueError "manual_l1 conflict"` при другом
  sentiment), что и в `upsert_domain_label`.

- **Журнал конфликтов разметки `label_conflicts` (v1.1, ADR 2026-09-05):**
  append-oriented, каждая запись привязана к `run_id` прогона.

  - `CONFLICT_TYPES` — кортеж допустимых значений `conflict_type`:
    `manual_neutral`, `unmatched_neutral`, `manual_conflict`, `invalid_or_unknown`.
  - `save_label_conflicts(records: list[dict], db_path: str = DB_PATH) -> int`
    — INSERT записей журнала. Валидирует `conflict_type` (входит в
    `CONFLICT_TYPES`); пустые `domain`/`query` ДОПУСТИМЫ — категория
    `invalid_or_unknown` как раз фиксирует строки с невалидным ключом.
    Валидация всей пачки до вставки (невалидная запись отменяет батч).
    Возвращает кол-во записанных.
  - `get_label_conflicts(run_id: str | None = None, limit: int = 500,
      db_path: str = DB_PATH) -> list[dict]`
    — Возвращает записи журнала, новые сверху; опциональный фильтр по `run_id`.
  - `prune_label_conflicts(keep_last_runs: int = 50, db_path: str = DB_PATH) -> int`
    — Ретеншн: удаляет записи журнала, кроме последних `keep_last_runs`
    прогонов (по `run_id`). Возвращает кол-во удалённых.

  DDL-состав таблицы (создаётся идемпотентно в `_init_db` и `migrate.py`):

  ```sql
  CREATE TABLE IF NOT EXISTS label_conflicts (
      id                 INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id             TEXT,
      domain             TEXT NOT NULL,
      url                TEXT,
      query              TEXT NOT NULL,
      geo                TEXT,
      searcher           TEXT,
      position           INTEGER,
      observed_label     TEXT,
      manual_label       TEXT,
      source             TEXT,
      confidence         TEXT,
      conflict_type      TEXT NOT NULL CHECK(conflict_type IN
                         ('manual_neutral','unmatched_neutral',
                          'manual_conflict','invalid_or_unknown')),
      recommended_action TEXT,
      created_at         TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE INDEX IF NOT EXISTS idx_lblconf_run ON label_conflicts(run_id);
  CREATE INDEX IF NOT EXISTS idx_lblconf_key ON label_conflicts(domain, query);
  ```

  Колонка `run_id TEXT` в `run_status` (uuid4.hex прогона, ADR 2026-09-05) —
  добавляется миграцией идемпотентно.

- **Заполнение `domain_labels`:**
  Ручная эталонная разметка (source=`manual_l1`) обычно заполняется вне приложения.
  Для импортов из Google Sheets предусмотрен `POST /labels/import` (см. ниже),
  изолированный `importHistoricalEtalonsToDb()` и подтверждаемая команда
  `SERPlux → Зафиксировать исправления в эталон`. Эндпоинт идемпотентен и
  устойчив к битым записям.
  Ключ эталона — нормализованный домен и query, без geo и client_id.

- **`POST /labels/import`**
  **Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>` (без токена — 401).
  **Тело:** допускаются два формата:
    - голый массив `[{domain, query, sentiment, source}, ...]`;
    - объект `{"labels": [...]}`.
  Принимается `domain` или URL в поле `domain`; сервер нормализует его до домена.
  `source` по умолчанию `"manual_l1"` (если не передан или пуст).
  **Поведение:**
  - Каждая запись импортируется через `storage.upsert_domain_label`, поэтому
    работают правила приоритета `source` (`manual_l1` не перезаписывается
    `snippet`/`page`) и идемпотентность по PK `(domain, query)`.
  - Битая запись не прерывает батч: увеличивается `skipped` (валидация) или
    `errors` (ошибка БД), собираются первые ~5 сообщений в `error_samples`.
  - Конфликт `manual_l1` → `manual_l1` с другим sentiment (ADR 2026-09-05):
    запись уходит в `errors`, сообщение `"manual_l1 conflict"` попадает в
    `error_samples`, батч продолжается. Тот же sentiment — идемпотентно.
  - Ответ HTTP 200 даже при частичных ошибках:
    `{"imported": N, "skipped": N, "errors": N, "error_samples": [...]}`.

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
        "default_model": "qwen3.6-plus",  # модель для API-вызова
        "models": ["qwen3.6-plus"],        # список доступных моделей
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
**Ответ:** список провайдеров с полями `id`, `enabled`, `priority`, `default_model`, `models`, `endpoint`, `api_key_env_var`.

```json
[
  {
    "id": "opencode-zen",
    "enabled": true,
    "priority": 1,
    "default_model": "mimo-v2.5-free",
    "models": ["mimo-v2.5-free", "nemotron-3-ultra-free"],
    "endpoint": "https://opencode.ai/zen/v1/chat/completions",
    "api_key_env_var": "OPENCODE_API_KEY"
  }
]
```

## webhook.py — POST /providers/register

**Метод:** `POST /providers/register`
**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Тело:**
```python
{
    "provider_id": str,              # slug, например "openrouter"
    "enabled": bool = True,
    "priority": int = 999,
    "default_model": str,            # одна из models
    "models": list[str],             # рабочие модели (после discover)
    "endpoint": str | None = None,   # если не передан — берётся из config.KNOWN_ENDPOINTS
    "api_key_env_var": str,          # имя env-переменной, НЕ сам ключ
}
```

**Поведение:**
- Сам API-ключ не принимается и не хранится — сервер читает его из env по `api_key_env_var`.
- `endpoint` валидируется (`_validate_endpoint`: только https, без localhost/private/link-local).
- Провайдер хранится в памяти (`config.PROVIDERS`) — после перезапуска контейнера нужно добавить в `config.py` или `.env`.
- 409 при существующем provider_id, 422 при неизвестном provider_id без endpoint или невалидном endpoint.

## webhook.py — POST /providers/discover

**Метод:** `POST /providers/discover`
**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Тело:**
```python
{
    "provider_id": str,
    "endpoint": str,          # OpenAI-совместимый, валидируется (https, не private)
    "api_key_env_var": str,   # имя env-переменной, НЕ сам ключ
}
```

**Поведение:**
- Сервер читает ключ из env по имени переменной (пусто → 400).
- `GET {base}/models` (суффикс `/chat/completions` автоматически убирается).
- Фильтрует модели: только id с literal `-free`, лимит `MAX_DISCOVER_MODELS = 20`.
- Каждую free-модель тестирует `POST {endpoint}` с `max_tokens=1` (timeout 10s).
- Ошибки сети/парсинга → 502.

**Ответ:**
```json
{
  "provider_id": "openrouter",
  "endpoint": "https://openrouter.ai/api/v1/chat/completions",
  "models": [{"id": "model-free", "status": "ok"}],
  "working": ["model-free"]
}
```

## webhook.py — PUT/DELETE /providers/{provider_id}

- `PUT /providers/{provider_id}` — обновляет `enabled`, `priority`, `default_model`, `models`, `endpoint`, `api_key_env_var` (только переданные поля). 404 если провайдер не найден.
- `DELETE /providers/{provider_id}` — удаляет провайдера. Нельзя удалить последнего включённого (400).

## labeler.py

- `label(
    rows: list[dict],
    db_path: str = storage.DB_PATH,
    label_mode: str = "auto",  # "auto" | "deep"
    force_relabel: bool = False,
    client_id: str = "default",
    provider_chain: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    validation_out: dict | None = None,
    stats_out: dict | None = None,  # v1.0.3
  ) -> list[dict]`
   — Проставляет `sentiment` (и алиас `label`), а также `confidence`,
   `label_mode`, `label_source` каждой строке.
   Параметры:
   - `label_mode`: режим разметки (**"auto"** | **"deep"**; дефолт "auto")
   - `force_relabel`: сбросить автоматический кэш и размечать заново;
     **эталон `manual_l1` НЕ обходится** (жёсткий референс, ADR 2026-09-05)
   - `client_id`: slug клиента (используется для `positions`/`labels`, не для `domain_labels`)
   - `provider_chain`: строка или список идентификаторов провайдеров через запятую;
     фильтрует `config.PROVIDERS` перед фолбек-цепочкой
   - `model`: override модели текущего провайдера (ADR 2026-07-15)
   - `run_id`: идентификатор прогона (uuid4.hex из webhook); пишется в записи
     журнала `label_conflicts`
   - `validation_out`: мутируемый dict-аккумулятор; после разметки заполняется
     счётчиками `{total, ok, manual_neutral, unmatched_neutral, manual_conflict,
     invalid_or_unknown, unlabeled, recorded}` — блок "validation" в stats прогона
   - `stats_out` (v1.0.3): опциональный мутируемый dict; после разметки
     заполняется breakdown по `label_source`:
     `{etalon_hit, llm_success, fallback_empty_snippet, fallback_provider_error,
     fallback_invalid_llm, fallback_invalid_key, invalid_key, total}`.
     Блок "labeling" в stats прогона (показывается в Sheets-статусе).
     При `stats_out=None` поведение прежнее (breakdown только в логах).
   Возвращает список с заполненными `sentiment`/`label`/`confidence`/`label_mode`/`label_source`.

   **Pre-LLM lookup (эталон — жёсткий референс):** перед LLM ищется `manual_l1`
   по ключу `(normalize_domain(url), normalize_query(query))`. Найдено →
   sentiment из эталона, `confidence='high'`, LLM не вызывается (нулевая
   стоимость). `force_relabel` сбрасывает только автоматический кэш, которого
   в auto-режиме больше нет.

   **Post-label валидация (`_validate_labels`):** после разметки каждая строка
   сравнивается produced sentiment с эталоном; категории конфликтов:
   - `manual_neutral` — эталон говорит neutral;
   - `unmatched_neutral` — эталона нет, fallback/LLM дал neutral
     (провайдер-сбой и пустой сниппет → neutral + uncertain);
   - `manual_conflict` — результат ≠ `manual_l1`: строка **исправляется на
     эталон**, конфликт пишется в журнал `label_conflicts` (не молча);
   - `invalid_or_unknown` — пустой ключ/URL или мусорный ответ LLM.

- `_parse_label(raw: str | None) -> str | None`
   — Нормализует ответ LLM к `{"positive","negative","neutral"}`; возвращает
   `None` для мусорного/нечитаемого ответа (раньше мусор превращался в ложный
   `neutral`).

- `_label_one_llm(row: dict, provider_chain=None, model=None, invalid_ref=None)`
   — Одиночный вызов LLM по сниппету с retry/fallback по цепочке провайдеров.
   `invalid_ref` — мутируемый счётчик нечитаемых ответов: инкрементируется,
   когда `_parse_label` вернул `None`. Строка получает `neutral` +
   `confidence='uncertain'` + `label_source='fallback_invalid_llm'`.

- **Транзиентное поле строки `label_source`** ∈
  {`manual_l1`, `llm`, `fallback_empty_snippet`, `fallback_provider_error`,
  `fallback_invalid_llm`, `fallback_invalid_key`}
  — источник метки в текущем прогоне (в `positions`/`labels` не сохраняется);
  используется валидатором, журналом конфликтов и статистикой прогона.

   **Режимы (двухрежимная система):**
   
  - **AUTO (дефолт):** иерархический режим с fallback на neutral
       - Шаг 1: ищет `sentiment` в справочнике `domain_labels(domain, query)`
          по **домену** (нормализованному). Жёсткий референс — ТОЛЬКО записи
         с `source='manual_l1'`; если найдено → `sentiment` из справочника,
         `confidence='high'`, LLM не вызывается (нулевая стоимость).
         `force_relabel` эталон НЕ обходит (v1.1 precedence rule 5)
      - Шаг 2: если жёсткого эталона нет → вызывает LLM для сниппета
        - Успех → `sentiment` из LLM, `confidence='high'`, `label_source='llm'`.
          Результат LLM в `domain_labels` НЕ записывается (авторазметка —
          не эталон, ADR 2026-08-16)
      - Шаг 3: при ошибке LLM (сеть, таймаут, провайдер недоступен) → `sentiment='neutral'`, `confidence='uncertain'`
        - neutral как маркер неуверенности (пропускаемые и ошибочные случаи помечаются)
       - Повторные прогоны: пары (domain, query) с `manual_l1` берутся из
         `domain_labels` повторно; LLM-результаты не кэшируются
   
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
    - Группирует строки по `(searcher, geo)`
     - Для каждой строки проверяет `domain_labels(domain, query)` → LLM → neutral
    - Логирует статистику по группе и детальный лог для каждой строки
    - Результат LLM в `domain_labels` НЕ записывается (эталон — только manual_l1)

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
    "searchers": list[str] | None = None,             # поисковики текущего прогона: ["google","yandex_ru","yandex_com"]; пустой список/неизвестные значения → 422
}
```

**Примечание:** с версии 2026-07-10 поддерживаются только режимы `"auto"` и `"deep"`.  
Старые значения `"domains"`, `"snippets"`, `"full"` больше не принимаются (валидация 422).

**Ответ 202 Accepted:**
```json
{
    "accepted": true,
    "started_at": "2026-07-04T10:00:00.123456+00:00",
    "client_id": "sudheimer",
    "run_id": "3f2c9a6e8b7d4f1a9c0d2e5b8a1f4c7e"
}
```

**`run_id`:** `uuid4.hex`, генерируется при старте прогона; хранится в
`run_status.run_id`, проходит `main.run` → labeler → журнал `label_conflicts`,
возвращается в `GET /status` (ADR 2026-09-05).

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
    "run_id": "3f2c9a6e8b7d4f1a9c0d2e5b8a1f4c7e",
    "message": "Прогон завершён успешно"
}
```

**Поля:**
- `status`: `"idle"` | `"starting"` | `"running"` | `"ok"` | `"error"`
- `started_at`: ISO-формат времени старта прогона (null если не было прогонов)
- `finished_at`: ISO-формат времени завершения (null пока прогон идёт)
- `client_id`: ID клиента из последнего/текущего прогона (null если не было прогонов)
- `run_id`: идентификатор последнего/текущего прогона, uuid4.hex (null если не было прогонов)
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

### GET /labels/conflicts

Возвращает журнал конфликтов разметки (v1.1, ADR 2026-09-05).

**Авторизация:** `Authorization: Bearer <WEBHOOK_SECRET>`

**Query-параметры:**
```python
{
    "run_id": str | None,   # фильтр по прогону (опционально)
    "limit": int = 500,     # максимум записей
}
```

**Ответ 200 OK:**
```json
{
    "conflicts": [
        {
            "run_id": "3f2c9a6e8b7d4f1a9c0d2e5b8a1f4c7e",
            "domain": "example.de",
            "url": "https://example.de/page",
            "query": "Example Group",
            "geo": "Германия",
            "searcher": "google",
            "position": 3,
            "observed_label": "neutral",
            "manual_label": "positive",
            "source": "manual_l1",
            "confidence": "high",
            "conflict_type": "manual_conflict",
            "recommended_action": "Проверить эталон и результат разметки",
            "created_at": "2026-09-05 12:00:00"
        }
    ],
    "count": 1
}
```

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
        "default_model": "qwen3.6-plus",
        "models": ["qwen3.6-plus"]
    }
]
```

**Примечание:** POST/PUT/DELETE /providers не реализованы (ADR 2026-07-03: провайдеры в config.py, read-only).

---

## collector.py

- `collect(config: dict) -> list[Row]`
   — Собирает снимки выдачи по всем связкам searcher×geo из config.
   Частичный сбой: ошибка одной связки логируется, сбор продолжается.

   **Raises (v1.0.3):** `CollectTimeoutError` — ТОЛЬКО когда poll_status Topvisor
   вернул False (таймаут `timeout_sec`) И финальная попытка скачивания снапшотов
   не дала ни одной строки. Таймаут поллинга сам по себе сбор не прерывает:
   после него выполняется одна финальная попытка `get_snapshot` по всем связкам
   (снапшот часто дозревает вскоре после таймаута). Частичный сбор — обычный успех.
   Поймано в `main.run()` → `exit_code=1` + сообщение оператору про повторный запуск.

## Важно

- `label` в Row — алиас для `sentiment` (обратная совместимость с exporter, reporter, main.py)
- `client_id` по умолчанию = "default" (для миграции с одноклиентной модели)
- `update_labels()` → `insert_labels()`: INSERT новой версии, не UPDATE существующей
- Таблица `labels` получила поле `confidence` (`'high' | 'uncertain'`), пока всегда `'high'`
- Режим `auto` (дефолт) использует справочник `domain_labels` (кэш по полному URL) → затем LLM по сниппету; режим `deep` зарезервирован для разметки по контенту страницы

## Миграция схемы (domain_labels + confidence)

Для существующих БД, уже перенесённых на схему `clients/positions/labels`,
необходимо выполнить:

```sql
ALTER TABLE labels
ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high';

-- Актуальная схема domain_labels (ключ по домену + query, без geo/client_id)
CREATE TABLE domain_labels (
    domain      TEXT NOT NULL,
    query       TEXT NOT NULL,           -- нормализованный key субъекта, lowercase
    sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
    source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain, query)
);

CREATE INDEX idx_domlbl_domain_query ON domain_labels(domain, query);
```

- `migrate.py` выполняет эти DDL-шаги идемпотентно
- Если существует старая схема `domain_labels` (с `id`/`client_id`/`domain`) — таблица пересоздаётся
- На боевой БД запускать **только после бэкапа** и проверки на копии
- v1.1 (ADR 2026-09-05): миграция также идемпотентно создаёт таблицу
  `label_conflicts` (DDL в разделе storage.py) и колонку `run_status.run_id`

## reporter.py — раскладка отчёта

- `build_report(date: str | None = None, force: bool = False, sheet_id: str | None = None,
                client_id: str = "default", db_path: str = storage.DB_PATH) -> None`
  — Строит матрицу-отчёт на листе «Отчёт» в Google Sheets.
  - Загружает профиль клиента из БД (`client.queries`, `client.regions_map`).
  - Динамическая раскладка колонок из `_build_subject_layout(queries)`.
  - Лист полностью очищается перед записью.

- `_build_subject_layout(queries: list[dict]) -> dict`
  — Строит раскладку колонок из списка субъектов.
  - **Правило буферов (канон Лист1):**
    - Субъект 1: pos=1 (B), url=2 (C)
    - Буфер после первого субъекта: 3 колонки (D, E, F)
    - Субъект 2: pos=6 (G), url=7 (H)
    - Буфер перед каждым последующим (≥3): 1 колонка
    - Субъект N (N≥2): pos = 6 + (N-2)*3, url = pos + 1
  - Возвращает `{"num_subjects": N, "cols": total_cols, "subjects": [{"key", "display", "pos", "url"}, ...]}`.

- **Вертикальная структура (для каждого провайдера):**
  - Строка 1: `Позиции {провайдер} на {ДД.ММ.ГГГГ}`
  - Строка 2: пустой буфер
  - Строка 3: имя субъекта (в url-колонке, правая)
  - Строка 4: название гео (в pos-колонке, левая)
  - Строки 5–14: 10 позиций (номер в pos-колонке с заливкой sentiment, URL в url-колонке)
  - Строка 15: пустой буфер
  - Повтор для следующего гео.

- **Заливка sentiment:**
  - Применяется к ячейке номера позиции (pos-колонка), не к URL.
  - positive → зелёный (0.85, 0.92, 0.83)
  - negative → красный (0.96, 0.80, 0.80)
  - neutral → жёлтый (1.0, 0.95, 0.80)

- **Канон раскладки:** `docs/report_layout.md`
