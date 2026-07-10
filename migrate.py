#!/usr/bin/env python3
"""
Миграция БД SERPlux на схему clients/positions/labels/domain_labels.

Запускать вручную, явно указывая путь к БД:
    python migrate.py --db serplux.db

Скрипт идемпотентен и поддерживает три стартовых состояния БД:

  1. Чистая БД (нет results, нет positions/labels/domain_labels):
     создать полную схему, авто-клиент 'default', данных не переносить.

  2. БД после 1-й миграции (есть positions/labels, но labels без колонки
     confidence, и нет таблицы domain_labels): досоздать недостающее
     (ALTER labels + CREATE domain_labels + индекс), клиента 'default'
     оставить как есть.

  3. Полностью мигрированная БД (positions/labels/domain_labels, confidence
     в labels присутствует, results уже удалена): ничего не менять,
     отчитаться «актуально».

Поток migrate(db_path):
   1. backup (всегда)
   2. _create_new_schema(conn)        — все таблицы IF NOT EXISTS
   3. _apply_schema_patches(conn)     — confidence + domain_labels
   4. авто-клиент 'default'           — INSERT OR IGNORE
   5. if _table_exists(conn, "results"):
          перенос results→positions
          перенос labels
          верификация COUNT(results)==COUNT(positions)
          DROP results
      else:
          log «перенос данных не требуется»
   6. seed/обновление клиента 28938353 + перенос default на 28938353
   7. нормализация client_id (28938353 → client01)
   8. _verify_schema(conn)            — всегда, в конце

НЕ запускает миграцию автоматически и НЕ трогает боевую БД без явного --db.
"""

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import storage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _backup_db(db_path: str) -> str:
    suffix = datetime.now().strftime("%Y-%m-%d")
    backup_path = f"{db_path}.bak.{suffix}"
    if os.path.exists(backup_path):
        # Если бэкап за сегодня уже есть — добавляем счётчик
        base = backup_path
        counter = 1
        while os.path.exists(backup_path):
            backup_path = f"{base}.{counter}"
            counter += 1
    shutil.copy2(db_path, backup_path)
    log.info("Бэкап создан: %s", backup_path)
    return backup_path


