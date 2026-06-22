import sqlite3
import logging
from typing import Any

log = logging.getLogger(__name__)

DB_PATH = "serplux.db"

Row = dict[str, Any]


def _get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(db_path: str = DB_PATH) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                searcher TEXT NOT NULL,
                query TEXT NOT NULL,
                geo TEXT NOT NULL,
                region_index INTEGER NOT NULL,
                position INTEGER NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                snippet TEXT NOT NULL DEFAULT '',
                label TEXT,
                UNIQUE(date, searcher, query, geo, position, url)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON results(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date_searcher_geo ON results(date, searcher, geo)")
        conn.commit()
        log.info("БД инициализирована: %s", db_path)
    finally:
        conn.close()


def save(rows: list[Row], db_path: str = DB_PATH) -> int:
    conn = _get_conn(db_path)
    inserted = 0
    try:
        for row in rows:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO results
                   (date, searcher, query, geo, region_index, position, url, domain, snippet, label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["date"],
                    row["searcher"],
                    row["query"],
                    row["geo"],
                    row["region_index"],
                    row["position"],
                    row["url"],
                    row["domain"],
                    row.get("snippet", ""),
                    row.get("label"),
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        log.info("Сохранено %s строк из %s", inserted, len(rows))
    finally:
        conn.close()
    return inserted


def update_labels(rows: list[Row], db_path: str = DB_PATH) -> int:
    conn = _get_conn(db_path)
    updated = 0
    try:
        for row in rows:
            label = row.get("label")
            if label is None:
                continue
            cursor = conn.execute(
                """UPDATE results SET label = ?
                   WHERE date = ? AND searcher = ? AND query = ?
                     AND geo = ? AND position = ? AND url = ?""",
                (
                    label,
                    row["date"],
                    row["searcher"],
                    row["query"],
                    row["geo"],
                    row["position"],
                    row["url"],
                ),
            )
            if cursor.rowcount > 0:
                updated += 1
        conn.commit()
        log.info("Обновлено %s меток из %s", updated, len(rows))
    finally:
        conn.close()
    return updated


def get_cached_label(url: str, query: str, db_path: str = DB_PATH) -> str | None:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT label FROM results
               WHERE url = ? AND query = ? AND label IS NOT NULL
               ORDER BY date DESC
               LIMIT 1""",
            (url, query),
        ).fetchone()
        if row:
            return row["label"]
        return None
    finally:
        conn.close()


def get_history(filters: dict | None = None, db_path: str = DB_PATH) -> list[Row]:
    conn = _get_conn(db_path)
    try:
        query = "SELECT * FROM results WHERE 1=1"
        params: list[Any] = []

        if filters:
            if "date" in filters:
                query += " AND date = ?"
                params.append(filters["date"])
            if "searcher" in filters:
                query += " AND searcher = ?"
                params.append(filters["searcher"])
            if "geo" in filters:
                query += " AND geo = ?"
                params.append(filters["geo"])
            if "query" in filters:
                query += " AND query = ?"
                params.append(filters["query"])

        query += " ORDER BY date DESC, query, position"

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import os

    TEST_DB = "test_serplux.db"

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    _init_db(TEST_DB)

    test_rows: list[Row] = [
        {
            "date": "2026-06-19",
            "searcher": "google",
            "query": "test query",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://example.com/page1",
            "domain": "example.com",
            "snippet": "Test snippet 1",
            "label": "positive",
        },
        {
            "date": "2026-06-19",
            "searcher": "google",
            "query": "test query",
            "geo": "Литва",
            "region_index": 1300,
            "position": 2,
            "url": "https://example.com/page2",
            "domain": "example.com",
            "snippet": "Test snippet 2",
            "label": None,
        },
        {
            "date": "2026-06-19",
            "searcher": "google",
            "query": "another query",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://test.org",
            "domain": "test.org",
            "snippet": "Test snippet 3",
            "label": "negative",
        },
    ]

    print("=== Тест storage.py (изолированная БД: %s) ===\n" % TEST_DB)

    print("1. Первый save()...")
    inserted1 = save(test_rows, TEST_DB)
    print(f"   Вставлено: {inserted1} (ожидалось 3)\n")

    print("2. Повторный save() тех же строк (идемпотентность)...")
    inserted2 = save(test_rows, TEST_DB)
    print(f"   Вставлено: {inserted2} (ожидалось 0)\n")

    print("3. get_cached_label() для известного URL+query...")
    label1 = get_cached_label("https://example.com/page1", "test query", TEST_DB)
    print(f"   label для https://example.com/page1 + 'test query': {label1} (ожидалось 'positive')\n")

    print("4. get_cached_label() для URL без метки...")
    label2 = get_cached_label("https://example.com/page2", "test query", TEST_DB)
    print(f"   label для https://example.com/page2 + 'test query': {label2} (ожидалось None)\n")

    print("5. get_cached_label() для неизвестного URL...")
    label3 = get_cached_label("https://unknown.com", "test query", TEST_DB)
    print(f"   label для https://unknown.com + 'test query': {label3} (ожидалось None)\n")

    print("6. get_history() без фильтров...")
    history = get_history(db_path=TEST_DB)
    print(f"   Всего строк: {len(history)}")
    for i, row in enumerate(history, 1):
        print(f"   {i}. date={row['date']} query='{row['query']}' pos={row['position']} "
              f"url={row['url'][:40]} label={row['label']}")
    print()

    print("7. get_history() с фильтром query='test query'...")
    filtered = get_history({"query": "test query"}, TEST_DB)
    print(f"   Строк: {len(filtered)}")
    for row in filtered:
        print(f"   query='{row['query']}' pos={row['position']} url={row['url'][:40]}")
    print()

    print("8. Кэш переживает смену даты...")
    old_row: Row = {
        "date": "2026-06-13",
        "searcher": "google",
        "query": "chempioil",
        "geo": "Литва",
        "region_index": 1300,
        "position": 3,
        "url": "https://chempioil.com",
        "domain": "chempioil.com",
        "snippet": "Old snippet",
        "label": "neutral",
    }
    new_row: Row = {
        "date": "2026-06-20",
        "searcher": "google",
        "query": "chempioil",
        "geo": "Литва",
        "region_index": 1300,
        "position": 3,
        "url": "https://chempioil.com",
        "domain": "chempioil.com",
        "snippet": "New snippet",
        "label": None,
    }
    save([old_row], TEST_DB)
    save([new_row], TEST_DB)
    cached = get_cached_label("https://chempioil.com", "chempioil", TEST_DB)
    print(f"   Старая дата (2026-06-13): label=neutral")
    print(f"   Новая дата (2026-06-20): label=None")
    print(f"   get_cached_label(): {cached} (ожидалось 'neutral')")
    if cached == "neutral":
        print("   ✓ Кэш работает корректно")
    else:
        print("   ✗ БАГ: кэш не работает!")
    print()

    os.remove(TEST_DB)
    print("Тестовая БД удалена: %s" % TEST_DB)
    print("=== Тест завершён ===")

    print("\n=== Тест update_labels() ===")

    _init_db(TEST_DB)

    raw_rows: list[Row] = [
        {
            "date": "2026-06-22",
            "searcher": "google",
            "query": "test subject",
            "geo": "Москва",
            "region_index": 213,
            "position": 1,
            "url": "https://example.com/page1",
            "domain": "example.com",
            "snippet": "Snippet 1",
            "label": None,
        },
        {
            "date": "2026-06-22",
            "searcher": "google",
            "query": "test subject",
            "geo": "Москва",
            "region_index": 213,
            "position": 2,
            "url": "https://example.com/page2",
            "domain": "example.com",
            "snippet": "Snippet 2",
            "label": None,
        },
    ]

    print("1. save() 2 строки без меток...")
    saved = save(raw_rows, TEST_DB)
    print(f"   Вставлено: {saved} (ожидалось 2)")
    assert saved == 2

    history_before = get_history(db_path=TEST_DB)
    assert all(r["label"] is None for r in history_before)
    print("   ✓ Все label = NULL\n")

    print("2. update_labels() тех же строк с метками...")
    labeled_rows = [
        {**raw_rows[0], "label": "positive"},
        {**raw_rows[1], "label": "negative"},
    ]
    updated = update_labels(labeled_rows, TEST_DB)
    print(f"   Обновлено: {updated} (ожидалось 2)")
    assert updated == 2

    history_after = get_history(db_path=TEST_DB)
    labels = {r["url"]: r["label"] for r in history_after}
    assert labels["https://example.com/page1"] == "positive"
    assert labels["https://example.com/page2"] == "negative"
    print("   ✓ Метки записались: positive, negative\n")

    print("3. update_labels() с label=None — существующая метка НЕ затирается...")
    null_rows = [
        {**raw_rows[0], "label": None},
    ]
    updated_null = update_labels(null_rows, TEST_DB)
    print(f"   Обновлено: {updated_null} (ожидалось 0, т.к. label=None пропускается)")
    assert updated_null == 0

    still_positive = get_cached_label("https://example.com/page1", "test subject", TEST_DB)
    assert still_positive == "positive"
    print(f"   ✓ Метка 'positive' сохранилась: {still_positive}\n")

    os.remove(TEST_DB)
    print("Тестовая БД удалена: %s" % TEST_DB)
    print("=== Тест update_labels завершён ===")
