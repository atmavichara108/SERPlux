"""
T-001 — тесты новой схемы данных SERPlux (clients/positions/labels).

Все тесты используют изолированные временные БД, боевую serplux.db не трогают.
"""

import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import migrate
import storage


# ─── Фикстуры ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    """Возвращает путь к временной БД для теста."""
    return str(tmp_path / "test_schema.db")


@pytest.fixture
def init_db(db_path):
    """Инициализирует новую схему."""
    storage._init_db(db_path)
    return db_path


@pytest.fixture
def sample_rows():
    """Тестовые Row для новой схемы."""
    return [
        {
            "date": "2026-06-19",
            "searcher": "google",
            "query": "subject A",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://example.com/page1",
            "domain": "example.com",
            "snippet": "Snippet 1",
        },
        {
            "date": "2026-06-19",
            "searcher": "google",
            "query": "subject A",
            "geo": "Литва",
            "region_index": 1300,
            "position": 2,
            "url": "https://example.com/page2",
            "domain": "example.com",
            "snippet": "Snippet 2",
        },
        {
            "date": "2026-06-19",
            "searcher": "yandex_ru",
            "query": "subject B",
            "geo": "Германия",
            "region_index": 1018,
            "position": 1,
            "url": "https://test.org",
            "domain": "test.org",
            "snippet": "Snippet 3",
        },
    ]


# ─── Блок 1: Миграция без потери строк ────────────────────────────────────────


