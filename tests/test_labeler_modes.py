"""
T-00X — тесты справочника доменов и режима domains в labeler.py.

Проверяем:
- storage.get_domain_label / upsert_domain_label
- labeler.label() в режиме domains без вызова LLM
- обратную совместимость режима snippets
"""

import sqlite3

import pytest

import labeler
import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_labeler_modes.db")


@pytest.fixture
def init_db(db_path):
    storage._init_db(db_path)
    return db_path


@pytest.fixture
def sample_row():
    return {
        "date": "2026-06-19",
        "searcher": "google",
        "query": "subject A",
        "geo": "Литва",
        "region_index": 1300,
        "position": 1,
        "url": "https://example.com/page1",
        "domain": "example.com",
        "snippet": "Snippet 1",
    }


# ─── storage.py: справочник доменов ───────────────────────────────────────────


def test_get_domain_label_found(init_db):
    storage.upsert_domain_label("default", "example.com", "positive", db_path=init_db)

    result = storage.get_domain_label("default", "example.com", init_db)

    assert result == {
        "sentiment": "positive",
        "source": "manual",
        "confidence": "high",
    }


def test_get_domain_label_not_found(init_db):
    assert storage.get_domain_label("default", "unknown.com", init_db) is None


def test_upsert_domain_label_insert(init_db):
    storage.upsert_domain_label("default", "example.com", "negative", db_path=init_db)

    conn = sqlite3.connect(init_db)
    try:
        row = conn.execute(
            "SELECT client_id, domain, sentiment, source FROM domain_labels"
        ).fetchone()
        assert row == ("default", "example.com", "negative", "manual")
    finally:
        conn.close()


def test_upsert_domain_label_update(init_db):
    storage.upsert_domain_label("default", "example.com", "positive", db_path=init_db)
    storage.upsert_domain_label("default", "example.com", "neutral", source="llm", db_path=init_db)

    result = storage.get_domain_label("default", "example.com", init_db)
    assert result is not None
    assert result["sentiment"] == "neutral"
    assert result["source"] == "llm"
    assert result["confidence"] == "high"

    conn = sqlite3.connect(init_db)
    try:
        row = conn.execute(
            "SELECT created_at, updated_at, source FROM domain_labels"
        ).fetchone()
        created_at, updated_at, source = row
        assert source == "llm"
        # updated_at должен быть не раньше created_at
        assert updated_at >= created_at
    finally:
        conn.close()


def test_get_domain_label_client_isolation(init_db):
    storage.upsert_domain_label("client-a", "example.com", "positive", db_path=init_db)

    assert storage.get_domain_label("default", "example.com", init_db) is None
    found = storage.get_domain_label("client-a", "example.com", init_db)
    assert found is not None and found["sentiment"] == "positive"


# ─── labeler.py: режим domains ────────────────────────────────────────────────


def test_domains_mode_uses_dictionary_no_llm(init_db, sample_row, monkeypatch):
    storage.upsert_domain_label("default", "example.com", "positive", db_path=init_db)

    llm_called = {"n": 0}

    def fake_label_one_llm(row):
        llm_called["n"] += 1
        raise AssertionError("LLM не должен вызываться в режиме domains")

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="domains")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "positive"
    assert labeled["label"] == "positive"
    assert labeled["confidence"] == "high"
    assert labeled["label_mode"] == "domains"
    assert llm_called["n"] == 0


def test_domains_mode_missing_domain_returns_none(init_db, sample_row, monkeypatch):
    llm_called = {"n": 0}

    def fake_label_one_llm(row):
        llm_called["n"] += 1
        return "positive"

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="domains")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] is None
    assert labeled["label"] is None
    assert labeled["confidence"] == "high"
    assert llm_called["n"] == 0


def test_domains_mode_respects_client_id(init_db, sample_row, monkeypatch):
    storage.upsert_domain_label("client-a", "example.com", "negative", db_path=init_db)

    def fail_if_called(row):
        raise AssertionError("LLM не должен вызываться в режиме domains")

    monkeypatch.setattr(labeler, "_label_one_llm", fail_if_called)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="domains", client_id="client-a")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "negative"
    assert labeled["client_id"] == "client-a"


# ─── labeler.py: режим snippets не сломан ─────────────────────────────────────


def test_snippets_mode_uses_cache(init_db, sample_row, monkeypatch):
    storage.save([sample_row], init_db)
    storage.insert_labels([{**sample_row, "sentiment": "neutral", "label_mode": "snippets"}], init_db)

    llm_called = {"n": 0}

    def fake_label_one_llm(row):
        llm_called["n"] += 1
        return "positive"

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="snippets")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "high"
    assert llm_called["n"] == 0


def test_snippets_mode_force_relabel_calls_llm(init_db, sample_row, monkeypatch):
    storage.save([sample_row], init_db)
    storage.insert_labels([{**sample_row, "sentiment": "neutral", "label_mode": "snippets"}], init_db)

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="snippets", force_relabel=True)

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "negative"
    assert labeled["label"] == "negative"
    assert labeled["confidence"] == "high"


def test_full_mode_is_stub(init_db, sample_row):
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="full")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] is None
    assert labeled["label"] is None
    assert labeled["confidence"] == "high"
    assert labeled["label_mode"] == "full"
