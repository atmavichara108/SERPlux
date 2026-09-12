"""
T-00X — тесты режимов labeler.py (auto и deep).

Проверяем:
- Режим AUTO: кэш domain_labels (по полному URL) → сниппет → neutral при ошибке
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

    def fake_label_one_llm(row, provider_chain=None, model=None, invalid_ref=None, rotation=None):
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

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "positive")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"
    assert labeled["label_mode"] == "auto"


def test_auto_mode_snippet_fallback_to_neutral_on_provider_error(init_db, sample_row, monkeypatch):
    """AUTO режим: ошибка провайдера → neutral с confidence='uncertain', кэш не отравляется."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: None)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"

    # Временный neutral при ошибке не должен попасть в domain_labels
    cached = storage.get_domain_label(
        "https://example.com/page1", "subject A", "Литва", init_db
    )
    assert cached is None


def test_auto_mode_snippet_success_is_not_saved_to_domain_labels(init_db, sample_row, monkeypatch):
    """AUTO режим: успешная разметка по сниппету не меняет эталон."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "negative"
    assert labeled["label"] == "negative"

    # Нейроразметка не записывается в domain_labels.
    cached = storage.get_domain_label("https://example.com/page1", "subject A", "Литва", init_db)
    assert cached is None


def test_report_etalon_coverage(init_db, sample_row, caplog):
    """Coverage считает уникальные domain+query и логирует пропущенный эталон."""
    import logging
    storage.upsert_domain_label("www.example.com", " SUBJECT A ", sentiment="positive", source="manual_l1", db_path=init_db)
    rows = [sample_row, {**sample_row, "url": "https://other.example/page"}]
    caplog.set_level(logging.INFO, logger="labeler")

    report = labeler.report_etalon_coverage(rows, init_db)

    assert report == {"total": 2, "matched": 1, "unmatched": 1, "coverage_pct": 50.0}
    assert "НЕТ ЭТАЛОНА: domain=other.example query=subject a" in caplog.text


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
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "negative")

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto")

    # AUTO должна взять из кэша и вернуть positive (не позволить перезаписать)
    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "positive"

    # В domain_labels осталось positive (manual_l1 не изменилась)
    cached = storage.get_domain_label("https://example.com/page1", "subject a", "Литва", init_db)
    assert cached == "positive"


def test_auto_mode_force_relabel_respects_manual_l1(init_db, sample_row, monkeypatch):
    """AUTO режим: force_relabel=True не обходит manual_l1 эталон (precedence rule)."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    llm_called = {"n": 0}

    def fake_label_one_llm(row, provider_chain=None, model=None, invalid_ref=None, rotation=None):
        llm_called["n"] += 1
        raise AssertionError("LLM не должен вызываться: manual_l1 выигрывает всегда")

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", force_relabel=True)

    assert len(result) == 1
    labeled = result[0]
    # manual_l1 эталон возвращается как есть, force_relabel его не обходит
    assert labeled["sentiment"] == "positive"
    assert labeled["label"] == "positive"
    assert llm_called["n"] == 0


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

    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "negative")

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
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "negative")

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
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "positive")

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


# ─── Тесты retry при 429 / сетевых ошибках ───────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_call_provider_retries_on_429_and_succeeds(monkeypatch):
    """_call_provider повторяет запрос при 429 и возвращает результат при успехе."""
    import requests as req_mod

    responses = iter([
        _FakeResponse(429, "Too Many Requests"),
        _FakeResponse(200, "positive"),
    ])

    def fake_post(*args, **kwargs):
        resp = next(responses)
        if resp.status_code >= 400:
            raise req_mod.exceptions.HTTPError("error", response=resp)
        return resp

    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(labeler.time, "sleep", lambda x: None)

    provider_cfg = {
        "default_model": "default-model",
        "endpoint": "https://example.com/v1/chat/completions",
        "api_key_env_var": "TEST_API_KEY",
    }
    monkeypatch.setenv("TEST_API_KEY", "test-key")

    result = labeler._call_provider("test-provider", provider_cfg, "prompt")
    assert result == "positive"


