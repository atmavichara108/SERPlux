"""
Тесты идемпотентности migrate.py для схемы domain_labels + confidence + профиля клиента.

Покрывают три поддерживаемых состояния БД:
  1. БД после 1-й миграции (есть positions/labels без confidence, нет domain_labels)
  2. Полностью мигрированная БД
  3. БД с legacy results → перенос + доведение схемы + seed профиля
"""

import json
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


@pytest.fixture
def empty_db(db_path):
    """Создаёт пустой файл БД для migrate()."""
    sqlite3.connect(db_path).close()
    return db_path


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
        # существующие данные не потеряны и перенесены на 28938353
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
        assert conn.execute(
            "SELECT client_id FROM positions"
        ).fetchone()[0] == "28938353"
        # default удалён, 28938353 создан
        assert conn.execute(
            "SELECT 1 FROM clients WHERE client_id='default'"
        ).fetchone() is None
        client = conn.execute(
            "SELECT client_name FROM clients WHERE client_id='28938353'"
        ).fetchone()
        assert client is not None
        assert client[0] == "Sudheimer Group"
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


# ─── 4. Seed профиля клиента 28938353 из конфигурации репозитория ─────────────


def test_migrate_seeds_client_profile(empty_db, monkeypatch):
    db_path = empty_db
    """migrate() засевает 28938353 из config.py/regions_map_client1.json/env."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")

    migrate.migrate(db_path)

    client = storage.get_client("28938353", db_path)
    assert client is not None
    assert client["client_id"] == "28938353"
    assert client["client_name"] == "Sudheimer Group"
    assert client["project_id"] == 28938353

    # queries: 4 субъекта (key+display, без pos/url)
    assert len(client["queries"]) == 4
    assert all("key" in q and "display" in q for q in client["queries"])
    assert set(q["key"] for q in client["queries"]) == {
        "juri sudheimer", "erik sudheimer", "sct chemicals", "chempioil"
    }

    # regions_map: массив из regions_map_client1.json
    assert isinstance(client["regions_map"], list)
    assert len(client["regions_map"]) == 15
    assert all("searcher" in r and "geo_name" in r for r in client["regions_map"])

    # searchers: уникальные из regions_map
    assert sorted(client["searchers"]) == ["google", "yandex_com", "yandex_ru"]


def test_migrate_seeds_client_profile_idempotent(empty_db, monkeypatch):
    db_path = empty_db
    """Двойной migrate не создаёт дублей 28938353 и не падает."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")
    migrate.migrate(db_path)
    migrate.migrate(db_path)

    clients = storage.list_clients(db_path)
    target = [c for c in clients if c["client_id"] == "28938353"]
    assert len(target) == 1
    assert all(c["client_id"] != "default" for c in clients)


