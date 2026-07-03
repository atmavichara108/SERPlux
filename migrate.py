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
  6. _verify_schema(conn)            — всегда, в конце

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
    """Дополняет схему старых БД: колонка confidence в labels + справочник domain_labels.

    Идемпотентно:
      - ALTER TABLE labels ADD COLUMN confidence — только если колонки нет
        (проверка через PRAGMA table_info(labels));
      - CREATE TABLE IF NOT EXISTS domain_labels + индекс idx_domlbl_client_domain.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(labels)").fetchall()}
    if "confidence" not in cols:
        conn.execute("""
            ALTER TABLE labels
            ADD COLUMN confidence TEXT CHECK(confidence IN ('high','uncertain')) DEFAULT 'high'
        """)
        log.info("Колонка labels.confidence добавлена (ALTER TABLE)")

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

        conn.commit()

        # Шаг 5: верификация схемы (всегда, в конце)
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