def test_call_provider_retries_on_network_error_and_succeeds(monkeypatch):
    """_call_provider повторяет запрос при сетевой ошибке и возвращает результат."""
    import requests as req_mod

    responses = iter([
        req_mod.exceptions.ConnectionError("connection refused"),
        _FakeResponse(200, "negative"),
    ])

    def fake_post(*args, **kwargs):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(labeler.time, "sleep", lambda x: None)

    provider_cfg = {
        "default_model": "default-model",
        "endpoint": "https://example.com/v1/chat/completions",
        "api_key_env_var": "TEST_API_KEY",
    }
    monkeypatch.setenv("TEST_API_KEY", "test-key")

    result = labeler._call_provider("test-provider", provider_cfg, "prompt")
    assert result == "negative"


def test_call_provider_gives_up_after_retries_on_429(monkeypatch):
    """_call_provider сдаётся после 3 попыток и возвращает None."""
    import requests as req_mod

    def fake_post(*args, **kwargs):
        resp = _FakeResponse(429, "Too Many Requests")
        raise req_mod.exceptions.HTTPError("error", response=resp)

    monkeypatch.setattr(req_mod, "post", fake_post)
    monkeypatch.setattr(labeler.time, "sleep", lambda x: None)

    provider_cfg = {
        "default_model": "default-model",
        "endpoint": "https://example.com/v1/chat/completions",
        "api_key_env_var": "TEST_API_KEY",
    }
    monkeypatch.setenv("TEST_API_KEY", "test-key")

    result = labeler._call_provider("test-provider", provider_cfg, "prompt")
    assert result is None


# ─── Тесты переключения модели ────────────────────────────────────────────────────


def test_label_passes_model_override(monkeypatch, init_db, sample_row):
    """label() передаёт model override в _label_one_llm."""
    captured_model: dict[str, str | None] = {"value": None}

    def fake_label_one_llm(row, provider_chain=None, model=None, invalid_ref=None, rotation=None):
        captured_model["value"] = model
        return "positive"

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    labeler.label(rows, db_path=init_db, label_mode="auto", model="custom-model-v2")

    assert captured_model["value"] == "custom-model-v2"


def test_label_without_model_override(monkeypatch, init_db, sample_row):
    """label() без model передаёт None в _label_one_llm."""
    captured_model: dict[str, str | None] = {"value": "not_none"}

    def fake_label_one_llm(row, provider_chain=None, model=None, invalid_ref=None, rotation=None):
        captured_model["value"] = model
        return "positive"

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    labeler.label(rows, db_path=init_db, label_mode="auto")

    assert captured_model["value"] is None


def test_call_provider_uses_model_override(monkeypatch):
    """_call_provider использует model override вместо default_model."""
    captured_model: dict[str, str | None] = {"value": None}

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "positive"}}]}

    import requests as req_mod
    monkeypatch.setattr(req_mod, "post", lambda *args, **kwargs: (captured_model.update({"value": kwargs["json"]["model"]}) or FakeResponse()))

    import config as cfg_mod
    provider_cfg = {
        "default_model": "default-model",
        "endpoint": "https://example.com/v1/chat/completions",
        "api_key_env_var": "TEST_API_KEY",
    }
    monkeypatch.setenv("TEST_API_KEY", "test-key")

    labeler._call_provider("test-provider", provider_cfg, "prompt", model="override-model")

    assert captured_model["value"] == "override-model"


def test_call_provider_uses_default_model_when_no_override(monkeypatch):
    """_call_provider использует default_model когда model=None."""
    captured_model: dict[str, str | None] = {"value": None}

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "positive"}}]}

    import requests as req_mod
    monkeypatch.setattr(req_mod, "post", lambda *args, **kwargs: (captured_model.update({"value": kwargs["json"]["model"]}) or FakeResponse()))

    import config as cfg_mod
    provider_cfg = {
        "default_model": "default-model-v1",
        "endpoint": "https://example.com/v1/chat/completions",
        "api_key_env_var": "TEST_API_KEY",
    }
    monkeypatch.setenv("TEST_API_KEY", "test-key")

    labeler._call_provider("test-provider", provider_cfg, "prompt")

    assert captured_model["value"] == "default-model-v1"


# ─── v1.0.3: breakdown разметки через stats_out ───────────────────────────────


