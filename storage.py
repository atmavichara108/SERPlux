import os
import sqlite3
import time
from typing import Any

import config

log = config.setup_logging(__name__)

# Путь к БД: env DB_PATH > дефолт (для контейнера задаётся через docker-compose)
DB_PATH = os.environ.get("DB_PATH", "serplux.db")

Row = dict[str, Any]

# Допустимые режимы разметки (должны совпадать с CHECK в БД).
# auto/deep — актуальные режимы labeler.py; domains/snippets/full оставлены
# для обратной совместимости со старыми метками в БД.
LABEL_MODES = {"auto", "deep", "domains", "snippets", "full"}

# Допустимые значения тональности (должны совпадать с CHECK в БД)
SENTIMENTS = {"positive", "negative", "neutral"}


def _get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Включаем поддержку внешних ключей (важно для ON DELETE CASCADE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_domain_labels_schema(conn: sqlite3.Connection) -> None:
    """
    Создаёт таблицу domain_labels с ключом (domain, query).
    Старые схемы с колонками url или geo удаляются: эталон будет импортирован заново.
    Это миграция: старый snippet-кэш затрётся, это ОК.
    """
    old_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(domain_labels)").fetchall()
    }
    if old_cols and ("geo" in old_cols or "url" in old_cols):
        log.warning("domain_labels: обнаружена старая схема, пересоздаю")
        conn.execute("DROP TABLE IF EXISTS domain_labels")
    conn.execute("DROP INDEX IF EXISTS idx_domlbl_url_query")
    conn.execute("DROP INDEX IF EXISTS idx_domlbl_geo")
    conn.execute("DROP INDEX IF EXISTS idx_domlbl_client_domain")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_labels (
            domain      TEXT NOT NULL,
            query       TEXT NOT NULL,
            sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
            source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (domain, query)
        )
    """)


def _init_db(db_path: str = DB_PATH) -> None:
    """Создаёт новую схему clients/positions/labels. Авто-клиент 'default'."""
    conn = _get_conn(db_path)
    try:
        # Клиенты
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id     TEXT PRIMARY KEY,
                client_name   TEXT NOT NULL,
                project_id    INTEGER,
                sheet_id      TEXT,
                searchers     TEXT,                      -- JSON список, напр. ["google","yandex_ru"]
                geos          TEXT,                      -- JSON список гео
                regions_map   TEXT,                      -- JSON массив регионов или имя файла (legacy)
                queries       TEXT,                      -- JSON массив субъектов [{key, display}]
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Миграция: добавляем колонки если их нет (для старых БД)
        for col, dtype in [
            ("searchers", "TEXT"),
            ("geos", "TEXT"),
            ("regions_map", "TEXT"),
            ("queries", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

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
                label_mode     TEXT NOT NULL CHECK(label_mode IN ('auto','deep','domains','snippets','full')),
                label_version  INTEGER NOT NULL,
                sentiment      TEXT CHECK(sentiment IN ('positive','negative','neutral')),
                confidence     TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(position_id, label_mode, label_version)
            )
        """)

        # Статус последнего прогона (персистентный, переживает рестарт контейнера)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_status (
                id            INTEGER PRIMARY KEY CHECK(id = 1),
                started_at    TEXT,
                finished_at   TEXT,
                status        TEXT NOT NULL DEFAULT 'idle',
                client_id     TEXT,
                stats         TEXT,
                message       TEXT DEFAULT '',
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO run_status (id, status)
            VALUES (1, 'idle')
        """)

        # run_id (v1.1): криптографически случайный идентификатор прогона.
        # Для старых БД колонка добавляется миграционно (идемпотентно).
        try:
            conn.execute("ALTER TABLE run_status ADD COLUMN run_id TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

        # Журнал валидации/конфликтов разметки (v1.1 workstream A).
        # Append-oriented: записи привязаны к run_id, ретеншн через prune.
        conn.execute("""
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
                conflict_type      TEXT NOT NULL CHECK(conflict_type IN (
                    'manual_neutral','unmatched_neutral','manual_conflict','invalid_or_unknown')),
                recommended_action TEXT,
                created_at         TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lblconf_run ON label_conflicts(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lblconf_key ON label_conflicts(domain, query)")

        # Персистентный реестр LLM-провайдеров (v1.0.2 techdebt):
        # источник истины для провайдеров, добавленных/изменённых через UI.
        # Встроенный `opencode-zen` из config.py сидится сюда при первой инициализации.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                provider_id      TEXT PRIMARY KEY,
                enabled          INTEGER NOT NULL DEFAULT 1,
                priority         INTEGER NOT NULL DEFAULT 999,
                default_model    TEXT NOT NULL DEFAULT '',
                models           TEXT NOT NULL DEFAULT '[]',
                endpoint         TEXT NOT NULL DEFAULT '',
                api_key_env_var  TEXT NOT NULL DEFAULT '',
                updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Справочник эталонной разметки: ключ (domain, query), без geo и client_id.
        # Приоритет source: manual_l1 > snippet/page. manual_l1 не перезаписывается
        # автоматическими источниками (snippet/page), только другим manual_l1.
        _ensure_domain_labels_schema(conn)

        # Индексы
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_date ON positions(client_id, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_url_query   ON positions(url, query)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_url  ON positions(client_id, url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_position    ON labels(position_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_client_mode ON labels(client_id, label_mode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_latest      ON labels(position_id, label_mode, label_version DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_domlbl_domain_query ON domain_labels(domain, query)")

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


# ─── Персистентный реестр LLM-провайдеров (v1.0.2 techdebt) ─────────────────

def _providers_from_rows(rows) -> list[dict]:
    """Преобразует строки таблицы providers в список конфигов провайдеров."""
    result = []
    for row in rows:
        result.append({
            "provider_id": row["provider_id"],
            "enabled": bool(row["enabled"]),
            "priority": int(row["priority"]),
            "default_model": row["default_model"],
            "models": _deserialize_json_field(row["models"]),
            "endpoint": row["endpoint"],
            "api_key_env_var": row["api_key_env_var"],
        })
    return result


def save_provider(provider_id: str, cfg: dict, db_path: str = DB_PATH) -> None:
    """INSERT OR REPLACE провайдера в персистентный реестр."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO providers
                   (provider_id, enabled, priority, default_model, models, endpoint, api_key_env_var, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                 ON CONFLICT(provider_id) DO UPDATE SET
                     enabled = excluded.enabled,
                     priority = excluded.priority,
                     default_model = excluded.default_model,
                     models = excluded.models,
                     endpoint = excluded.endpoint,
                     api_key_env_var = excluded.api_key_env_var,
                     updated_at = datetime('now')""",
            (
                provider_id,
                1 if cfg.get("enabled", False) else 0,
                int(cfg.get("priority", 999)),
                cfg.get("default_model", ""),
                _serialize_json_field(cfg.get("models", []) or []),
                cfg.get("endpoint", ""),
                cfg.get("api_key_env_var", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_provider(provider_id: str, db_path: str = DB_PATH) -> None:
    """Удаляет провайдера из персистентного реестра."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM providers WHERE provider_id = ?", (provider_id,))
        conn.commit()
    finally:
        conn.close()


def list_persisted_providers(db_path: str = DB_PATH) -> list[dict]:
    """Возвращает все персистентные провайдеры (в порядке приоритета)."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT provider_id, enabled, priority, default_model, models, endpoint, api_key_env_var
               FROM providers ORDER BY priority ASC, provider_id ASC""",
        ).fetchall()
        return _providers_from_rows(rows)
    finally:
        conn.close()


def load_providers_into_runtime(defaults: dict, db_path: str = DB_PATH) -> dict:
    """Возвращает словарь провайдеров: встроенные `defaults` + персистентные из БД.

    Персистентный реестр перекрывает встроенный config (источник истины — БД
    для провайдеров, которыми управляли через UI). Встроенные провайдеры, которых
    нет в БД, сохраняются как есть.
    """
    _ensure_db(db_path)
    runtime = dict(defaults)
    persisted = list_persisted_providers(db_path)
    for p in persisted:
        runtime[p["provider_id"]] = {
            "enabled": p["enabled"],
            "priority": p["priority"],
            "default_model": p["default_model"],
            "models": p["models"],
            "endpoint": p["endpoint"],
            "api_key_env_var": p["api_key_env_var"],
        }
    return runtime


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
    confidence: str = "high",
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
                   (position_id, client_id, label_mode, label_version, sentiment, confidence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (position_id, client_id, label_mode, version, sentiment, confidence),
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
    label_mode берётся из row.get('label_mode', 'auto').
    client_id берётся из row.get('client_id', 'default').
    Возвращает количество вставленных меток.
    """
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

            label_mode = row.get("label_mode", "auto")
            if label_mode not in LABEL_MODES:
                log.warning("Неизвестный label_mode=%s, пропускаю", label_mode)
                continue

            client_id = row.get("client_id", "default")
            confidence = row.get("confidence", "high")

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

            inserted += _insert_one_label(conn, position_id, client_id, label_mode, sentiment, confidence)

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


def normalize_domain(value: str | None) -> str:
    """Нормализует URL или домен до ``domain``.

    Примеры:
        >>> normalize_domain('https://www.chempioil.com/de')
        'chempioil.com'
        >>> normalize_domain('www.occrp.org')
        'occrp.org'
        >>> normalize_domain('https://sctchemicals.ae/production/')
        'sctchemicals.ae'
        >>> normalize_domain('OCCRP.ORG')
        'occrp.org'
    """
    if not value:
        return ""
    from urllib.parse import urlparse

    raw = str(value).strip()
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    domain = parsed.hostname or ""
    return domain.lower().removeprefix("www.").strip()


def normalize_query(value: str | None) -> str:
    """Нормализует ключ запроса: пробелы по краям и регистр."""
    return (value or "").strip().lower()


def normalize_url(url: str) -> str:
    """
    Канонизирует URL для ключа в domain_labels:
      - lowercase scheme, host и path
      - убирает fragment (#...)
      - убирает trailing slash, если он единственный символ пути
      - query-параметры оставляет как есть
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url.strip())
        scheme = (parsed.scheme or "").lower()
        netloc = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        # Убираем trailing slash в конце пути
        if path.endswith("/"):
            path = path[:-1]
        params = parsed.params
        query = parsed.query
        fragment = ""  # отбрасываем
        return urlunparse((scheme, netloc, path, params, query, fragment))
    except Exception:
        # Fallback: хотя бы lowercase и без fragment
        return url.strip().lower().split("#")[0]


def _normalize_geo(geo: str) -> str:
    """
    Нормализует geo: strip + lowercase.
    Любое значение (например 'Germany', 'germany', 'Германия', 'германия')
    приводится к единому нижнему регистру без потери данных и без маппинга.
    """
    return (geo or "").strip().lower()


def get_domain_label(domain_or_url: str, query: str, db_path: str = DB_PATH, *legacy) -> str | None:
    """
    Возвращает sentiment из справочника domain_labels по (domain, query).
    """
    # Третий geo-аргумент старого контракта игнорируется; последний путь к БД
    # сохраняем для перехода существующих интеграционных вызовов.
    if legacy:
        db_path = legacy[-1]
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT sentiment
               FROM domain_labels
               WHERE domain = ? AND query = ?""",
            (normalize_domain(domain_or_url), normalize_query(query)),
        ).fetchone()
        return row["sentiment"] if row else None
    finally:
        conn.close()


def get_domain_label_record(
    domain_or_url: str, query: str, db_path: str = DB_PATH
) -> dict | None:
    """
    Возвращает полную запись эталона по (domain, query):
    {domain, query, sentiment, source, updated_at} или None.

    Используется валидатором разметки (v1.1 workstream A): источник записи
    нужен, чтобы отличать жёсткий референс manual_l1 от legacy-автозаписей
    (snippet/page), которые эталоном не являются.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        domain = normalize_domain(domain_or_url)
        query_norm = normalize_query(query)
        if not domain or not query_norm:
            return None
        row = conn.execute(
            """SELECT domain, query, sentiment, source, updated_at
               FROM domain_labels
               WHERE domain = ? AND query = ?""",
            (domain, query_norm),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_domain_label(
    domain_or_url: str | None = None,
    query: str | None = None,
    *args,
    sentiment: str | None = None,
    source: str | None = None,
    db_path: str = DB_PATH,
    url: str | None = None,
    **kwargs,
) -> None:
    """
    INSERT или UPDATE записи в domain_labels по PRIMARY KEY (domain, query).

    Приоритет source:
      - 'manual_l1' — не перезаписывается источниками 'snippet' или 'page'.
      - 'snippet'/'page' могут перезаписывать друг друга и 'snippet'/'page'.
      - 'manual_l1' может перезаписать любую существующую запись.
    """
    domain_or_url = domain_or_url or url
    # Принимаем старые позиционные geo-вызовы только для плавного перехода;
    # geo никогда не участвует в новом ключе.
    if args:
        if len(args) == 2:
            sentiment, source = args
        elif len(args) >= 3:
            _, sentiment, source = args[:3]
            if len(args) >= 4:
                db_path = args[3]
    if "geo" in kwargs:
        kwargs.pop("geo")
    if source not in ("manual_l1", "snippet", "page"):
        raise ValueError(f"source must be one of manual_l1/snippet/page, got {source}")

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        domain = normalize_domain(domain_or_url)
        query_norm = normalize_query(query)
        # Проверяем существующую запись и её source/sentiment
        existing = conn.execute(
            """SELECT source, sentiment FROM domain_labels
               WHERE domain = ? AND query = ?""",
            (domain, query_norm),
        ).fetchone()

        if existing is not None and existing["source"] == "manual_l1":
            if source != "manual_l1":
                # Существующая manual_l1 не перезаписывается автоматическими источниками
                log.debug(
                    "domain_labels: пропускаю обновление %s/%s (manual_l1 -> %s)",
                    domain, query_norm, source
                )
                return
            if existing["sentiment"] != sentiment:
                # Конфликт ручных эталонов: last-write-wins запрещён
                raise ValueError(
                    f"manual_l1 conflict for ({domain}, {query_norm}): "
                    f"existing={existing['sentiment']}, new={sentiment}; "
                    "требуется явное разрешение оператора (last-write-wins запрещён)"
                )
            # Тот же sentiment — идемпотентный upsert, идём дальше

        conn.execute(
            """INSERT INTO domain_labels
                   (domain, query, sentiment, source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(domain, query) DO UPDATE SET
                    sentiment = excluded.sentiment,
                    source = excluded.source,
                    updated_at = datetime('now')""",
            (domain, query_norm, sentiment, source),
        )
        conn.commit()
    finally:
        conn.close()


def bulk_upsert_domain_labels(
    items: list[dict],
    db_path: str = DB_PATH,
) -> None:
    """
    Массовый upsert записей в domain_labels.

    Каждый элемент items — dict с ключами: domain (или url), query, sentiment, source.
    Применяются те же правила приоритета source, что и в upsert_domain_label.
    """
    valid_sources = {"manual_l1", "snippet", "page"}
    for item in items:
        if item["source"] not in valid_sources:
            raise ValueError(
                f"source must be one of manual_l1/snippet/page, got {item['source']}"
            )

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        # Сначала находим все существующие manual_l1 (с их sentiment),
        # которые нельзя перезаписывать автоматикой и нельзя молча конфликтовать
        keys = [
            (normalize_domain(item.get("domain", item.get("url", ""))), normalize_query(item["query"]))
            for item in items
        ]
        placeholders = ",".join("(?, ?)" for _ in keys)
        if placeholders:
            flat_keys = [v for tup in keys for v in tup]
            existing_manual = {
                (row["domain"], row["query"]): row["sentiment"]
                for row in conn.execute(
                    f"""SELECT domain, query, sentiment FROM domain_labels
                        WHERE (domain, query) IN ({placeholders})
                          AND source = 'manual_l1'""",
                    flat_keys,
                ).fetchall()
            }
        else:
            existing_manual = {}

        # Intra-batch учёт manual_l1: два конфликтующих manual в одном батче
        # тоже дают ValueError, а не last-write-wins
        seen_manual: dict[tuple[str, str], str] = {}

        for item in items:
            domain = normalize_domain(item.get("domain", item.get("url", "")))
            query = normalize_query(item["query"])
            sentiment = item["sentiment"]
            source = item["source"]
            key = (domain, query)

            existing_sentiment = existing_manual.get(key) or seen_manual.get(key)
            if existing_sentiment is not None:
                if source != "manual_l1":
                    continue
                if existing_sentiment != sentiment:
                    raise ValueError(
                        f"manual_l1 conflict for ({domain}, {query}): "
                        f"existing={existing_sentiment}, new={sentiment}; "
                        "требуется явное разрешение оператора (last-write-wins запрещён)"
                    )

            conn.execute(
                """INSERT INTO domain_labels
                       (domain, query, sentiment, source, updated_at)
                     VALUES (?, ?, ?, ?, datetime('now'))
                     ON CONFLICT(domain, query) DO UPDATE SET
                        sentiment = excluded.sentiment,
                        source = excluded.source,
                        updated_at = datetime('now')""",
                (domain, query, sentiment, source),
            )
            if source == "manual_l1":
                seen_manual[key] = sentiment
        conn.commit()
    finally:
        conn.close()


# ─── Журнал валидации/конфликтов разметки (v1.1 workstream A) ─────────────────

# Допустимые категории конфликтов (совпадают с CHECK в таблице label_conflicts)
CONFLICT_TYPES = ("manual_neutral", "unmatched_neutral", "manual_conflict", "invalid_or_unknown")


def save_label_conflicts(records: list[dict], db_path: str = DB_PATH) -> int:
    """
    Вставляет записи в журнал label_conflicts (append-only аудит).

    Валидация КАЖДОЙ записи выполняется ДО вставки: невалидная запись в
    середине батча отменяет весь батч (ValueError, ничего не записано).
    Отсутствующие ключи записываются как NULL.
    Возвращает число вставленных записей.
    """
    if not records:
        return 0

    # Валидация всех записей до любой вставки.
    # Пустые domain/query ДОПУСТИМЫ: категория invalid_or_unknown как раз
    # фиксирует строки с невалидным ключом ("" удовлетворяет NOT NULL).
    for rec in records:
        if rec.get("conflict_type") not in CONFLICT_TYPES:
            raise ValueError(
                f"conflict_type must be one of {'/'.join(CONFLICT_TYPES)}, "
                f"got {rec.get('conflict_type')!r}"
            )

    columns = (
        "run_id", "domain", "url", "query", "geo", "searcher", "position",
        "observed_label", "manual_label", "source", "confidence",
        "conflict_type", "recommended_action",
    )
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        for rec in records:
            conn.execute(
                f"INSERT INTO label_conflicts ({col_list}) VALUES ({placeholders})",
                tuple(rec.get(col) for col in columns),
            )
        conn.commit()
        return len(records)
    finally:
        conn.close()


def get_label_conflicts(
    run_id: str | None = None, limit: int = 500, db_path: str = DB_PATH
) -> list[dict]:
    """
    Возвращает записи журнала label_conflicts (новые сверху: ORDER BY id DESC).
    Если задан run_id — только записи этого прогона.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        query = "SELECT * FROM label_conflicts"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prune_label_conflicts(keep_last_runs: int = 50, db_path: str = DB_PATH) -> int:
    """
    Ретеншн журнала: оставляет записи только последних keep_last_runs прогонов
    (по MAX(created_at) каждого run_id). Записи с run_id=NULL не удаляются —
    это журнал прямых вызовов label() вне прогона (скрипты/отладка).
    Возвращает суммарное число удалённых записей.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        keep_rows = conn.execute(
            """SELECT run_id, MAX(created_at) AS last_ts
               FROM label_conflicts
               WHERE run_id IS NOT NULL
               GROUP BY run_id
               ORDER BY last_ts DESC
               LIMIT ?""",
            (keep_last_runs,),
        ).fetchall()
        keep_ids = [r["run_id"] for r in keep_rows]

        removed = 0
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            cur = conn.execute(
                f"""DELETE FROM label_conflicts
                    WHERE run_id IS NOT NULL AND run_id NOT IN ({placeholders})""",
                keep_ids,
            )
            removed += cur.rowcount
        else:
            cur = conn.execute("DELETE FROM label_conflicts WHERE run_id IS NOT NULL")
            removed += cur.rowcount

        # Записи с run_id=NULL НЕ удаляются: валидатор пишет их при прямых
        # вызовах label() без прогона; production-прогоны всегда имеют run_id
        # и ретеншн по keep_last_runs.

        conn.commit()
        if removed:
            log.info("prune_label_conflicts: удалено %s записей", removed)
        return removed
    finally:
        conn.close()


# ─── Управление статусом прогона ──────────────────────────────────────────────


def get_run_status(db_path: str = DB_PATH) -> dict:
    """
    Возвращает последний статус прогона из таблицы run_status.
    Поле stats десериализуется из JSON.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT started_at, finished_at, status, client_id, stats, message, run_id
               FROM run_status WHERE id = 1"""
        ).fetchone()
        if row is None:
            return {
                "started_at": None,
                "finished_at": None,
                "status": "idle",
                "client_id": None,
                "stats": None,
                "message": "",
                "run_id": None,
            }
        result = dict(row)
        stats = result.get("stats")
        if isinstance(stats, str) and stats:
            try:
                result["stats"] = _json.loads(stats)
            except (ValueError, TypeError):
                result["stats"] = None
        return result
    finally:
        conn.close()


def update_run_status(fields: dict, db_path: str = DB_PATH) -> None:
    """
    Атомарно обновляет поля статуса прогона (id=1).
    Допустимые поля: started_at, finished_at, status, client_id, stats, message, run_id.
    stats может быть dict/list — будет сериализован в JSON.
    """
    allowed = {"started_at", "finished_at", "status", "client_id", "stats", "message", "run_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Недопустимые поля run_status: {', '.join(sorted(unknown))}")

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        values = {}
        for key, value in fields.items():
            if key == "stats" and not isinstance(value, (str, type(None))):
                values[key] = _json.dumps(value, ensure_ascii=False)
            else:
                values[key] = value

        if not values:
            return

        set_clause = ", ".join(f"{col} = ?" for col in values)
        params = list(values.values())
        params.append(1)  # id
        conn.execute(
            f"""UPDATE run_status
                SET {set_clause}, updated_at = datetime('now')
                WHERE id = ?""",
            params,
        )
        conn.commit()
    finally:
        conn.close()


# ─── Управление профилями клиентов ────────────────────────────────────────────


def get_dates(client_id: str | None = None, db_path: str = DB_PATH) -> list[str]:
    """Возвращает уникальные даты из positions, отсортированные по убыванию."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        if client_id:
            rows = conn.execute(
                "SELECT DISTINCT date FROM positions WHERE client_id = ? ORDER BY date DESC",
                (client_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT date FROM positions ORDER BY date DESC"
            ).fetchall()
        return [r["date"] for r in rows]
    finally:
        conn.close()


import json as _json

_CLIENT_COLUMNS = {"client_name", "project_id", "sheet_id", "searchers", "geos", "regions_map", "queries"}


def _serialize_json_field(value: list | None) -> str | None:
    """Сериализует список в JSON-строку или возвращает None."""
    if value is None:
        return None
    return _json.dumps(value, ensure_ascii=False)


def _serialize_json_list_or_str(value: list | str | None) -> str | None:
    """Сериализует список в JSON-строку; строку оставляет как есть (legacy)."""
    if value is None:
        return None
    if isinstance(value, list):
        return _json.dumps(value, ensure_ascii=False)
    return value  # legacy-строка имени файла


def _deserialize_json_field(value: str | None) -> list:
    """Десериализует JSON-строку в список. Пустое/невалидное -> []."""
    if not value:
        return []
    try:
        result = _json.loads(value)
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


def _deserialize_regions_map(value: str | None) -> list[dict] | str | list:
    """
    Десериализует regions_map.
    - JSON-массив -> список dict
    - None/пустое -> []
    - Строка (legacy-имя файла) -> исходная строка + WARNING
    """
    if not value:
        return []
    try:
        result = _json.loads(value)
        if isinstance(result, list):
            return result
        return []
    except (ValueError, TypeError):
        log.warning("regions_map хранится как строка (legacy): %s", value)
        return value


def _hydrate_client(row: sqlite3.Row) -> dict:
    """Превращает строку clients в dict, раскрывая JSON-поля."""
    client = dict(row)
    client["searchers"] = _deserialize_json_field(client.get("searchers"))
    client["geos"] = _deserialize_json_field(client.get("geos"))
    client["queries"] = _deserialize_json_field(client.get("queries"))
    client["regions_map"] = _deserialize_regions_map(client.get("regions_map"))
    return client


def list_clients(db_path: str = DB_PATH) -> list[dict]:
    """Возвращает список клиентов с полями профиля."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT client_id, client_name, project_id, sheet_id, searchers, geos, regions_map, queries
               FROM clients
               ORDER BY client_id"""
        ).fetchall()
        return [_hydrate_client(r) for r in rows]
    finally:
        conn.close()


def get_client(client_id: str, db_path: str = DB_PATH) -> dict | None:
    """Возвращает клиента по id или None, если не найден."""
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT client_id, client_name, project_id, sheet_id, searchers, geos, regions_map, queries
               FROM clients
               WHERE client_id = ?""",
            (client_id,),
        ).fetchone()
        return _hydrate_client(row) if row else None
    finally:
        conn.close()


def create_client(
    client_id: str,
    client_name: str,
    project_id: int | None = None,
    sheet_id: str | None = None,
    searchers: list[str] | None = None,
    geos: list[str] | None = None,
    regions_map: list[dict] | str | None = None,
    queries: list[dict] | None = None,
    db_path: str = DB_PATH,
) -> None:
    """
    Создаёт нового клиента. searchers/geos/queries передаются как списки,
    сериализуются в JSON. regions_map может быть JSON-массивом или legacy-строкой.
    Выбрасывает ValueError, если client_id уже существует.
    """
    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO clients
                   (client_id, client_name, project_id, sheet_id,
                    searchers, geos, regions_map, queries, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                client_id,
                client_name,
                project_id,
                sheet_id,
                _serialize_json_field(searchers),
                _serialize_json_field(geos),
                _serialize_json_list_or_str(regions_map),
                _serialize_json_field(queries),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(f"client_id '{client_id}' already exists") from exc
    finally:
        conn.close()


def update_client(client_id: str, db_path: str = DB_PATH, **fields) -> None:
    """
    Обновляет поля профиля клиента. Допустимые поля: client_name, project_id, sheet_id,
    searchers, geos, regions_map, queries. searchers/geos/queries принимаются как списки.
    regions_map может быть JSON-массивом или legacy-строкой.
    Обновляет updated_at. Выбрасывает ValueError, если клиент не найден
    или переданы недопустимые поля.
    """
    unknown = set(fields) - _CLIENT_COLUMNS
    if unknown:
        raise ValueError(f"Недопустимые поля: {', '.join(sorted(unknown))}")

    # Сериализуем JSON-поля перед записью в БД
    processed_fields = {}
    for key, value in fields.items():
        if key in ("searchers", "geos", "queries"):
            processed_fields[key] = _serialize_json_field(value)
        elif key == "regions_map":
            processed_fields[key] = _serialize_json_list_or_str(value)
        else:
            processed_fields[key] = value

    _ensure_db(db_path)
    conn = _get_conn(db_path)
    try:
        if processed_fields:
            set_clause = ", ".join(f"{col} = ?" for col in processed_fields)
            values = list(processed_fields.values())
            values.append(client_id)
            cur = conn.execute(
                f"""UPDATE clients
                    SET {set_clause}, updated_at = datetime('now')
                    WHERE client_id = ?""",
                values,
            )
        else:
            # Если полей нет — всё равно обновляем updated_at
            cur = conn.execute(
                """UPDATE clients
                    SET updated_at = datetime('now')
                    WHERE client_id = ?""",
                (client_id,),
            )

        if cur.rowcount == 0:
            raise ValueError(f"client_id '{client_id}' not found")

        conn.commit()
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
            "label_mode": "auto",
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
            "label_mode": "auto",
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
