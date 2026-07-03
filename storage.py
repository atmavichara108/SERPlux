import os
import sqlite3
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# Путь к БД: env DB_PATH > дефолт (для контейнера задаётся через docker-compose)
DB_PATH = os.environ.get("DB_PATH", "serplux.db")

Row = dict[str, Any]

# Допустимые режимы разметки (должны совпадать с CHECK в БД)
LABEL_MODES = {"domains", "snippets", "full"}

# Допустимые значения тональности (должны совпадать с CHECK в БД)
SENTIMENTS = {"positive", "negative", "neutral"}


def _get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Включаем поддержку внешних ключей (важно для ON DELETE CASCADE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_db(db_path: str = DB_PATH) -> None:
    """Создаёт новую схему clients/positions/labels. Авто-клиент 'default'."""
    conn = _get_conn(db_path)
    try:
        # Клиенты
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id   TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                project_id  INTEGER,
                sheet_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Позиции (сырые данные из Topvisor)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
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
            )
        """)

        # Метки (версионированные)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS labels (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id    INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
                client_id      TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
                label_mode     TEXT NOT NULL CHECK(label_mode IN ('domains','snippets','full')),
                label_version  INTEGER NOT NULL,
                sentiment      TEXT CHECK(sentiment IN ('positive','negative','neutral')),
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(position_id, label_mode, label_version)
            )
        """)

        # Индексы
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_date ON positions(client_id, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_url_query   ON positions(url, query)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_url  ON positions(client_id, url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_position    ON labels(position_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_client_mode ON labels(client_id, label_mode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_latest      ON labels(position_id, label_mode, label_version DESC)")

        # Авто-клиент по умолчанию
        conn.execute("""
            INSERT OR IGNORE INTO clients (client_id, client_name)
            VALUES ('default', 'Default')
        """)

        conn.commit()
        log.info("БД инициализирована: %s", db_path)
    finally:
        conn.close()


_DB_INITIALIZED = False


def _ensure_db(db_path: str = DB_PATH) -> None:
    """Ленивая инициализация БД — вызывается перед каждым запросом."""
    global _DB_INITIALIZED
    if db_path != DB_PATH:
        # Тестовая БД — инициализируем каждый раз (она создаётся тестом)
        return
    if not _DB_INITIALIZED:
        _init_db(db_path)
        _DB_INITIALIZED = True


def _find_position_id(conn: sqlite3.Connection, row: Row, client_id: str) -> int | None:
    """Находит id позиции по составному ключу."""
    cur = conn.execute(
        """SELECT id FROM positions
           WHERE client_id = ? AND date = ? AND searcher = ? AND query = ?
             AND geo = ? AND position = ? AND url = ?""",
        (
            client_id,
            row["date"],
            row["searcher"],
            row["query"],
            row["geo"],
            row["position"],
            row["url"],
        ),
    )
    result = cur.fetchone()
    return result["id"] if result else None


def save(rows: list[Row], db_path: str = DB_PATH, client_id: str = "default") -> int:
    """INSERT OR IGNORE в positions. Возвращает количество вставленных строк."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    inserted = 0
    try:
        # Убеждаемся, что клиент существует
        conn.execute(
            "INSERT OR IGNORE INTO clients (client_id, client_name) VALUES (?, ?)",
            (client_id, client_id),
        )

        for row in rows:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO positions
                   (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_id,
                    row["date"],
                    row["searcher"],
                    row["query"],
                    row["geo"],
                    row["region_index"],
                    row["position"],
                    row["url"],
                    row["domain"],
                    row.get("snippet", ""),
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        log.info("Сохранено %s строк из %s (client_id=%s)", inserted, len(rows), client_id)
    finally:
        conn.close()
    return inserted


def _next_label_version(conn: sqlite3.Connection, position_id: int, label_mode: str) -> int:
    """Вычисляет следующую версию метки для пары (position_id, label_mode)."""
    cur = conn.execute(
        "SELECT COALESCE(MAX(label_version), 0) + 1 FROM labels WHERE position_id = ? AND label_mode = ?",
        (position_id, label_mode),
    )
    return cur.fetchone()[0]


def _insert_one_label(
    conn: sqlite3.Connection,
    position_id: int,
    client_id: str,
    label_mode: str,
    sentiment: str | None,
    max_retries: int = 3,
) -> int:
    """
    Вставляет одну метку в транзакции BEGIN IMMEDIATE.
    При гонке за version повторяет SELECT+INSERT до max_retries раз.
    Возвращает 1 если вставка успешна, 0 если не удалось.
    """
    for attempt in range(1, max_retries + 1):
        try:
            conn.execute("BEGIN IMMEDIATE")
            version = _next_label_version(conn, position_id, label_mode)
            conn.execute(
                """INSERT INTO labels
                   (position_id, client_id, label_mode, label_version, sentiment)
                   VALUES (?, ?, ?, ?, ?)""",
                (position_id, client_id, label_mode, version, sentiment),
            )
            conn.commit()
            return 1
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            if "UNIQUE constraint failed" in str(exc):
                log.warning(
                    "Гонка версий для position_id=%s mode=%s, попытка %s/%s",
                    position_id, label_mode, attempt, max_retries,
                )
                if attempt < max_retries:
                    # Небольшая пауза перед повтором
                    time.sleep(0.01 * attempt)
                    continue
            log.error(
                "Не удалось вставить метку для position_id=%s mode=%s: %s",
                position_id, label_mode, exc,
            )
            return 0
        except Exception as exc:
            conn.rollback()
            log.error(
                "Ошибка вставки метки для position_id=%s mode=%s: %s",
                position_id, label_mode, exc,
            )
            return 0
    return 0


def insert_labels(rows: list[Row], db_path: str = DB_PATH) -> int:
    """
    INSERT новой версии метки для каждой строки.
    sentiment=None пропускается.
    label_mode берётся из row.get('label_mode', 'snippets').
    client_id берётся из row.get('client_id', 'default').
    Возвращает количество вставленных меток.
    """
    if not rows:
        return 0

    if not rows:
        return 0

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    # Ручное управление транзакциями, чтобы _insert_one_label мог делать
    # BEGIN IMMEDIATE для каждой метки отдельно.
    conn.isolation_level = None
    inserted = 0
    try:
        for row in rows:
            sentiment = row.get("sentiment") or row.get("label")
            if sentiment is None:
                continue

            label_mode = row.get("label_mode", "snippets")
            if label_mode not in LABEL_MODES:
                log.warning("Неизвестный label_mode=%s, пропускаю", label_mode)
                continue

            client_id = row.get("client_id", "default")

            # Убеждаемся, что клиент существует (autocommit при isolation_level=None)
            conn.execute(
                "INSERT OR IGNORE INTO clients (client_id, client_name) VALUES (?, ?)",
                (client_id, client_id),
            )

            position_id = _find_position_id(conn, row, client_id)
            if position_id is None:
                log.warning(
                    "Позиция не найдена для метки: %s %s %s %s pos=%s url=%s",
                    client_id, row["date"], row["searcher"], row["query"],
                    row["position"], row["url"],
                )
                continue

            inserted += _insert_one_label(conn, position_id, client_id, label_mode, sentiment)

        log.info("Вставлено %s меток из %s", inserted, len(rows))
    finally:
        conn.close()
    return inserted


def update_labels(rows: list[Row], db_path: str = DB_PATH) -> int:
    """DEPRECATED. Оставлен для обратной совместимости — делегирует insert_labels."""
    log.warning("update_labels() устарела, используйте insert_labels()")
    return insert_labels(rows, db_path=db_path)


def get_cached_label(url: str, query: str, db_path: str = DB_PATH) -> str | None:
    """
    Ищет последнюю не-NULL sentiment по паре (url, query)
    через JOIN positions + labels, сортировка по created_at DESC.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT l.sentiment
               FROM positions p
               JOIN labels l ON l.position_id = p.id
               WHERE p.url = ? AND p.query = ? AND l.sentiment IS NOT NULL
               ORDER BY l.created_at DESC, l.id DESC
               LIMIT 1""",
            (url, query),
        ).fetchone()
        return row["sentiment"] if row else None
    finally:
        conn.close()


def _row_from_join(r: sqlite3.Row, with_label: bool = True) -> Row:
    """Преобразует строку JOIN positions+labels в Row по контракту."""
    row: Row = {
        "date": r["date"],
        "searcher": r["searcher"],
        "query": r["query"],
        "geo": r["geo"],
        "region_index": r["region_index"],
        "position": r["position"],
        "url": r["url"],
        "domain": r["domain"],
        "snippet": r["snippet"],
        "client_id": r["client_id"],
    }
    if with_label:
        sentiment = r["sentiment"]
        row["sentiment"] = sentiment
        row["label"] = sentiment  # алиас для обратной совместимости
        row["label_mode"] = r["label_mode"]
        row["label_version"] = r["label_version"]
    else:
        row["sentiment"] = None
        row["label"] = None
        row["label_mode"] = None
        row["label_version"] = None
    return row


def get_history(filters: dict | None = None, db_path: str = DB_PATH) -> list[Row]:
    """
    Возвращает строки из positions с JOIN labels.
    По умолчанию — последняя метка на позицию (по created_at DESC).
    filters:
      - date, searcher, geo, query: фильтры по positions
      - client_id: фильтр по клиенту
      - label_version='all': все версии меток
      - label_mode: фильтр по режиму разметки
    """
    _ensure_db(db_path)
    filters = filters or {}
    conn = _get_conn(db_path)
    try:
        all_versions = filters.get("label_version") == "all"
        client_id = filters.get("client_id")

        params: list[Any] = []
        where = ["1=1"]

        if client_id is not None:
            where.append("p.client_id = ?")
            params.append(client_id)

        for field in ("date", "searcher", "geo", "query"):
            if field in filters:
                where.append(f"p.{field} = ?")
                params.append(filters[field])

        if all_versions:
            # Все версии меток
            extra_where = ""
            extra_params: list[Any] = []
            if "label_mode" in filters:
                extra_where = " AND l.label_mode = ?"
                extra_params.append(filters["label_mode"])
            query = f"""
                SELECT p.*, l.sentiment, l.label_mode, l.label_version
                FROM positions p
                JOIN labels l ON l.position_id = p.id
                WHERE {' AND '.join(where)}{extra_where}
                ORDER BY p.date DESC, p.query, p.position, l.created_at DESC
            """
            rows = conn.execute(query, params + extra_params).fetchall()
            return [_row_from_join(r) for r in rows]

        # По умолчанию — последняя метка на позицию
        extra_where = ""
        extra_params: list[Any] = []
        if "label_mode" in filters:
            extra_where = " AND l.label_mode = ?"
            extra_params.append(filters["label_mode"])

        query = f"""
            SELECT p.*, l.sentiment, l.label_mode, l.label_version
            FROM positions p
            JOIN labels l ON l.position_id = p.id
            WHERE {' AND '.join(where)}
              AND l.id = (
                  SELECT id
                  FROM labels
                  WHERE position_id = p.id
                    AND sentiment IS NOT NULL
                    {("AND label_mode = ?" if "label_mode" in filters else "")}
                  ORDER BY created_at DESC, id DESC
                  LIMIT 1
              )
              {extra_where}
            ORDER BY p.date DESC, p.query, p.position
        """
        if "label_mode" in filters:
            # label_mode используется и в подзапросе, и в основном запросе
            params_with_mode = params + [filters["label_mode"]] + extra_params
        else:
            params_with_mode = params + extra_params

        rows = conn.execute(query, params_with_mode).fetchall()

        # Позиции без меток не попадают в JOIN-результат.
        # Для совместимости с предыдущим поведением (results содержало все строки)
        # добавляем строки без меток, если не запрошены конкретные режим/версия.
        if "label_mode" not in filters and not all_versions:
            labeled_ids = {r["id"] for r in rows}
            no_label_where = where.copy()
            if labeled_ids:
                placeholders = ",".join("?" * len(labeled_ids))
                no_label_where.append(f"p.id NOT IN ({placeholders})")
                no_label_params = params + list(labeled_ids)
            else:
                no_label_params = params
            no_label_query = f"""
                SELECT p.*
                FROM positions p
                WHERE {' AND '.join(no_label_where)}
                ORDER BY p.date DESC, p.query, p.position
            """
            no_label_rows = conn.execute(no_label_query, no_label_params).fetchall()
            rows_result = [_row_from_join(r) for r in rows]
            rows_result.extend([_row_from_join(r, with_label=False) for r in no_label_rows])
            return rows_result

        return [_row_from_join(r) for r in rows]
    finally:
        conn.close()


def get_label_history(position_id: int, db_path: str = DB_PATH) -> list[dict]:
    """Возвращает все версии меток для позиции."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT label_mode, label_version, sentiment, created_at
               FROM labels
               WHERE position_id = ?
               ORDER BY label_mode, label_version""",
            (position_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import os as _os

    TEST_DB = "test_serplux.db"

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)

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
            "sentiment": "positive",
            "label_mode": "snippets",
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
            "sentiment": None,
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
            "sentiment": "negative",
            "label_mode": "snippets",
        },
    ]

    print("=== Тест storage.py (изолированная БД: %s) ===\n" % TEST_DB)

    print("1. Первый save()...")
    inserted1 = save(test_rows, TEST_DB)
    print(f"   Вставлено: {inserted1} (ожидалось 3)\n")

    print("2. Повторный save() тех же строк (идемпотентность)...")
    inserted2 = save(test_rows, TEST_DB)
    print(f"   Вставлено: {inserted2} (ожидалось 0)\n")

    print("3. insert_labels()...")
    labeled = insert_labels(test_rows, TEST_DB)
    print(f"   Вставлено меток: {labeled} (ожидалось 2)\n")

    print("4. insert_labels() повторно — новая версия...")
    relabeled = [
        {**test_rows[0], "sentiment": "neutral"},
        {**test_rows[2], "sentiment": "positive"},
    ]
    labeled2 = insert_labels(relabeled, TEST_DB)
    print(f"   Вставлено меток: {labeled2} (ожидалось 2), версия должна быть 2\n")

    print("5. get_cached_label() для известного URL+query...")
    label1 = get_cached_label("https://example.com/page1", "test query", TEST_DB)
    print(f"   label для https://example.com/page1 + 'test query': {label1} (ожидалось 'neutral')\n")

    print("6. get_cached_label() для URL без метки...")
    label2 = get_cached_label("https://example.com/page2", "test query", TEST_DB)
    print(f"   label для https://example.com/page2 + 'test query': {label2} (ожидалось None)\n")

    print("7. get_history() без фильтров...")
    history = get_history(db_path=TEST_DB)
    print(f"   Всего строк: {len(history)}")
    for i, row in enumerate(history, 1):
        print(f"   {i}. date={row['date']} query='{row['query']}' pos={row['position']} "
              f"url={row['url'][:40]} sentiment={row['sentiment']} version={row['label_version']}")
    print()

    print("8. get_history(label_version='all')...")
    all_versions = get_history({"label_version": "all"}, TEST_DB)
    print(f"   Всего строк: {len(all_versions)}")
    for row in all_versions:
        print(f"   query='{row['query']}' pos={row['position']} sentiment={row['sentiment']} "
              f"mode={row['label_mode']} version={row['label_version']}")
    print()

    print("9. Кэш переживает смену даты...")
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
        "sentiment": "neutral",
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
        "sentiment": None,
    }
    save([old_row], TEST_DB)
    save([new_row], TEST_DB)
    insert_labels([old_row], TEST_DB)
    cached = get_cached_label("https://chempioil.com", "chempioil", TEST_DB)
    print(f"   get_cached_label(): {cached} (ожидалось 'neutral')")
    if cached == "neutral":
        print("   ✓ Кэш работает корректно")
    else:
        print("   ✗ БАГ: кэш не работает!")
    print()

    print("10. DEPRECATED update_labels() делегирует insert_labels()...")
    deprecated_rows = [
        {**old_row, "sentiment": "positive"},
    ]
    updated = update_labels(deprecated_rows, TEST_DB)
    print(f"    Обновлено: {updated} (ожидалось 1)\n")

    _os.remove(TEST_DB)
    print("Тестовая БД удалена: %s" % TEST_DB)
    print("=== Тест завершён ===")