def _create_new_schema(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы clients, positions, labels (с confidence), индексы.

    Свежая БД сразу получает колонку confidence в labels — как в storage._init_db.
    Старые БД с labels без confidence патчатся в _apply_schema_patches.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id   TEXT PRIMARY KEY,
            client_name TEXT NOT NULL,
            project_id  INTEGER,
            sheet_id    TEXT,
            searchers   TEXT,                      -- JSON список
            geos        TEXT,                      -- JSON список
            regions_map TEXT,                      -- JSON массив регионов или имя файла (legacy)
            queries     TEXT,                      -- JSON массив субъектов [{key, display}]
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_date ON positions(client_id, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_url_query   ON positions(url, query)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_client_url  ON positions(client_id, url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_position    ON labels(position_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_client_mode ON labels(client_id, label_mode)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_latest      ON labels(position_id, label_mode, label_version DESC)")


def _apply_schema_patches(conn: sqlite3.Connection) -> None:
    """Дополняет схему старых БД: колонка confidence в labels + справочник domain_labels + поля профиля клиента.

    Идемпотентно:
      - ALTER TABLE labels ADD COLUMN confidence — только если колонки нет
        (проверка через PRAGMA table_info(labels));
      - ALTER TABLE clients ADD COLUMN searchers/geos/regions_map/queries — только если нет;
      - CREATE TABLE IF NOT EXISTS domain_labels с актуальной схемой (domain, query, geo);
        если существует старая схема (id/client_id) — пересоздаёт таблицу.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(labels)").fetchall()}
    if "confidence" not in cols:
        conn.execute("""
            ALTER TABLE labels
            ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high'
        """)
        log.info("Колонка labels.confidence добавлена (ALTER TABLE)")

    client_cols = {row[1] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
    for col, dtype in [
        ("searchers", "TEXT"),
        ("geos", "TEXT"),
        ("regions_map", "TEXT"),
        ("queries", "TEXT"),
    ]:
        if col not in client_cols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {dtype}")
            log.info("Колонка clients.%s добавлена (ALTER TABLE)", col)

    # Справочник domain_labels с актуальной схемой (domain, query, geo)
    old_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(domain_labels)").fetchall()
    }
    if old_cols and ("client_id" in old_cols or "id" in old_cols):
        log.warning("domain_labels: обнаружена старая схема (id/client_id), пересоздаю")
        conn.execute("DROP TABLE IF EXISTS domain_labels")
        conn.execute("DROP INDEX IF EXISTS idx_domlbl_client_domain")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_labels (
            domain      TEXT NOT NULL,
            query       TEXT NOT NULL,
            geo         TEXT NOT NULL,
            sentiment   TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral')),
            source      TEXT NOT NULL CHECK(source IN ('manual_l1','snippet','page')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (domain, query, geo)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domlbl_domain_query ON domain_labels(domain, query)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domlbl_geo ON domain_labels(geo)")

    # Персистентный статус прогона (singleton, id=1)
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
    conn.execute("INSERT OR IGNORE INTO run_status (id, status) VALUES (1, 'idle')")

    # Обновление CHECK constraint labels для режимов auto/deep
    create_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='labels'"
    ).fetchone()
    if create_sql_row and "'auto'" not in create_sql_row[0]:
        log.warning("labels: обнаружен устаревший CHECK(label_mode), пересоздаю таблицу")
        conn.execute("ALTER TABLE labels RENAME TO labels_old")
        conn.execute("""
            CREATE TABLE labels (
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
        # Копируем только те колонки, которые реально есть в labels_old
        old_cols = {row[1] for row in conn.execute("PRAGMA table_info(labels_old)").fetchall()}
        new_cols = ["position_id", "client_id", "label_mode", "label_version", "sentiment"]
        if "confidence" in old_cols:
            new_cols.append("confidence")
        if "created_at" in old_cols:
            new_cols.append("created_at")
        if "id" in old_cols:
            new_cols.insert(0, "id")
        src_cols = ", ".join(new_cols)
        conn.execute(f"""
            INSERT INTO labels ({src_cols})
            SELECT {src_cols} FROM labels_old
        """)
        conn.execute("DROP TABLE labels_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_position ON labels(position_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_client_mode ON labels(client_id, label_mode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lbl_latest ON labels(position_id, label_mode, label_version DESC)")


def _migrate_results_to_positions(conn: sqlite3.Connection) -> int:
    """Переносит строки из results в positions. Возвращает количество перенесённых."""
    conn.execute("""
        INSERT INTO positions
            (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet)
        SELECT
            'default', date, searcher, query, geo, region_index, position, url, domain, snippet
        FROM results
    """)
    return conn.total_changes


def _migrate_labels(conn: sqlite3.Connection) -> int:
    """Переносит не-NULL метки из results в labels (version=1, mode='snippets')."""
    conn.execute("""
        INSERT INTO labels
            (position_id, client_id, label_mode, label_version, sentiment)
        SELECT
            p.id, 'default', 'snippets', 1, r.label
        FROM results r
        JOIN positions p ON p.client_id = 'default'
                        AND p.date = r.date
                        AND p.searcher = r.searcher
                        AND p.query = r.query
                        AND p.geo = r.geo
                        AND p.position = r.position
                        AND p.url = r.url
        WHERE r.label IS NOT NULL
    """)
    return conn.total_changes


def _verify_schema(conn: sqlite3.Connection) -> None:
    """В конце миграции проверяет, что схема доведена до актуального состояния.

    Логирует список таблиц и колонки labels, затем проверяет наличие
    таблицы domain_labels и колонки confidence в labels. При отсутствии —
    RuntimeError.
    """
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    log.info("Таблицы в БД: %s", ", ".join(tables) or "<нет>")

    label_cols = [row[1] for row in conn.execute("PRAGMA table_info(labels)").fetchall()]
    log.info("Колонки labels: %s", ", ".join(label_cols))

    if not _table_exists(conn, "domain_labels"):
        raise RuntimeError("Схема не доведена: таблица domain_labels отсутствует")
    if "confidence" not in label_cols:
        raise RuntimeError("Схема не доведена: колонка labels.confidence отсутствует")
    if not _table_exists(conn, "run_status"):
        raise RuntimeError("Схема не доведена: таблица run_status отсутствует")

    create_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='labels'"
    ).fetchone()
    if create_sql_row and "'auto'" not in create_sql_row[0]:
        raise RuntimeError("Схема не доведена: labels.label_mode не поддерживает auto/deep")

    client_cols = {row[1] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
    for col in ("searchers", "geos", "regions_map", "queries"):
        if col not in client_cols:
            raise RuntimeError(f"Схема не доведена: колонка clients.{col} отсутствует")


def _backup_preseed(db_path: str) -> str:
    """Дополнительный бэкап непосредственно перед seed (операция переноса client_id)."""
    suffix = datetime.now().strftime("%Y-%m-%d")
    backup_path = f"{db_path}.preseed.{suffix}"
    if os.path.exists(backup_path):
        base = backup_path
        counter = 1
        while os.path.exists(backup_path):
            backup_path = f"{base}.{counter}"
            counter += 1
    shutil.copy2(db_path, backup_path)
    log.info("Preseed-бэкап создан: %s", backup_path)
    return backup_path


def _log_client_stats(conn: sqlite3.Connection, prefix: str) -> None:
    """Логирует количество positions/labels/domain_labels по client_id (GROUP BY)."""
    pos_stats = conn.execute(
        "SELECT client_id, COUNT(*) FROM positions GROUP BY client_id ORDER BY client_id"
    ).fetchall()
    lbl_stats = conn.execute(
        "SELECT client_id, COUNT(*) FROM labels GROUP BY client_id ORDER BY client_id"
    ).fetchall()
    dom_total = conn.execute("SELECT COUNT(*) FROM domain_labels").fetchone()[0]
    log.info("%s positions по client_id: %s", prefix, pos_stats)
    log.info("%s labels по client_id: %s", prefix, lbl_stats)
    log.info("%s domain_labels всего: %s", prefix, dom_total)


def _resolve_position_duplicates(conn: sqlite3.Connection, musor_id: str, target: str) -> int:
    """Удаляет positions у musor_id, дублирующиеся по UNIQUE-ключу с target."""
    dups = conn.execute(
        """SELECT p1.id FROM positions p1
           JOIN positions p2 ON p2.client_id = ?
             AND p2.date = p1.date AND p2.searcher = p1.searcher AND p2.query = p1.query
             AND p2.geo = p1.geo AND p2.position = p1.position AND p2.url = p1.url
           WHERE p1.client_id = ?""",
        (target, musor_id),
    ).fetchall()
    dup_ids = [r[0] for r in dups]
    if dup_ids:
        placeholders = ",".join("?" * len(dup_ids))
        conn.execute(f"DELETE FROM positions WHERE id IN ({placeholders})", dup_ids)
        log.info("Удалено дубликатов positions у %s: %s", musor_id, len(dup_ids))
    return len(dup_ids)


def _resolve_label_duplicates(conn: sqlite3.Connection, musor_id: str, target: str) -> int:
    """Удаляет labels у musor_id, дублирующиеся по UNIQUE-ключу с target."""
    dups = conn.execute(
        """SELECT l1.id FROM labels l1
           JOIN labels l2 ON l2.client_id = ? AND l2.position_id = l1.position_id
             AND l2.label_mode = l1.label_mode AND l2.label_version = l1.label_version
           WHERE l1.client_id = ?""",
        (target, musor_id),
    ).fetchall()
    dup_ids = [r[0] for r in dups]
    if dup_ids:
        placeholders = ",".join("?" * len(dup_ids))
        conn.execute(f"DELETE FROM labels WHERE id IN ({placeholders})", dup_ids)
        log.info("Удалено дубликатов labels у %s: %s", musor_id, len(dup_ids))
    return len(dup_ids)


def _seed_client_profile(conn: sqlite3.Connection, db_path: str) -> None:
    """
    Разовый seed/обновление профиля клиента 28938353 из конфигурации репозитория.

    - client_id   = "28938353" (совпадает с project_id в Topvisor)
    - client_name = "Sudheimer Group"
    - project_id  = int(env TOPVISOR_PROJECT_ID)
    - queries     из config._DEPRECATED_SUBJECT_BLOCKS (key + display, без pos/url)
    - regions_map из regions_map_client1.json (полный массив)
    - searchers   уникальные searcher из regions_map_client1.json

    Если клиент 28938353 уже существует — обновляет профиль (не плодит дубликат).
    Переносит боевые данные с мусорного client_id 'default' на '28938353' через UPDATE.
    Каскадного удаления не допускается: перед DELETE 'default' проверяется, что у него
    0 дочерних записей.
    """
    import json as _json

    target = "28938353"
    client_name = "Sudheimer Group"
    musor_id = "default"

    _log_client_stats(conn, "До seed")

    # Дополнительный бэкап перед операцией переноса client_id
    _backup_preseed(db_path)

    # project_id из env (не хардкод)
    project_id = None
    env_project = os.environ.get("TOPVISOR_PROJECT_ID")
    if env_project:
        try:
            project_id = int(env_project)
        except ValueError:
            log.warning("TOPVISOR_PROJECT_ID не число: %s", env_project)

    # queries из config._DEPRECATED_SUBJECT_BLOCKS (key + display, без pos/url)
    import config

    queries = [{"key": sb["key"], "display": sb["display"]} for sb in config._DEPRECATED_SUBJECT_BLOCKS]

    # regions_map из файла (не хардкод)
    regions_map_path = os.path.join(os.path.dirname(__file__), "regions_map_client1.json")
    with open(regions_map_path, "r", encoding="utf-8") as f:
        regions_map = _json.load(f)

    # searchers — уникальные searcher из карты регионов
    searchers = sorted({r["searcher"] for r in regions_map})

    # Создаём или обновляем клиента
    cur = conn.execute("SELECT 1 FROM clients WHERE client_id = ?", (target,))
    if cur.fetchone() is None:
        conn.execute(
            """INSERT INTO clients
               (client_id, client_name, project_id, searchers, geos, regions_map, queries, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                target,
                client_name,
                project_id,
                _json.dumps(searchers, ensure_ascii=False),
                None,
                _json.dumps(regions_map, ensure_ascii=False),
                _json.dumps(queries, ensure_ascii=False),
            ),
        )
        log.info(
            "Создан клиент %s (%s): project_id=%s, searchers=%s, queries=%s, regions_map=%s записей",
            target, client_name, project_id, searchers, len(queries), len(regions_map),
        )
    else:
        conn.execute(
            """UPDATE clients
               SET client_name = ?, project_id = ?, searchers = ?, geos = ?,
                   regions_map = ?, queries = ?, updated_at = datetime('now')
               WHERE client_id = ?""",
            (
                client_name,
                project_id,
                _json.dumps(searchers, ensure_ascii=False),
                None,
                _json.dumps(regions_map, ensure_ascii=False),
                _json.dumps(queries, ensure_ascii=False),
                target,
            ),
        )
        log.info(
            "Обновлён профиль клиента %s (%s): project_id=%s, searchers=%s, queries=%s, regions_map=%s записей",
            target, client_name, project_id, searchers, len(queries), len(regions_map),
        )

    # Перенос positions с default на target (предварительно удаляем дубликаты)
    count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE client_id = ?", (musor_id,)
    ).fetchone()[0]
    if count:
        log.info("Перенос positions с %s на %s: %s строк", musor_id, target, count)
        _resolve_position_duplicates(conn, musor_id, target)
        conn.execute("UPDATE positions SET client_id = ? WHERE client_id = ?", (target, musor_id))

    # Перенос labels с default на target (предварительно удаляем дубликаты)
    count = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE client_id = ?", (musor_id,)
    ).fetchone()[0]
    if count:
        log.info("Перенос labels с %s на %s: %s строк", musor_id, target, count)
        _resolve_label_duplicates(conn, musor_id, target)
        conn.execute("UPDATE labels SET client_id = ? WHERE client_id = ?", (target, musor_id))

    # Верификация: у default не осталось данных. Каскадное удаление запрещено.
    # domain_labels больше не привязана к client_id, поэтому проверяем только positions/labels.
    for table in ("positions", "labels"):
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE client_id = ?", (musor_id,)
        ).fetchone()[0]
        if cnt:
            raise RuntimeError(
                f"Остались данные у {musor_id} в {table}: {cnt}. "
                "Удаление мусорного клиента отменено — каскадное удаление запрещено."
            )

    # Удаление мусорного клиента default (теперь безопасно — у него нет дочерних записей)
    conn.execute("DELETE FROM clients WHERE client_id = ?", (musor_id,))
    log.info("Удалён мусорный клиент: %s", musor_id)

    _log_client_stats(conn, "После seed")


def _normalize_client_id(conn: sqlite3.Connection) -> None:
    """
    Нормализация идентификатора клиента: переносит данные с численного 
    client_id="28938353" на строковый slug client_id="client01".
    
    Логика:
    1. Если нет "28938353" в clients — ничего не делать (уже нормализовано).
    2. Если "28938353" есть:
       a) Если "client01" не существует:
          - Создать "client01" со ВСЕМ профилем из "28938353"
       b) Если "client01" уже существует:
          - Merge: перенос только данных с "28938353" на "client01"
       c) UPDATE positions/labels/domain_labels: "28938353" → "client01"
       d) DELETE "28938353" из clients
       e) Верификация целостности (нет осиротевших)
    """
    source = "28938353"
    target = "client01"
    
    # Проверяем наличие source
    source_row = conn.execute(
        "SELECT client_name, project_id, searchers, geos, regions_map, queries "
        "FROM clients WHERE client_id = ?",
        (source,)
    ).fetchone()
    
    if source_row is None:
        log.info("Нормализация client_id: %s не найден, ничего не делать", source)
        return
    
    client_name, project_id, searchers, geos, regions_map, queries = source_row
    
    # Проверяем наличие target
    target_exists = conn.execute(
        "SELECT 1 FROM clients WHERE client_id = ?", (target,)
    ).fetchone() is not None
    
    if not target_exists:
        # Создаём target с полным профилем из source
        conn.execute(
            """INSERT INTO clients
               (client_id, client_name, project_id, searchers, geos, regions_map, queries, 
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (target, client_name, project_id, searchers, geos, regions_map, queries),
        )
        log.info(
            "Создан нормализованный клиент %s из %s (profile: project_id=%s)",
            target, source, project_id,
        )
    else:
        log.info(
            "Нормализация client_id: %s уже существует, выполняю merge (перенос только данных)",
            target
        )
    
    # Перенос positions
    pos_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE client_id = ?", (source,)
    ).fetchone()[0]
    if pos_count:
        conn.execute(
            "UPDATE positions SET client_id = ? WHERE client_id = ?",
            (target, source)
        )
        log.info("Перенесено positions: %s → %s (%s записей)", source, target, pos_count)
    
    # Перенос labels
    label_count = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE client_id = ?", (source,)
    ).fetchone()[0]
    if label_count:
        conn.execute(
            "UPDATE labels SET client_id = ? WHERE client_id = ?",
            (target, source)
        )
        log.info("Перенесено labels: %s → %s (%s записей)", source, target, label_count)
    
    # Удаление source (теперь безопасно — все данные перенесены)
    conn.execute("DELETE FROM clients WHERE client_id = ?", (source,))
    log.info("Удалён старый идентификатор клиента: %s", source)
    
    # Верификация целостности: нет осиротевших записей
    for table in ("positions", "labels"):
        orphan_count = conn.execute(
            f"""SELECT COUNT(*) FROM {table} p
               LEFT JOIN clients c ON p.client_id = c.client_id
               WHERE c.client_id IS NULL AND p.client_id IS NOT NULL"""
        ).fetchone()[0]
        if orphan_count:
            raise RuntimeError(
                f"Нормализация client_id: осиротевшие записи в {table}: {orphan_count}. "
                "Откат."
            )
    
    log.info("Нормализация client_id завершена: %s → %s", source, target)
    _log_client_stats(conn, "После нормализации client_id")


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"БД не найдена: {db_path}")

    conn = _get_conn(db_path)
    try:
        # Шаг 0: бэкап (всегда)
        _backup_db(db_path)

        log.info("Начинаю миграцию %s", db_path)

        # Шаг 1: создаём полную схему (IF NOT EXISTS — безопасно для существующих)
        _create_new_schema(conn)

        # Шаг 2: патчи для старых БД (confidence + domain_labels)
        _apply_schema_patches(conn)

        # Шаг 3: авто-клиент default (всегда, идемпотентно)
        conn.execute(
            "INSERT OR IGNORE INTO clients (client_id, client_name) VALUES ('default', 'Default')"
        )

        # Шаг 4: перенос данных из results (только если legacy-таблица есть)
        if _table_exists(conn, "results"):
            positions_count_before = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            _migrate_results_to_positions(conn)
            positions_count_after = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            migrated_positions = positions_count_after - positions_count_before

            _migrate_labels(conn)
            labels_count = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]

            # Верификация: COUNT(results) == COUNT(positions) после переноса
            results_count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            final_positions_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

            log.info("Верификация: results=%s, positions=%s", results_count, final_positions_count)

            if results_count != final_positions_count:
                raise RuntimeError(
                    f"Верификация не пройдена: results={results_count}, positions={final_positions_count}. "
                    "Откат: таблица results НЕ удалена."
                )

            # DROP results только после успешной верификации
            conn.execute("DROP TABLE results")
            log.info(
                "Перенесено позиций=%s, меток=%s, results удалена",
                migrated_positions, labels_count,
            )
        else:
            log.info("Перенос данных не требуется (results отсутствует)")

        # Шаг 5: seed/обновление профиля клиента 28938353 + перенос мусорного default
        _seed_client_profile(conn, db_path)

        # Шаг 6: нормализация client_id (28938353 → client01)
        _normalize_client_id(conn)

        conn.commit()

        # Шаг 7: верификация схемы (всегда, в конце)
        _verify_schema(conn)

        log.info("Миграция завершена успешно")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Миграция БД SERPlux на схему clients/positions/labels",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Путь к SQLite-БД (например, serplux.db)",
    )
    args = parser.parse_args()

    try:
        migrate(args.db)
        return 0
    except Exception as e:
        log.error("Миграция прервана: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())