def test_migrate_updates_existing_empty_profile(db_path, monkeypatch):
    """Если 28938353 уже есть с пустым профилем — дозаполняем, не плодим дубликат."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE clients (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                project_id INTEGER,
                sheet_id TEXT,
                searchers TEXT,
                geos TEXT,
                regions_map TEXT,
                queries TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO clients (client_id, client_name) VALUES (?, ?)",
            ("28938353", "Old Name"),
        )
        conn.commit()
    finally:
        conn.close()

    migrate.migrate(db_path)

    client = storage.get_client("28938353", db_path)
    assert client is not None
    assert client["client_name"] == "Sudheimer Group"
    assert client["project_id"] == 28938353
    assert len(client["queries"]) == 4
    assert len(client["regions_map"]) == 15


def test_migrate_transfers_default_data_to_client(db_path, monkeypatch):
    """Данные с default переносятся на 28938353, default удалён."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE clients (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                project_id INTEGER,
                sheet_id TEXT,
                searchers TEXT,
                geos TEXT,
                regions_map TEXT,
                queries TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL REFERENCES clients(client_id),
                date TEXT NOT NULL,
                searcher TEXT NOT NULL,
                query TEXT NOT NULL,
                geo TEXT NOT NULL,
                region_index INTEGER NOT NULL,
                position INTEGER NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                snippet TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                client_id TEXT NOT NULL REFERENCES clients(client_id),
                label_mode TEXT NOT NULL,
                label_version INTEGER NOT NULL,
                sentiment TEXT,
                confidence TEXT DEFAULT 'high'
            )
        """)
        conn.executemany(
            "INSERT INTO clients (client_id, client_name) VALUES (?, ?)",
            [("default", "Default"), ("28938353", "28938353")],
        )
        # Разные url/position, чтобы не было дедупликации при переносе
        conn.execute(
            "INSERT INTO positions (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet) "
            "VALUES ('default', '2026-07-01', 'google', 'q', 'Литва', 1300, 1, 'https://a.com', 'a.com', '')"
        )
        conn.execute(
            "INSERT INTO positions (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet) "
            "VALUES ('28938353', '2026-07-01', 'google', 'q', 'Литва', 1300, 2, 'https://b.com', 'b.com', '')"
        )
        conn.executemany(
            "INSERT INTO labels (position_id, client_id, label_mode, label_version, sentiment) VALUES (?, ?, 'snippets', 1, 'positive')",
            [(1, "default"), (2, "28938353")],
        )
        conn.commit()
    finally:
        conn.close()

    migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert storage.get_client("default", db_path) is None
        assert storage.get_client("28938353", db_path) is not None

        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM positions WHERE client_id = '28938353'"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM positions WHERE client_id = 'default'"
        ).fetchone()[0] == 0

        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM labels WHERE client_id = '28938353'"
        ).fetchone()[0] == 2

        # Каскадного удаления не было — таблицы positions/labels не пусты
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
    finally:
        conn.close()


def test_migrate_seed_handles_duplicates_default_and_target(db_path, monkeypatch):
    """Дубликаты positions между default и 28938353 разрешаются без падения."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE clients (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                project_id INTEGER,
                sheet_id TEXT,
                searchers TEXT,
                geos TEXT,
                regions_map TEXT,
                queries TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL REFERENCES clients(client_id),
                date TEXT NOT NULL,
                searcher TEXT NOT NULL,
                query TEXT NOT NULL,
                geo TEXT NOT NULL,
                region_index INTEGER NOT NULL,
                position INTEGER NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                snippet TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.executemany(
            "INSERT INTO clients (client_id, client_name) VALUES (?, ?)",
            [("default", "Default"), ("28938353", "28938353")],
        )
        # Одинаковая строка у обоих — дубликат по UNIQUE(client_id,date,...)
        conn.executemany(
            "INSERT INTO positions (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet) "
            "VALUES (?, '2026-07-01', 'google', 'q', 'Литва', 1300, 1, 'https://a.com', 'a.com', '')",
            [("default",), ("28938353",)],
        )
        conn.commit()
    finally:
        conn.close()

    migrate.migrate(db_path)

    client = storage.get_client("28938353", db_path)
    assert client is not None
    assert storage.get_client("default", db_path) is None

    conn = sqlite3.connect(db_path)
    try:
        # Одна строка сохранилась, дубликат удалён
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
        assert conn.execute(
            "SELECT client_id FROM positions"
        ).fetchone()[0] == "28938353"
    finally:
        conn.close()


def test_migrate_seed_makes_preseed_backup(empty_db, monkeypatch):
    db_path = empty_db
    """Перед seed создаётся дополнительный preseed-бэкап."""
    monkeypatch.setenv("TOPVISOR_PROJECT_ID", "28938353")
    migrate.migrate(db_path)

    import glob as _glob
    backups = _glob.glob(f"{db_path}.preseed.*")
    assert len(backups) >= 1, "preseed-бэкап не создан"


# ─── 5. Обратная совместимость: migrate без env project_id ────────────────────


def test_migrate_seed_without_env_project_id_still_creates_client(empty_db, monkeypatch):
    db_path = empty_db
    """Если TOPVISOR_PROJECT_ID не задан, 28938353 создаётся с project_id=None."""
    monkeypatch.delenv("TOPVISOR_PROJECT_ID", raising=False)
    migrate.migrate(db_path)

    client = storage.get_client("28938353", db_path)
    assert client is not None
    assert client["project_id"] is None
    assert len(client["queries"]) == 4
    assert len(client["regions_map"]) == 15