def test_label_stats_out_etalon_hit(init_db, sample_row, monkeypatch):
    """stats_out: строка из эталона manual_l1 → etalon_hit=1, LLM не вызван."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )
    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: (
            pytest.fail("LLM не должен вызываться при etalon HIT")
        ),
    )

    stats_out: dict = {}
    labeler.label([sample_row], db_path=init_db, label_mode="auto", stats_out=stats_out)

    assert stats_out["etalon_hit"] == 1
    assert stats_out["llm_success"] == 0
    assert stats_out["total"] == 1


def test_label_stats_out_llm_success(init_db, sample_row, monkeypatch):
    """stats_out: успешная LLM-разметка → llm_success=1, etalon_hit=0."""
    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "negative",
    )

    stats_out: dict = {}
    labeler.label([sample_row], db_path=init_db, label_mode="auto", stats_out=stats_out)

    assert stats_out["llm_success"] == 1
    assert stats_out["etalon_hit"] == 0
    assert stats_out["total"] == 1


def test_label_stats_out_fallback_empty_snippet(init_db, sample_row):
    """stats_out: пустой сниппет → fallback_empty_snippet=1."""
    sample_row["snippet"] = ""

    stats_out: dict = {}
    labeler.label([sample_row], db_path=init_db, label_mode="auto", stats_out=stats_out)

    assert stats_out["fallback_empty_snippet"] == 1
    assert stats_out["llm_success"] == 0
    assert stats_out["total"] == 1


def test_label_stats_out_fallback_provider_error(init_db, sample_row, monkeypatch):
    """stats_out: все провайдеры недоступны → fallback_provider_error=1."""
    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: None,
    )

    stats_out: dict = {}
    labeler.label([sample_row], db_path=init_db, label_mode="auto", stats_out=stats_out)

    assert stats_out["fallback_provider_error"] == 1
    assert stats_out["total"] == 1


def test_label_stats_out_none_keeps_old_behavior(init_db, sample_row, monkeypatch):
    """stats_out=None (дефолт): поведение прежнее, breakdown только в логах."""
    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "positive",
    )

    result = labeler.label([sample_row], db_path=init_db, label_mode="auto")
    assert result[0]["sentiment"] == "positive"


def test_label_stats_out_mixed_sources(init_db, sample_row, monkeypatch):
    """stats_out: смешанный пачка — сумма категорий сходится с total."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    row_llm = {
        **sample_row,
        "url": "https://other.com/x",
        "domain": "other.com",
        "snippet": "Other snippet",
    }
    row_empty = {**row_llm, "url": "https://third.com/y", "domain": "third.com", "snippet": ""}

    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: "neutral",
    )

    stats_out: dict = {}
    labeler.label(
        [sample_row, row_llm, row_empty],
        db_path=init_db, label_mode="auto", stats_out=stats_out,
    )

    assert stats_out["etalon_hit"] == 1
    assert stats_out["llm_success"] == 1
    assert stats_out["fallback_empty_snippet"] == 1
    assert stats_out["total"] == 3
    categorized = (
        stats_out["etalon_hit"] + stats_out["llm_success"]
        + stats_out["fallback_empty_snippet"] + stats_out["fallback_provider_error"]
        + stats_out["fallback_invalid_llm"] + stats_out["fallback_invalid_key"]
        + stats_out["invalid_key"]
    )
    assert categorized == stats_out["total"]


# ─── v1.0.4: динамическая ротация моделей (3-strikes cooldown) ────────────────


def test_rotation_pool_excludes_cooldown_models():
    """_effective_model_pool: модель в cooldown исключается из пула."""
    provider_cfg = {
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "glm-5.3-flash", "kimi-k2.6"],
    }
    rotation = labeler._new_rotation_state()
    rotation["cooldown"].add("deepseek-v4-flash")
    pool = labeler._effective_model_pool(provider_cfg, None, rotation)
    assert pool == ["glm-5.3-flash", "kimi-k2.6"]


def test_rotation_pool_preferred_model_first():
    """_effective_model_pool: preferred model (model override) идёт первой."""
    provider_cfg = {
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "glm-5.3-flash", "kimi-k2.6"],
    }
    rotation = labeler._new_rotation_state()
    pool = labeler._effective_model_pool(provider_cfg, "kimi-k2.6", rotation)
    assert pool == ["kimi-k2.6", "deepseek-v4-flash", "glm-5.3-flash"]