def _create_legacy_db(db_path: str, rows: list[dict]) -> None:
    """Создаёт БД со старой схемой results и заполняет её."""
    if os.path.exists(db_path):
        os.remove(db_path)
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
            """INSERT INTO results
               (date, searcher, query, geo, region_index, position, url, domain, snippet, label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["date"], r["searcher"], r["query"], r["geo"],
                    r["region_index"], r["position"], r["url"], r["domain"],
                    r.get("snippet", ""), r.get("label")
                )
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_preserves_row_count(db_path, sample_rows):
    """Миграция переносит все строки из results в positions без потерь."""
    legacy_rows = [
        {**r, "label": "positive" if i == 0 else None}
        for i, r in enumerate(sample_rows)
    ]
    _create_legacy_db(db_path, legacy_rows)

    migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        results_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='results'"
        ).fetchone()[0]
        assert results_count == 0, "Таблица results должна быть удалена после миграции"

        positions_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        assert positions_count == len(legacy_rows)

        labels_count = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        assert labels_count == 1  # только одна не-NULL метка

        # default удалён seed, данные перенесены на 28938353
        assert conn.execute(
            "SELECT 1 FROM clients WHERE client_id = 'default'"
        ).fetchone() is None
        client = conn.execute(
            "SELECT client_id, client_name FROM clients WHERE client_id = '28938353'"
        ).fetchone()
        assert client == ("28938353", "Sudheimer Group")
        assert conn.execute(
            "SELECT client_id FROM positions"
        ).fetchone()[0] == "28938353"
    finally:
        conn.close()


def test_migration_aborts_on_count_mismatch(db_path, sample_rows, monkeypatch):
    """Если COUNT не совпал — миграция прерывается, results остаётся."""
    _create_legacy_db(db_path, sample_rows)

    # Ломаем перенос: вставляем лишнюю строку в positions заранее,
    # чтобы counts не совпали (но на самом деле migrate сделает INSERT, поэтому
    # перенесёт всё равно. Другой способ — подменить _migrate_results_to_positions).
    original_migrate_positions = migrate._migrate_results_to_positions

    def broken_migrate(conn):
        # Переносим только первую строку
        conn.execute("""
            INSERT INTO positions
                (client_id, date, searcher, query, geo, region_index, position, url, domain, snippet)
            SELECT 'default', date, searcher, query, geo, region_index, position, url, domain, snippet
            FROM results LIMIT 1
        """)

    monkeypatch.setattr(migrate, "_migrate_results_to_positions", broken_migrate)

    with pytest.raises(RuntimeError):
        migrate.migrate(db_path)

    conn = sqlite3.connect(db_path)
    try:
        results_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'"
        ).fetchone()
        assert results_exists is not None, "results должна остаться при неудачной миграции"
    finally:
        conn.close()


# ─── Блок 2: Инкремент версий (включая гонку + retry) ─────────────────────────


def test_label_version_increments(init_db, sample_rows):
    """Повторная insert_labels создаёт новую версию для той же позиции."""
    storage.save(sample_rows, init_db)

    labeled = [{**sample_rows[0], "sentiment": "positive", "label_mode": "snippets"}]
    assert storage.insert_labels(labeled, init_db) == 1

    labeled2 = [{**sample_rows[0], "sentiment": "neutral", "label_mode": "snippets"}]
    assert storage.insert_labels(labeled2, init_db) == 1

    conn = sqlite3.connect(init_db)
    try:
        versions = conn.execute(
            "SELECT label_version, sentiment FROM labels WHERE position_id = 1 ORDER BY label_version"
        ).fetchall()
        assert versions == [(1, "positive"), (2, "neutral")]
    finally:
        conn.close()


def test_label_version_independent_per_mode(init_db, sample_rows):
    """Разные label_mode имеют независимые счётчики версий."""
    storage.save(sample_rows, init_db)

    row = {**sample_rows[0], "sentiment": "positive"}
    storage.insert_labels([{**row, "label_mode": "snippets"}], init_db)
    storage.insert_labels([{**row, "label_mode": "domains"}], init_db)
    storage.insert_labels([{**row, "label_mode": "snippets"}], init_db)

    conn = sqlite3.connect(init_db)
    try:
        rows = conn.execute(
            "SELECT label_mode, label_version FROM labels WHERE position_id = 1 ORDER BY label_mode, label_version"
        ).fetchall()
        assert rows == [("domains", 1), ("snippets", 1), ("snippets", 2)]
    finally:
        conn.close()


def test_insert_label_retry_on_race(init_db, sample_rows, monkeypatch):
    """При гонке за версией insert_labels делает retry и вставляет успешно."""
    storage.save(sample_rows, init_db)

    # Сначала вставляем version=1 вручную
    conn = sqlite3.connect(init_db)
    try:
        conn.execute(
            "INSERT INTO labels (position_id, client_id, label_mode, label_version, sentiment) VALUES (?, ?, ?, ?, ?)",
            (1, "default", "snippets", 1, "positive")
        )
        conn.commit()
    finally:
        conn.close()

    # Имитируем гонку: первые 2 попытки _next_label_version возвращают 1,
    # третья — 2. INSERT с version=1 даст UNIQUE violation → retry → version=2.
    call_count = {"n": 0}

    def fake_next_label_version(conn, position_id, label_mode):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return 1
        return 2

    monkeypatch.setattr(storage, "_next_label_version", fake_next_label_version)

    labeled = [{**sample_rows[0], "sentiment": "negative", "label_mode": "snippets"}]
    inserted = storage.insert_labels(labeled, init_db)

    assert inserted == 1
    assert call_count["n"] == 3

    conn = sqlite3.connect(init_db)
    try:
        versions = conn.execute(
            "SELECT label_version, sentiment FROM labels WHERE position_id = 1 ORDER BY label_version"
        ).fetchall()
        assert versions == [(1, "positive"), (2, "negative")]
    finally:
        conn.close()


# ─── Блок 3: get_history с фильтрами ──────────────────────────────────────────


def test_get_history_default_latest_label(init_db, sample_rows):
    """По умолчанию get_history возвращает последнюю метку на позицию."""
    storage.save(sample_rows, init_db)

    # Две версии для первой позиции
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive", "label_mode": "snippets"}], init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "neutral", "label_mode": "snippets"}], init_db)

    history = storage.get_history(db_path=init_db)
    by_url = {r["url"]: r for r in history}

    assert by_url["https://example.com/page1"]["sentiment"] == "neutral"
    assert by_url["https://example.com/page1"]["label"] == "neutral"
    assert by_url["https://example.com/page1"]["label_version"] == 2
    assert by_url["https://example.com/page2"]["sentiment"] is None
    assert by_url["https://test.org"]["sentiment"] is None


def test_get_history_all_versions(init_db, sample_rows):
    """label_version='all' возвращает все версии меток."""
    storage.save(sample_rows, init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive"}], init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "negative"}], init_db)

    history = storage.get_history({"label_version": "all"}, init_db)
    versions = [r for r in history if r["url"] == "https://example.com/page1"]
    assert len(versions) == 2
    assert {(r["sentiment"], r["label_version"]) for r in versions} == {("positive", 1), ("negative", 2)}


def test_get_history_client_filter(init_db, sample_rows):
    """Фильтр client_id ограничивает выборку по клиенту."""
    storage.save(sample_rows, init_db, client_id="client-a")
    storage.insert_labels(
        [{**sample_rows[0], "sentiment": "positive", "client_id": "client-a"}],
        init_db,
    )

    history_default = storage.get_history({"client_id": "default"}, init_db)
    history_a = storage.get_history({"client_id": "client-a"}, init_db)

    assert len(history_default) == 0
    assert len(history_a) == 3
    assert history_a[0]["client_id"] == "client-a"


def test_get_history_preserves_filters(init_db, sample_rows):
    """date/searcher/geo/query фильтры работают в новой схеме."""
    storage.save(sample_rows, init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive"}], init_db)

    filtered = storage.get_history({"query": "subject A", "searcher": "google"}, init_db)
    assert len(filtered) == 2
    assert all(r["query"] == "subject A" and r["searcher"] == "google" for r in filtered)


# ─── Блок 4: get_cached_label через JOIN ──────────────────────────────────────


def test_get_cached_label_joins_positions_and_labels(init_db, sample_rows):
    """get_cached_label ищет по (url, query) через JOIN positions+labels."""
    storage.save(sample_rows, init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive"}], init_db)

    cached = storage.get_cached_label("https://example.com/page1", "subject A", init_db)
    assert cached == "positive"


def test_get_cached_label_returns_latest_non_null(init_db, sample_rows):
    """get_cached_label возвращает последнюю не-NULL sentiment."""
    storage.save(sample_rows, init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive"}], init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": None}], init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "negative"}], init_db)

    cached = storage.get_cached_label("https://example.com/page1", "subject A", init_db)
    assert cached == "negative"


def test_get_cached_label_none_for_no_match(init_db, sample_rows):
    """get_cached_label возвращает None, если нет подходящей пары."""
    storage.save(sample_rows, init_db)
    storage.insert_labels([{**sample_rows[0], "sentiment": "positive"}], init_db)

    assert storage.get_cached_label("https://example.com/page1", "other query", init_db) is None
    assert storage.get_cached_label("https://unknown.com", "subject A", init_db) is None


# ─── Блок 5: insert_labels ────────────────────────────────────────────────────


def test_insert_labels_skips_none_sentiment(init_db, sample_rows):
    """Строки с sentiment=None не создают записей в labels."""
    storage.save(sample_rows, init_db)

    rows = [
        {**sample_rows[0], "sentiment": None},
        {**sample_rows[1], "sentiment": "neutral"},
    ]
    inserted = storage.insert_labels(rows, init_db)
    assert inserted == 1

    conn = sqlite3.connect(init_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
    finally:
        conn.close()


def test_insert_labels_requires_existing_position(init_db, sample_rows):
    """Метка для несуществующей позиции пропускается, не падает."""
    storage.save([sample_rows[0]], init_db)

    rows = [
        {**sample_rows[0], "sentiment": "positive"},
        {**sample_rows[1], "sentiment": "negative"},
    ]
    inserted = storage.insert_labels(rows, init_db)
    assert inserted == 1


def test_insert_labels_rejects_invalid_mode(init_db, sample_rows):
    """Неизвестный label_mode пропускается."""
    storage.save(sample_rows, init_db)

    rows = [{**sample_rows[0], "sentiment": "positive", "label_mode": "invalid"}]
    inserted = storage.insert_labels(rows, init_db)
    assert inserted == 0


def test_insert_labels_alias_label(init_db, sample_rows):
    """insert_labels понимает label как алиас sentiment."""
    storage.save(sample_rows, init_db)

    rows = [{**sample_rows[0], "label": "positive"}]
    assert storage.insert_labels(rows, init_db) == 1

    cached = storage.get_cached_label("https://example.com/page1", "subject A", init_db)
    assert cached == "positive"


# ─── Блок 6: Атомарность (retry 3 попытки) ────────────────────────────────────


def test_atomic_retry_gives_up_after_three_attempts(init_db, sample_rows, monkeypatch):
    """После 3 неудачных попыток insert_labels возвращает 0 и не падает."""
    storage.save(sample_rows, init_db)

    # Вставляем version=1
    conn = sqlite3.connect(init_db)
    try:
        conn.execute(
            "INSERT INTO labels (position_id, client_id, label_mode, label_version, sentiment) VALUES (?, ?, ?, ?, ?)",
            (1, "default", "snippets", 1, "positive")
        )
        conn.commit()
    finally:
        conn.close()

    # Всегда возвращаем занятую версию
    monkeypatch.setattr(storage, "_next_label_version", lambda conn, pid, mode: 1)

    labeled = [{**sample_rows[0], "sentiment": "negative", "label_mode": "snippets"}]
    inserted = storage.insert_labels(labeled, init_db)

    assert inserted == 0

    conn = sqlite3.connect(init_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
    finally:
        conn.close()


def test_concurrent_inserts_do_not_corrupt_db(init_db, sample_rows):
    """Конкурентные insert_labels не ломают БД (все метки валидны)."""
    storage.save(sample_rows, init_db)

    errors = []

    def worker(sentiment):
        try:
            rows = [{**sample_rows[0], "sentiment": sentiment, "label_mode": "snippets"}]
            storage.insert_labels(rows, init_db)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=("positive",)),
        threading.Thread(target=worker, args=("negative",)),
        threading.Thread(target=worker, args=("neutral",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Ошибки при конкурентной вставке: {errors}"

    conn = sqlite3.connect(init_db)
    try:
        versions = conn.execute(
            "SELECT label_version, sentiment FROM labels WHERE position_id = 1 ORDER BY label_version"
        ).fetchall()
        assert len(versions) == 3
        assert [v[0] for v in versions] == [1, 2, 3]
    finally:
        conn.close()


def test_schema_has_required_constraints(init_db):
    """В БД созданы все таблицы, FK, CHECK и индексы из ADR."""
    conn = sqlite3.connect(init_db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert tables >= {"clients", "positions", "labels"}

        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        required_indexes = {
            "idx_pos_client_date",
            "idx_pos_url_query",
            "idx_pos_client_url",
            "idx_lbl_position",
            "idx_lbl_client_mode",
            "idx_lbl_latest",
        }
        assert required_indexes <= indexes, f"Не хватает индексов: {required_indexes - indexes}"

        # FK включены в positions и labels
        for table in ("positions", "labels"):
            fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert fks, f"Нет внешних ключей в {table}"
    finally:
        conn.close()


def test_default_client_created_on_init(init_db):
    """При инициализации БД создаётся клиент 'default'."""
    conn = sqlite3.connect(init_db)
    try:
        row = conn.execute(
            "SELECT client_id, client_name FROM clients WHERE client_id = 'default'"
        ).fetchone()
        assert row == ("default", "Default")
    finally:
        conn.close()


# ─── Блок 7: управление клиентами ─────────────────────────────────────────────


class TestClientManagement:
    """Тесты CRUD-функций клиентов в storage.py."""

    def test_list_clients_returns_default(self, init_db):
        """list_clients возвращает только 'default' в пустой БД."""
        clients = storage.list_clients(init_db)
        assert clients == [{
            "client_id": "default",
            "client_name": "Default",
            "project_id": None,
            "sheet_id": None,
            "searchers": [],
            "geos": [],
            "regions_map": [],
            "queries": [],
        }]

    def test_create_and_list_clients(self, init_db):
        """create_client добавляет клиента; list_clients возвращает его."""
        storage.create_client("acme", "Acme Corp", project_id=123, sheet_id="abc", db_path=init_db)
        clients = storage.list_clients(init_db)
        by_id = {c["client_id"]: c for c in clients}

        assert "acme" in by_id
        assert by_id["acme"] == {
            "client_id": "acme",
            "client_name": "Acme Corp",
            "project_id": 123,
            "sheet_id": "abc",
            "searchers": [],
            "geos": [],
            "regions_map": [],
            "queries": [],
        }
        assert "default" in by_id

    def test_create_client_with_searchers_geos_regions_map(self, init_db):
        """create_client сериализует searchers/geos и сохраняет regions_map."""
        storage.create_client(
            "full",
            "Full Client",
            project_id=456,
            sheet_id="sh",
            searchers=["google", "yandex_ru"],
            geos=["Литва", "Германия"],
            regions_map="regions_map_full.json",
            db_path=init_db,
        )
        client = storage.get_client("full", init_db)
        assert client == {
            "client_id": "full",
            "client_name": "Full Client",
            "project_id": 456,
            "sheet_id": "sh",
            "searchers": ["google", "yandex_ru"],
            "geos": ["Литва", "Германия"],
            "regions_map": "regions_map_full.json",
            "queries": [],
        }

    def test_create_client_optional_fields(self, init_db):
        """project_id и sheet_id необязательны и по умолчанию None."""
        storage.create_client("minimal", "Minimal Client", db_path=init_db)
        client = storage.get_client("minimal", init_db)
        assert client is not None
        assert client["project_id"] is None
        assert client["sheet_id"] is None

    def test_create_client_duplicate_raises(self, init_db):
        """Повторное создание клиента с тем же client_id вызывает ValueError."""
        storage.create_client("dup", "Dup", db_path=init_db)
        with pytest.raises(ValueError, match="already exists"):
            storage.create_client("dup", "Dup 2", db_path=init_db)

    def test_get_client_existing(self, init_db):
        """get_client возвращает данные существующего клиента."""
        storage.create_client("get", "Get Me", project_id=42, sheet_id="sh", db_path=init_db)
        client = storage.get_client("get", init_db)
        assert client == {
            "client_id": "get",
            "client_name": "Get Me",
            "project_id": 42,
            "sheet_id": "sh",
            "searchers": [],
            "geos": [],
            "regions_map": [],
            "queries": [],
        }

    def test_get_client_missing_returns_none(self, init_db):
        """get_client возвращает None для несуществующего клиента."""
        assert storage.get_client("missing", init_db) is None

    def test_update_client(self, init_db):
        """update_client изменяет поля и обновляет updated_at."""
        storage.create_client("upd", "Upd", project_id=1, sheet_id="old", db_path=init_db)

        conn = sqlite3.connect(init_db)
        try:
            old_updated_at = conn.execute(
                "SELECT updated_at FROM clients WHERE client_id = 'upd'"
            ).fetchone()[0]
        finally:
            conn.close()

        # Пауза, чтобы updated_at гарантированно изменился (datetime('now') — секунды)
        time.sleep(1.1)

        storage.update_client(
            "upd",
            init_db,
            client_name="Updated",
            project_id=2,
            sheet_id="new",
            searchers=["google"],
            geos=["Литва"],
            regions_map="regions_map_upd.json",
        )

        client = storage.get_client("upd", init_db)
        assert client == {
            "client_id": "upd",
            "client_name": "Updated",
            "project_id": 2,
            "sheet_id": "new",
            "searchers": ["google"],
            "geos": ["Литва"],
            "regions_map": "regions_map_upd.json",
            "queries": [],
        }

        conn = sqlite3.connect(init_db)
        try:
            new_updated_at = conn.execute(
                "SELECT updated_at FROM clients WHERE client_id = 'upd'"
            ).fetchone()[0]
        finally:
            conn.close()

        assert new_updated_at > old_updated_at

    def test_update_client_not_found_raises(self, init_db):
        """update_client для несуществующего клиента вызывает ValueError."""
        with pytest.raises(ValueError, match="not found"):
            storage.update_client("ghost", init_db, client_name="Ghost")

    def test_update_client_rejects_unknown_fields(self, init_db):
        """update_client отклоняет недопустимые поля."""
        storage.create_client("bad", "Bad", db_path=init_db)
        with pytest.raises(ValueError, match="Недопустимые поля"):
            storage.update_client("bad", init_db, unknown="x")
