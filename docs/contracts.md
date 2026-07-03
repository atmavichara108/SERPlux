
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
    "label_mode": str | None,# "domains" | "snippets" | "full"
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

- `get_domain_label(client_id: str, domain: str, db_path: str = DB_PATH) -> dict | None`
  — Возвращает `{sentiment, source, confidence}` из `domain_labels` по
  `(client_id, domain)`, или `None` если домена нет в справочнике.
  Поле `confidence` возвращается как `'high'` (константа), т.к. справочник
  является источником истины для достоверно размеченных доменов.

- `upsert_domain_label(client_id: str, domain: str, sentiment: str,
                       source: str = "manual", db_path: str = DB_PATH) -> None`
  — INSERT или UPDATE записи в `domain_labels` по `UNIQUE(client_id, domain)`.
  При UPDATE обновляет `sentiment`, `source`, `updated_at`.

- `_init_db(db_path: str = DB_PATH) -> None`
  — Создаёт таблицы: clients, positions, labels, domain_labels.
  Авто-клиент 'default' если таблица clients пуста.

- `list_clients(db_path: str = DB_PATH) -> list[dict]`
  — Возвращает список клиентов: `client_id`, `client_name`, `project_id`, `sheet_id`.

- `get_client(client_id: str, db_path: str = DB_PATH) -> dict | None`
  — Возвращает одного клиента с полями `client_id`, `client_name`, `project_id`, `sheet_id`
  или `None`, если клиент не найден.

- `create_client(client_id: str, client_name: str, project_id: int | None = None,
                 sheet_id: str | None = None, db_path: str = DB_PATH) -> None`
  — Создаёт клиента. Выбрасывает `ValueError`, если `client_id` уже существует.

- `update_client(client_id: str, db_path: str = DB_PATH, **fields) -> None`
  — Обновляет поля `client_name`, `project_id`, `sheet_id` и `updated_at`.
  Выбрасывает `ValueError`, если клиент не найден или переданы недопустимые поля.

## labeler.py

- `label(rows: list[Row], db_path: str = DB_PATH, label_mode: str = "snippets",
         force_relabel: bool = False, client_id: str = "default") -> list[Row]`
  — Проставляет `sentiment` (и алиас `label`), а также `confidence` каждой строке.
  Параметры:
  - `label_mode`: режим разметки ("domains" | "snippets" | "full")
  - `force_relabel`: если True — игнорировать кэш, размечать всё заново
  - `client_id`: slug клиента, передаётся в `storage.get_domain_label()`
  Возвращает тот же список с заполненными `sentiment`/`label`/`confidence`.

  Режимы:
  - **domains** (реализован): для каждой строки берёт `domain`, ищет в справочнике
    через `storage.get_domain_label(client_id, domain)`. Если найдено — ставит
    `sentiment` из справочника, `confidence='high'`, LLM НЕ вызывается (нулевая
    стоимость). Если домена нет в справочнике — `sentiment=None`
    (помечается для ручной разметки, TBD).
  - **snippets** (реализован): текущая логика — кэш (`storage.get_cached_label`)
    → LLM по сниппету. `confidence='high'`.
  - **full**: заглушка, v2. Заход на страницу + LLM по полному тексту.

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

CREATE TABLE domain_labels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    domain      TEXT NOT NULL,
    sentiment   TEXT CHECK(sentiment IN ('positive','negative','neutral')),
    source      TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','llm')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(client_id, domain)
);

CREATE INDEX idx_domlbl_client_domain ON domain_labels(client_id, domain);
```

- `migrate.py` следует дополнить этими DDL-шагами
- На боевой БД запускать **только после бэкапа** и проверки на копии
