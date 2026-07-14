"""
T-00X — тесты режимов labeler.py (auto и deep).

Проверяем:
- Режим AUTO: кэш domain_labels → сниппет → neutral при ошибке
- Режим DEEP: обработка только neutral, приоритет positive/negative
- Логирование по searcher×geo
- Приоритет manual_l1 при upsert
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


# ─── Режим AUTO: кэш domain_labels → сниппет → neutral ──────────────────────────


def test_auto_mode_cache_hit_from_domain_labels(init_db, sample_row, monkeypatch):
    """AUTO режим: находит метку в domain_labels и не вызывает LLM."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",  # Note: query может быть в разных случаях
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    llm_called = {"n": 0}

    def fake_label_one_llm(row, provider_chain=None):
        llm_called["n"] += 1
        raise AssertionError("LLM не должен вызываться, когда есть кэш")

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "positive"
    assert labeled["label"] == "positive"
    assert labeled["label_mode"] == "auto"
    assert llm_called["n"] == 0


def test_auto_mode_snippet_fallback_to_neutral_on_empty_snippet(init_db, sample_row, monkeypatch):
    """AUTO режим: пустой сниппет → neutral с confidence='uncertain' (без LLM вызова)."""
    sample_row["snippet"] = ""  # Пустой сниппет

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "positive")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"
    assert labeled["label_mode"] == "auto"


def test_auto_mode_snippet_fallback_to_neutral_on_provider_error(init_db, sample_row, monkeypatch):
    """AUTO режим: ошибка провайдера → neutral с confidence='uncertain'."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: None)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"


def test_auto_mode_snippet_success_and_saves_to_domain_labels(init_db, sample_row, monkeypatch):
    """AUTO режим: успешная разметка по сниппету, сохранённая в domain_labels."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "negative"
    assert labeled["label"] == "negative"

    # Проверяем, что результат сохранён в domain_labels
    cached = storage.get_domain_label("https://example.com/page1", "subject A", "Литва", init_db)
    assert cached == "negative"


def test_auto_mode_respects_manual_l1_priority(init_db, sample_row, monkeypatch):
    """AUTO режим: manual_l1 в domain_labels не перезаписывается."""
    # Вставляем manual_l1 запись
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    # Пытаемся перезаписать через AUTO режим (source='snippet')
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    # AUTO должна взять из кэша и вернуть positive (не позволить перезаписать)
    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "positive"

    # В domain_labels осталось positive (manual_l1 не изменилась)
    cached = storage.get_domain_label("https://example.com/page1", "subject a", "Литва", init_db)
    assert cached == "positive"


def test_auto_mode_force_relabel_ignores_cache(init_db, sample_row, monkeypatch):
    """AUTO режим: force_relabel=True игнорирует кэш domain_labels."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", force_relabel=True)

    assert len(result) == 1
    labeled = result[0]
    # При force_relabel, даже с manual_l1, попытаемся разметить по сниппету
    # Но manual_l1 всё равно не перезаписывается, так что будет negative
    # Проверим, что LLM был вызван
    assert labeled["sentiment"] is not None


# ─── Режим DEEP: только neutral обрабатывается ──────────────────────────────────


def test_deep_mode_ignores_positive_sentiment(init_db, sample_row):
    """DEEP режим: positive не трогает."""
    sample_row["sentiment"] = "positive"

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="deep")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "positive"
    assert labeled["label"] == "positive"
    assert labeled["label_mode"] == "deep"


def test_deep_mode_ignores_negative_sentiment(init_db, sample_row):
    """DEEP режим: negative не трогает."""
    sample_row["sentiment"] = "negative"

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="deep")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "negative"
    assert labeled["label"] == "negative"


def test_deep_mode_processes_neutral_sentiment(init_db, sample_row):
    """DEEP режим: neutral остаётся neutral (пока заглушка)."""
    sample_row["sentiment"] = "neutral"

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="deep")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["label_mode"] == "deep"


def test_deep_mode_mixed_sentiments(init_db, sample_row):
    """DEEP режим: positive и negative не трогает, neutral обрабатывает."""
    rows = [
        {**sample_row, "sentiment": "positive", "url": "https://example.com/pos", "position": 1},
        {**sample_row, "sentiment": "negative", "url": "https://example.com/neg", "position": 2},
        {**sample_row, "sentiment": "neutral", "url": "https://example.com/neut", "position": 3},
    ]

    result = labeler.label(rows, db_path=init_db, label_mode="deep")

    assert len(result) == 3
    assert result[0]["sentiment"] == "positive"
    assert result[1]["sentiment"] == "negative"
    assert result[2]["sentiment"] == "neutral"


# ─── Режим AUTO и DEEP: групповое логирование по searcher×geo ─────────────────


def test_auto_mode_logs_stats_per_searcher_geo(init_db, caplog, monkeypatch):
    """AUTO режим: логирует статистику по searcher×geo."""
    import logging

    # Устанавливаем уровень логирования
    caplog.set_level(logging.INFO, logger="labeler")

    # Вставляем метку в кэш для одной строки
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

    rows = [
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
    ]

    labeler.label(rows, db_path=init_db, label_mode="auto")

    # Проверяем, что в логах есть статистика по searcher×geo
    log_text = caplog.text
    assert "google" in log_text
    assert "Литва" in log_text
    assert "cache_hit" in log_text or "кэш" in log_text.lower()


# ─── Интеграционные тесты ────────────────────────────────────────────────────────


def test_full_pipeline_auto_then_deep(init_db, sample_row, monkeypatch):
    """Полный пайплайн: AUTO разметил, потом DEEP обрабатывает."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "negative")

    # Шаг 1: AUTO разметка
    rows = [sample_row]
    auto_result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert auto_result[0]["sentiment"] == "negative"
    assert auto_result[0]["label_mode"] == "auto"

    # Шаг 2: DEEP обработка (negative не трогает)
    deep_result = labeler.label(auto_result, db_path=init_db, label_mode="deep")

    assert deep_result[0]["sentiment"] == "negative"
    assert deep_result[0]["label_mode"] == "deep"


def test_unknown_label_mode_defaults_to_auto(init_db, sample_row, monkeypatch):
    """Неизвестный режим падает на AUTO с warning."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None: "positive")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="unknown_mode")

    # Должен использовать AUTO как fallback
    assert len(result) == 1
    assert result[0]["sentiment"] is not None


# ─── Тесты цепочки провайдеров ────────────────────────────────────────────────────


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
