
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

- `_init_db(db_path: str = DB_PATH) -> None`
  — Создаёт таблицы: clients, positions, labels.
  Авто-клиент 'default' если таблица clients пуста.

## labeler.py

- `label(rows: list[Row], db_path: str = DB_PATH, label_mode: str = "snippets", force_relabel: bool = False) -> list[Row]`
  — Проставляет `sentiment` (и алиас `label`) каждой строке.
  Новые параметры:
  - `label_mode`: режим разметки ("domains" | "snippets" | "full")
  - `force_relabel`: если True — игнорировать кэш, размечать всё заново
  Сначала проверяет кэш (storage.get_cached_label), затем вызывает LLM.
  Возвращает тот же список с заполненными sentiment/label.
  **Режимы `domains` и `full` — заглушки, реализуются отдельно.**

## Важно

- `label` в Row — алиас для `sentiment` (обратная совместимость с exporter, reporter, main.py)
- `client_id` по умолчанию = "default" (для миграции с одноклиентной модели)
- `update_labels()` → `insert_labels()`: INSERT новой версии, не UPDATE существующей
- Таблица `labels` получила поле `confidence` (`'high' | 'uncertain'`), пока всегда `'high'`
- Новая таблица `domain_labels` — справочник доменов для режима `domains`

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