def test_rotation_three_strikes_then_next_model(monkeypatch, init_db, sample_row):
    """Семантика v1.0.4: отказ модели в строке -> следующая модель пула;
    счётчик последовательных отказов копится МЕЖДУ строками; 3 подряд ->
    cooldown, запросы подхватывает следующая модель динамически."""
    calls = []

    def fake_call(provider_id, provider_cfg, prompt, model=None):
        calls.append(model)
        return None if model == "deepseek-v4-flash" else "positive"

    monkeypatch.setattr(labeler, "_call_provider", fake_call)

    rotation = labeler._new_rotation_state()
    row = dict(sample_row)

    # Строки 1-2: deepseek отказал, glm подхватил сразу
    for _ in range(2):
        assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert "deepseek-v4-flash" not in rotation["cooldown"]
    assert rotation["consecutive_fail"]["deepseek-v4-flash"] == 2

    # Строка 3: третий отказ -> cooldown; запрос всё равно успевает в glm
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert "deepseek-v4-flash" in rotation["cooldown"]
    assert "glm-5.3-flash" not in rotation["cooldown"]

    # Строка 4+: deepseek в cooldown -> только glm, без новых попыток deepseek
    calls.clear()
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert calls == ["glm-5.3-flash"]


def test_rotation_success_resets_fail_counter(monkeypatch, init_db, sample_row):
    """Успешный вызов модели сбрасывает её счётчик последовательных отказов:
    после сброса модели нужно снова 3 отказа, чтобы уйти в cooldown."""
    # Поведение fake: deepseek-v4-flash отказывает по скрипту строк
    # (строки 1-2 fail, строка 3 success -> сброс, строка 4 fail);
    # glm-5.3-flash всегда успешен.
    ds_script = iter([None, None, "positive", None])
    calls = []

    def fake_call(provider_id, provider_cfg, prompt, model=None):
        calls.append(model)
        if model == "deepseek-v4-flash":
            return next(ds_script)
        return "positive"

    monkeypatch.setattr(labeler, "_call_provider", fake_call)

    rotation = labeler._new_rotation_state()

    # Строка 1: ds fail (fail=1) -> glm подхватывает
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert rotation["consecutive_fail"]["deepseek-v4-flash"] == 1
    assert "deepseek-v4-flash" not in rotation["cooldown"]

    # Строка 2: ds fail (fail=2) -> glm подхватывает
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert rotation["consecutive_fail"]["deepseek-v4-flash"] == 2
    assert "deepseek-v4-flash" not in rotation["cooldown"]

    # Строка 3: ds success -> счётчик сброшен в 0
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert rotation["consecutive_fail"]["deepseek-v4-flash"] == 0
    assert "deepseek-v4-flash" not in rotation["cooldown"]

    # Строка 4: ds fail снова (fail=1, не 2!) — сброс работал
    assert labeler._label_one_llm(dict(sample_row), rotation=rotation) == "positive"
    assert rotation["consecutive_fail"]["deepseek-v4-flash"] == 1
    assert "deepseek-v4-flash" not in rotation["cooldown"]


def test_rotation_all_models_cooldown_returns_none(monkeypatch, init_db, sample_row):
    """Все модели в cooldown -> _label_one_llm возвращает None без вызовов."""
    calls = []

    def fake_call(provider_id, provider_cfg, prompt, model=None):
        calls.append(model)
        return None

    monkeypatch.setattr(labeler, "_call_provider", fake_call)

    provider_cfg = {
        "enabled": True,
        "priority": 1,
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash"],
        "endpoint": "https://example.com",
        "api_key_env_var": "TEST_KEY",
    }
    monkeypatch.setattr(labeler.config, "PROVIDERS", {"p1": provider_cfg})
    monkeypatch.setenv("TEST_KEY", "k")

    rotation = labeler._new_rotation_state()
    rotation["cooldown"].add("deepseek-v4-flash")
    row = dict(sample_row)
    assert labeler._label_one_llm(row, provider_chain=None, rotation=rotation) is None
    assert calls == []  # cooldown-модель не вызывалась


def test_label_stats_out_llm_by_model(init_db, sample_row, monkeypatch):
    """stats.labeling.llm_by_model: успешные модели попадают в breakdown."""
    monkeypatch.setattr(
        labeler, "_label_one_llm",
        lambda row, provider_chain=None, model=None, invalid_ref=None, rotation=None: (
            rotation and rotation["consecutive_fail"].update(
                {"deepseek-v4-flash": 0, "glm-5.3-flash": 0}
            ) or "positive"
        ),
    )
    stats_out = {}
    labeler.label([dict(sample_row)], db_path=init_db, label_mode="auto", stats_out=stats_out)
    assert stats_out["llm_success"] == 1
    assert set(stats_out["llm_by_model"]) == {"deepseek-v4-flash", "glm-5.3-flash"}
