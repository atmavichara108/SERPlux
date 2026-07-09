"""
T-00X — тесты таблицы domain_labels (storage.py).

Проверяем:
- get_domain_label / upsert_domain_label
- приоритет source='manual_l1'
- уникальность (domain, query, geo)
- bulk_upsert_domain_labels
"""

import sqlite3

import pytest

import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_domain_labels.db")


@pytest.fixture
def init_db(db_path):
    storage._init_db(db_path)
    return db_path


# ─── get / upsert ─────────────────────────────────────────────────────────────


def test_get_domain_label_found(init_db):
    storage.upsert_domain_label(
        domain="example.com",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    result = storage.get_domain_label("example.com", "subject A", "Литва", init_db)
    assert result == "positive"


def test_get_domain_label_not_found(init_db):
    assert storage.get_domain_label("unknown.com", "subject a", "Литва", init_db) is None


def test_upsert_domain_label_insert(init_db):
    storage.upsert_domain_label(
        domain="example.com",
        query="subject a",
        geo="Литва",
        sentiment="negative",
        source="snippet",
        db_path=init_db,
    )

    conn = sqlite3.connect(init_db)
    try:
        row = conn.execute(
            "SELECT domain, query, geo, sentiment, source FROM domain_labels"
        ).fetchone()
        assert row == ("example.com", "subject a", "Литва", "negative", "snippet")
    finally:
        conn.close()


def test_upsert_domain_label_update_same_source(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "neutral", "page", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "neutral"


# ─── Приоритет manual_l1 ────────────────────────────────────────────────────


def test_manual_l1_not_overwritten_by_snippet(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"


def test_manual_l1_not_overwritten_by_page(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "negative", "page", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"


def test_manual_l1_overwrites_manual_l1(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "negative", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "negative"


def test_manual_l1_overwrites_snippet(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"


# ─── Уникальность (domain, query, geo) ───────────────────────────────────────


def test_domain_query_geo_unique(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "negative", "page", db_path=init_db
    )

    conn = sqlite3.connect(init_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM domain_labels WHERE domain = ? AND query = ? AND geo = ?",
            ("example.com", "subject a", "Литва"),
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "negative"


def test_different_geo_is_separate_record(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject a", "Латвия", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"
    assert storage.get_domain_label("example.com", "subject a", "Латвия", init_db) == "negative"


def test_different_query_is_separate_record(init_db):
    storage.upsert_domain_label(
        "example.com", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "example.com", "subject b", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"
    assert storage.get_domain_label("example.com", "subject b", "Литва", init_db) == "negative"


# ─── bulk_upsert ─────────────────────────────────────────────────────────────


def test_bulk_upsert_domain_labels(init_db):
    items = [
        {"domain": "a.com", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "snippet"},
        {"domain": "b.com", "query": "q2", "geo": "g2", "sentiment": "negative", "source": "page"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    assert storage.get_domain_label("a.com", "q1", "g1", init_db) == "positive"
    assert storage.get_domain_label("b.com", "q2", "g2", init_db) == "negative"


def test_bulk_upsert_respects_manual_l1_priority(init_db):
    storage.upsert_domain_label(
        "a.com", "q1", "g1", "positive", "manual_l1", db_path=init_db
    )

    items = [
        {"domain": "a.com", "query": "q1", "geo": "g1", "sentiment": "negative", "source": "snippet"},
        {"domain": "b.com", "query": "q2", "geo": "g2", "sentiment": "neutral", "source": "page"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    # manual_l1 не перезаписан
    assert storage.get_domain_label("a.com", "q1", "g1", init_db) == "positive"
    # остальные вставились
    assert storage.get_domain_label("b.com", "q2", "g2", init_db) == "neutral"


def test_bulk_upsert_manual_l1_overwrites_snippet(init_db):
    storage.upsert_domain_label(
        "a.com", "q1", "g1", "negative", "snippet", db_path=init_db
    )

    items = [
        {"domain": "a.com", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "manual_l1"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    assert storage.get_domain_label("a.com", "q1", "g1", init_db) == "positive"


# ─── query lowercase normalization ───────────────────────────────────────────


def test_query_normalized_to_lowercase(init_db):
    storage.upsert_domain_label(
        "example.com", "SuBjEcT A", "Литва", "positive", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("example.com", "SUBJECT A", "Литва", init_db) == "positive"
    assert storage.get_domain_label("example.com", "subject a", "Литва", init_db) == "positive"


# ─── source validation ───────────────────────────────────────────────────────


def test_upsert_domain_label_rejects_invalid_source(init_db):
    with pytest.raises(ValueError):
        storage.upsert_domain_label(
            "example.com", "subject a", "Литва", "positive", "manual", db_path=init_db
        )


def test_bulk_upsert_domain_label_rejects_invalid_source(init_db):
    items = [
        {"domain": "a.com", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "invalid"},
    ]
    with pytest.raises(ValueError):
        storage.bulk_upsert_domain_labels(items, db_path=init_db)
