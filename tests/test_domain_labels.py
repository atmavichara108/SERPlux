"""
T-00X — тесты таблицы domain_labels (storage.py).

Проверяем:
- get_domain_label / upsert_domain_label
- приоритет source='manual_l1'
- уникальность (domain, query)
- bulk_upsert_domain_labels
- нормализация домена
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
            "SELECT domain, query, sentiment, source FROM domain_labels"
        ).fetchone()
        assert row == ("example.com", "subject a", "negative", "snippet")
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


def test_manual_l1_conflict_returns_marker(init_db):
    """manual_l1 с ДРУГИМ sentiment поверх существующей manual_l1 → маркер конфликта.

    v1.0.2: конфликт не бросает исключение и не перезаписывает —
    upsert возвращает признак "manual_l1_conflict" наверх для жёлтой
    маркировки (apps_script «Спорные»). Запись сохраняется.
    """
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    result = storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "manual_l1", db_path=init_db
    )
    assert result == "manual_l1_conflict"

    # Существующая запись не изменилась (не перезаписана)
    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


def test_manual_l1_force_overwrites(init_db):
    """v1.0.5: force=True (явный операторский импорт) перезаписывает manual_l1.

    Инцидент 2026-09-14: исправления цветов заказчика не применялись —
    конфликт manual_l1 блокировал обновление. force разрешает last-write-wins
    ТОЛЬКО для явного ручного действия.
    """
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    result = storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "manual_l1",
        db_path=init_db, force=True,
    )
    assert result is None
    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "negative"


def test_bulk_upsert_force_overwrites(init_db):
    """v1.0.5: bulk_upsert с force=True перезаписывает конфликтующий manual_l1."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.bulk_upsert_domain_labels(
        [{"domain": "https://example.com/page", "query": "subject a",
          "sentiment": "negative", "source": "manual_l1"}],
        db_path=init_db, force=True,
    )
    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "negative"


def test_bulk_upsert_without_force_still_raises(init_db):
    """Без force конфликт manual_l1 по-прежнему ValueError (защита от автоматики)."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    with pytest.raises(ValueError, match="manual_l1 conflict"):
        storage.bulk_upsert_domain_labels(
            [{"domain": "https://example.com/page", "query": "subject a",
              "sentiment": "negative", "source": "manual_l1"}],
            db_path=init_db,
        )


def test_upsert_success_returns_none(init_db):
    """Успешный upsert возвращает None (не признак конфликта)."""
    result = storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    assert result is None


def test_manual_l1_same_sentiment_idempotent(init_db):
    """Повторный upsert manual_l1 с тем же sentiment — идемпотентный успех без ошибки."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


def test_bulk_upsert_manual_conflict_raises(init_db):
    """Bulk-батч с двумя manual_l1 (одинаковый domain+query, разный sentiment) → ValueError.

    bulk-путь сохраняет строгую валидацию батча (весь батч до вставки) —
    маркер конфликтов нужен только одиночному upsert через /labels/import.
    """
    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "manual_l1"},
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "negative", "source": "manual_l1"},
    ]
    with pytest.raises(ValueError, match="manual_l1 conflict"):
        storage.bulk_upsert_domain_labels(items, db_path=init_db)


def test_bulk_upsert_manual_same_sentiment_ok(init_db):
    """Две одинаковые manual_l1 записи в одном батче — без ошибки."""
    items = [
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "manual_l1"},
        {"url": "https://a.com/page", "query": "q1", "geo": "g1", "sentiment": "positive", "source": "manual_l1"},
    ]
    storage.bulk_upsert_domain_labels(items, db_path=init_db)

    assert storage.get_domain_label("https://a.com/page", "q1", "g1", init_db) == "positive"


def test_manual_l1_overwrites_snippet(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "positive"


# ─── Уникальность (domain, query) ────────────────────────────────────────────


def test_domain_query_geo_unique(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "negative", "page", db_path=init_db
    )

    conn = sqlite3.connect(init_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM domain_labels WHERE domain = ? AND query = ?",
            ("example.com", "subject a"),
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()

    assert storage.get_domain_label("https://example.com/page", "subject a", "Литва", init_db) == "negative"


def test_different_geo_uses_same_record(init_db):
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Латвия", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", init_db) == "negative"


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


# ─── Domain normalization ───────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    ("https://www.chempioil.com/de", "chempioil.com"),
    ("www.occrp.org", "occrp.org"),
    ("https://sctchemicals.ae/production/", "sctchemicals.ae"),
    ("OCCRP.ORG", "occrp.org"),
])
def test_normalize_domain_examples(value, expected):
    assert storage.normalize_domain(value) == expected


def test_domain_normalized_trailing_slash_and_fragment(init_db):
    storage.upsert_domain_label(
        "https://Example.COM/Page/?foo=bar#frag", "subject a", "Литва", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/Page?foo=bar", "subject a", init_db) == "positive"
    assert storage.get_domain_label("HTTPS://EXAMPLE.COM/Page?foo=bar", "subject a", init_db) == "positive"


def test_domain_normalized_lowercase_path(init_db):
    """Путь приводится к lowercase, чтобы `/Investigation/` и `/investigation/` совпадали."""
    storage.upsert_domain_label(
        "https://Example.COM/Investigation/", "subject a", "Литва", "negative", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/investigation", "subject a", init_db) == "negative"
    assert storage.get_domain_label("https://EXAMPLE.COM/INVESTIGATION", "subject a", init_db) == "negative"


# ─── geo normalization ───────────────────────────────────────────────────────


def test_geo_is_not_part_of_key(init_db):
    """Пробелы по краям и регистр geo не ломают составной ключ."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Литва", "positive", "snippet", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", init_db) == "positive"


def test_geo_normalized_english_value_lowercase(init_db):
    """Английское geo нормализуется только к lowercase, без маппинга на русский ключ."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Germany", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", init_db) == "positive"


def test_geo_normalized_cyprus_eng_lowercase(init_db):
    """Значение geo нормализуется только к lowercase, алиасы не маппятся."""
    storage.upsert_domain_label(
        "https://example.com/page", "subject a", "Кипр Eng", "positive", "manual_l1", db_path=init_db
    )

    assert storage.get_domain_label("https://example.com/page", "subject a", init_db) == "positive"


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
