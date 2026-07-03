#!/usr/bin/env python3
"""
Миграция БД с устаревшей схемы results на новую схему clients/positions/labels.

Запускать вручную, явно указывая путь к БД:
    python migrate.py --db serplux.db

Скрипт:
  1. Делает бэкап <db>.bak.YYYY-MM-DD
  2. Создаёт таблицы clients, positions, labels
  3. Добавляет клиента 'default'
  4. Переносит данные из results в positions (client_id='default')
  5. Переносит не-NULL метки в labels (version=1, mode='snippets')
  6. Верифицирует COUNT(results) == COUNT(positions)
  7. DROP TABLE results только при успешной верификации

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
    """Создаёт таблицы clients, positions, labels и индексы."""
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
            label_mode     TEXT NOT NULL CHECK(label_mode IN ('domains','snippets','full')),
            label_version  INTEGER NOT NULL,
            sentiment      TEXT CHECK(sentiment IN ('positive','negative','neutral')),
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
    """Дополняет схему: поле confidence в labels и справочник domain_labels."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(labels)").fetchall()}
    if "confidence" not in cols:
        conn.execute("""
            ALTER TABLE labels
            ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high'
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS domain_labels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
            domain      TEXT NOT NULL,
            sentiment   TEXT CHECK(sentiment IN ('positive','negative','neutral')),
            source      TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','llm')),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(client_id, domain)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domlbl_client_domain ON domain_labels(client_id, domain)")


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


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"БД не найдена: {db_path}")

    conn = _get_conn(db_path)
    try:
        # Шаг 0: бэкап
        _backup_db(db_path)

        # Если results нет — миграция не требуется
        if not _table_exists(conn, "results"):
            log.info("Таблица results не найдена в %s — миграция не требуется", db_path)
            return

        log.info("Начинаю миграцию %s", db_path)

        # Создаём новые таблицы
        _create_new_schema(conn)
        _apply_schema_patches(conn)

        # Авто-клиент default
        conn.execute(
            "INSERT OR IGNORE INTO clients (client_id, client_name) VALUES ('default', 'Default')"
        )

        # Переносим данные
        positions_count_before = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        _migrate_results_to_positions(conn)
        positions_count_after = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        migrated_positions = positions_count_after - positions_count_before

        _migrate_labels(conn)
        labels_count = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]

        # Верификация
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
        conn.commit()

        log.info(
            "Миграция завершена успешно: перенесено позиций=%s, меток=%s, results удалена",
            migrated_positions, labels_count,
        )
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
