"""
T-00X — тесты режимов labeler.py.

Проверяем:
- labeler.label() в режиме domains без вызова LLM
- обратную совместимость режима snippets
"""

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


# ─── labeler.py: режим domains ────────────────────────────────────────────────


def test_domains_mode_uses_dictionary_no_llm(init_db, sample_row, monkeypatch):
    storage.upsert_domain_label(
        domain="example.com",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

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


def test_domains_mode_respects_query_geo_key(init_db, sample_row, monkeypatch):
    # Для другого geo — другая запись
    storage.upsert_domain_label(
        domain="example.com",
        query="subject a",
        geo="Латвия",
        sentiment="negative",
        source="manual_l1",
        db_path=init_db,
    )

    def fail_if_called(row):
        raise AssertionError("LLM не должен вызываться в режиме domains")

    monkeypatch.setattr(labeler, "_label_one_llm", fail_if_called)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="domains")

    assert len(result) == 1
    labeled = result[0]
    # sample_row geo = "Литва", в справочнике только "Латвия" → не найдено
    assert labeled["sentiment"] is None


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

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

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


# ─── labeler.py: цепочка провайдеров из config ────────────────────────────────


def test_provider_chain_returns_enabled_sorted(monkeypatch):
    """_get_provider_chain возвращает включённых провайдеров по priority."""
    chain = labeler._get_provider_chain()
    assert len(chain) >= 1
    pid, cfg = chain[0]
    assert pid == "opencode-zen"
    assert cfg["enabled"] is True


def test_provider_chain_excludes_disabled(monkeypatch):
    """Отключённый провайдер исключается из цепочки."""
    import config as cfg_mod

    old = cfg_mod.PROVIDERS.copy()
    disabled = {k: {**v, "enabled": False} for k, v in old.items()}
    monkeypatch.setattr(cfg_mod, "PROVIDERS", disabled)

    chain = labeler._get_provider_chain()
    assert len(chain) == 0


def test_provider_chain_empty_when_none_enabled(monkeypatch):
    """Пустая цепочка, если нет включённых провайдеров — _label_one_llm
    возвращает None."""
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "PROVIDERS", {})

    chain = labeler._get_provider_chain()
    assert len(chain) == 0


def test_label_one_llm_returns_none_on_empty_chain(monkeypatch):
    """При пустой цепочке _label_one_llm возвращает None."""
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "PROVIDERS", {})

    row = {"url": "https://x.com", "query": "test", "snippet": "test"}
    result = labeler._label_one_llm(row)
    assert result is None
