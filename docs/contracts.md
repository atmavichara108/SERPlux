
# Контракты модулей SERPlux

ЖЁСТКОЕ ПРАВИЛО: каждый модуль реализует ровно эти сигнатуры.
Не менять имена функций, типы, ключи словарей. Не лезть в чужой модуль.

## Базовый тип данных: Row

Row — это обычный dict со строго этими ключами:

```python
Row = {
    "date": str,        # "2026-06-15" дата сбора (ISO)
    "searcher": str,    # "google" | "yandex_ru" | "yandex_com"
    "query": str,       # поисковый запрос
    "geo": str,         # человекочитаемое гео, напр. "Москва" или "Tbilisi"
    "region_index": int,# region_index topvisor для этого гео
    "position": int,    # позиция в выдаче, 1..N
    "url": str,         # найденный URL
    "domain": str,      # домен из URL, напр. "example.com"
    "snippet": str,     # сниппет из выдачи (может быть "")
    "label": str | None # "positive" | "negative" | "neutral" | None
}

## storage.py

- `save(rows: list[Row]) -> int` — сохраняет строки в SQLite, возвращает кол-во вставленных.
- `get_cached_label(url: str, query: str) -> str | None` — ищет последнюю не-NULL метку
  по паре (url, query), сортировка по date DESC. Возвращает метку или None.
- `get_history(filters: dict | None = None) -> list[Row]` — возвращает строки из БД
  с опциональными фильтрами (date, searcher, geo, query).

## labeler.py

- `label(rows: list[Row]) -> list[Row]` — проставляет поле label каждой строке.
  Сначала проверяет кэш (storage.get_cached_label), затем вызывает LLM (Gemini).
  При ошибке Gemini — фолбек на дешёвую модель. При ошибке обоих — label=None.
  Возвращает тот же список с заполненными label.
