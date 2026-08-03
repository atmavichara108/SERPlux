"""
T-00X — тесты таблицы domain_labels (storage.py).

Проверяем:
- get_domain_label / upsert_domain_label
- приоритет source='manual_l1'
- уникальность (url, query, geo)
- bulk_upsert_domain_labels
- нормализация URL (lowercase scheme/host, trailing slash, fragment)
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
        url="https://example.com/page",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    result = storage.get_domain_label("https://example.com/page", "subject A", "Литва", init_db)
    assert result == "positive"


def test_get_domain_label_not_found(init_db):
    assert storage.get_domain_label("https://unknown.com/page", "subject a", "Литва", init_db) is None


def test_upsert_domain_label_insert(init_db):
    storage.upsert_domain_label(
        url="https://example.com/page",
        query="subject a",
        geo="Литва",
        sentiment="negative",
        source="snippet",
        db_path=init_db,
    )

    conn = sqlite3.connect(init_db)
    try:
        row = conn.execute(
            "SELECT url, query, geo, sentiment, source FROM domain_labels"
        ).fetchone()
        # geo хранится в lowercase после нормализации
        assert row == ("https://example.com/page", "subject a", "литва", "negative", "snippet")
    finally:
        conn.close()


def test_upsert_domain_label_update_same_source(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "neutral", "page", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "neutral"


# ── Приоритет manual_l1 ────────────────────────────────────────────────────


def test_manual_l1_not_overwritten_by_snippet(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


def test_manual_l1_not_overwritten_by_page(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "page", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


def test_manual_l1_overwrites_manual_l1(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "negative"


def test_manual_l1_overwrites_snippet(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


# ─── Уникальность (domain, query, geo) ───────────────────────────────────────


def test_domain_query_geo_unique(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "page", db_path=init_db
    )

    conn = sqlite3.connect(init_db)
    try:
        # geo хранится в lowercase после нормализации
        count = conn.execute(
            "SELECT COUNT(*) FROM domain_labels WHERE url = ? AND query = ? AND geo = ?",
            ("https://example.com/page", "subject a", "литва"),
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "negative"


def test_different_geo_is_separate_record(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Латвия", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"
    assert storage.get_domain_label("https://example.com/page", "subject a", "Латвия", init_db) == "negative"


def test_different_query_is_separate_record(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject b", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"
    assert storage.get_domain_label("https://example.com/page", "subject b", "Литва", init_db) == "negative"


# ─── bulk_upsert ─────────────────────────────────────────────────────────────


def test_bulk_upsert_domain_labels(init_db):
    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "snippet"},
        {"url": "https://b.com/page", "query": "q2", "geo": "g2", "sentiment": "negative", "source": "page"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    assert storage.get_domain_label("https://a.com/page", "q1", "g1", init_db) == "positive"
    assert storage.get_domain_label("https://b.com/page", "q2", "g2", init_db) == "negative"


def test_bulk_upsert_respects_manual_l1_priority(init_db):
    storage.upsert_domain_label(
        "https://a.com/page", "q1", "g1", "positive", "manual_l1", db_path=init_db
    )

    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "negative", "source": "snippet"},
        {"url": "https://b.com/page", "query": "q2", "geo": "g2", "sentiment": "neutral", "source": "page"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    # manual_l1 не перезаписан
    assert storage.get_domain_label("https://a.com/page", "q1", "g1", init_db) == "positive"
    # остальные вставились
    assert storage.get_domain_label("https://b.com/page", "q2", "g2", init_db) == "neutral"


def test_bulk_upsert_manual_l1_overwrites_snippet(init_db):
    storage.upsert_domain_label(
        "https://a.com/page", "q1", "g1", "negative", "snippet", db_path=init_db
    )

    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "manual_l1"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    assert storage.get_domain_label("https://a.com/page", "q1", "g1", init_db) == "positive"


# ─── query lowercase normalization ───────────────────────────────────────────


def test_query_normalized_to_lowercase(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "SuBjEcT A", "Литва", "positive", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "SUBJECT A", "Литва", init_db) == "positive"
    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


# ─── URL normalization ───────────────────────────────────────────────────────


def test_url_normalized_trailing_slash_and_fragment(init_db):
    storage.upsert_domain_label(
        "https://Example.COM/Page/?foo=bar#frag", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    # Trailing slash и fragment отбрасываются, scheme/host lowercase
    assert storage.get_domain_label("https://example.com/Page?foo=bar", "subject a", "Литва", init_db) == "positive"
    assert storage.get_domain_label("HTTPS://EXAMPLE.COM/Page?foo=bar", "subject a", "Литва", init_db) == "positive"


def test_url_normalized_lowercase_path(init_db):
    """Путь приводится к lowercase, чтобы `/Investigation/` и `/investigation/` совпадали."""
    storage.upsert_domain_label(
        "https://Example.COM/Investigation/", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/investigation", "subject a", "Литва", init_db) == "negative"
    assert storage.get_domain_label("https://EXAMPLE.COM/INVESTIGATION", "subject a", "Литва", init_db) == "negative"


# ─── geo normalization ───────────────────────────────────────────────────────


def test_geo_normalized_strip_and_lowercase(init_db):
    """Пробелы по краям и регистр geo не ломают составной ключ."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", " литва ", init_db) == "positive"
    assert storage.get_domain_label("https://example.com/page", "subject a", "ЛИТВА", init_db) == "positive"


def test_bulk_upsert_domain_labels_geo_normalized(init_db):
    """bulk_upsert нормализует geo, чтобы совпадал ключ с ручной вставкой."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    items = [
        {"url": "https://example.com/page", "query": "subject a", "geo": " литва ", "sentiment": "negative", "source": "snippet"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    # manual_l1 не перезаписан из-за нормализации geo
    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


# ─── source validation ───────────────────────────────────────────────────────


def test_upsert_domain_label_rejects_invalid_source(init_db):
    with pytest.raises(ValueError):
        storage.upsert_domain_label(
            "https://example.com/page", "subject a", "Литва", "positive", "manual", db_path=init_db
        )


def test_bulk_upsert_domain_label_rejects_invalid_source(init_db):
    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "invalid"},
    ]
    with pytest.raises(ValueError):
        storage.bulk_upsert_domain_labels(items, db_path=init_db)
