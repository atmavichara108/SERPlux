"""
Тесты идемпотентности migrate.py для схемы domain_labels + confidence.

Покрывают три поддерживаемых состояния БД:
  1. БД после 1-й миграции (есть positions/labels без confidence, нет domain_labels)
  2. Полностью мигрированная БД
  3. БД с legacy results → перенос + доведение схемы
"""

import os
import sqlite3
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import migrate
import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_migrate.db")


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ─── 1. Досоздание domain_labels + confidence на «частично мигрированной» БД ──


def test_migrate_adds_domain_labels_to_already_migrated_db(db_path):
    """
    БД после 1-й миграции: clients + positions + labels БЕЗ confidence,
    БЕЗ domain_labels, БЕЗ results. migrate() должна досоздать недостающее.
    """
    conn = sqlite3.connect(db_path)
    try:
        # DDL как в migrate._create_new_schema, но labels БЕЗ confidence
        conn.execute("""
            CREATE TABLE clients (
                client_id   TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                project_id  INTEGER,
                sheet_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE positions (
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
            CREATE TABLE labels (
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
        conn.execute(
            "INSERT INTO clients (client_id, client_name) VALUES ('default', 'Default')"
        )
        # вставим одну позицию с меткой, чтобы данные присутствовали
        conn.execute(
            "INSERT INTO positions (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet) "
            "VALUES ('default','2026-07-01','google','q','Литва',1300,1,'https://a.com','a.com','')"
        )
        conn.execute(
            "INSERT INTO labels (position_id, client_id, label_mode, label_version, sentiment) "
            "VALUES (1,'default','snippets',1,'positive')"
        )
        conn.commit()
    finally:
        conn.close()

    migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert _table_exists(conn, "domain_labels"), "domain_labels должна быть создана"
        assert _table_exists(conn, "results") is False, "results не должна появиться"
        assert "confidence" in _columns(conn, "labels"), "labels.confidence должна быть"
        # существующие данные не потеряны
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
        # клиент default на месте
        assert conn.execute(
            "SELECT client_name FROM clients WHERE client_id='default'"
        ).fetchone()[0] == "Default"
    finally:
        conn.close()


# ─── 2. Идемпотентность на полностью мигрированной БД ─────────────────────────


def test_migrate_idempotent_on_fully_migrated_db(db_path):
    """Двойной запуск migrate на полной схеме (storage._init_db) не ломает БД."""
    storage._init_db(db_path)

    migrate.migrate(db_path)
    migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert _table_exists(conn, "results") is False
        assert _table_exists(conn, "domain_labels")
        assert "confidence" in _columns(conn, "labels")
        # таблица domain_labels — ровно одна (не дублируется при повторе)
        domlbl_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='domain_labels'"
        ).fetchone()[0]
        assert domlbl_count == 1
        # колонка confidence в labels — ровно одна (ALTER не повторяется)
        confidence_cols = conn.execute(
            "PRAGMA table_info(labels)"
        ).fetchall()
        assert sum(1 for c in confidence_cols if c[1] == "confidence") == 1
        # позиции/метки пусты (могрим ничего не вставляли)
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0
    finally:
        conn.close()


# ─── 3. Legacy results → перенос + доведение схемы ─────────────────────────────


def test_migrate_with_results_transfers_and_applies_schema(db_path):
    """БД с legacy results мигрируется: перенос + domain_labels + confidence."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE results (
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
        conn.executemany(
            "INSERT INTO results (date, searcher, query, geo, region_index, position, url, domain, snippet, label) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-07-01", "google", "subject A", "Литва", 1300, 1,
                 "https://a.com", "a.com", "snip1", "positive"),
                ("2026-07-01", "google", "subject A", "Литва", 1300, 2,
                 "https://b.com", "b.com", "snip2", None),
                ("2026-07-01", "yandex_ru", "subject B", "Германия", 1018, 1,
                 "https://c.org", "c.org", "snip3", "negative"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert _table_exists(conn, "results") is False, "results должна быть удалена"
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 2  # две не-NULL метки
        assert _table_exists(conn, "domain_labels"), "domain_labels должна быть создана"
        assert "confidence" in _columns(conn, "labels"), "labels.confidence должна быть"
    finally:
        conn.close()