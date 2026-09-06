"""
Тесты post-label валидатора эталона (v1.1 workstream A).

Проверяем контракт:
- Ключ эталона: (storage.normalize_domain(url), storage.normalize_query(query)).
- Жёсткий референс — ТОЛЬКО source=='manual_l1'; manual_l1 выигрывает ВСЕГДА,
  даже при force_relabel=True; LLM при manual-hit не вызывается.
- Категории журнала: manual_neutral / unmatched_neutral / manual_conflict /
  invalid_or_unknown; ok — запись в журнале НЕ создаётся.
- manual_conflict: строка ИСПРАВЛЯЕТСЯ на эталон (sentiment/label = manual,
  confidence='high').
- Эталон в domain_labels валидация НИКОГДА не меняет.
- Записи журнала привязаны к run_id; ретеншн через prune_label_conflicts.

Примечание: реализация в полёте — до её приезда тесты могут падать,
это ожидаемо. Файл зафиксирован на контракте.
"""

import time

import pytest

import labeler
import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_validator.db")


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


# ─── manual_l1: жёсткий референт, всегда выигрывает ─────────────────────────────


def test_manual_hit_skips_llm_even_with_force_relabel(init_db, sample_row, monkeypatch):
    """manual_l1 выигрывает ВСЕГДА: LLM не вызывается даже при force_relabel=True."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    def fake_label_one_llm(row, *args, **kwargs):
        raise AssertionError("LLM не должен вызываться при manual-hit даже с force_relabel")

    monkeypatch.setattr(labeler, "_label_one_llm", fake_label_one_llm)

    rows = [sample_row]
    result = labeler.label(
        rows,
        db_path=init_db,
        label_mode="auto",
        force_relabel=True,
    )

    assert len(result) == 1
    assert result[0]["sentiment"] == "positive"
    assert result[0]["label"] == "positive"


def test_manual_neutral_classified_and_recorded(init_db, sample_row, monkeypatch):
    """manual_l1 = neutral, результат neutral → категория manual_neutral, action 'none'."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="neutral",
        source="manual_l1",
        db_path=init_db,
    )
    sample_row["snippet"] = ""  # пустой сниппет: без manual ушёл бы в unmatched_neutral

    # Защитный мок: при ручном hit LLM звать не должны; если звали — вернём neutral
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: "neutral")

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert result[0]["sentiment"] == "neutral"
    assert out["manual_neutral"] == 1

    records = storage.get_label_conflicts(db_path=init_db)
    assert len(records) == 1
    assert records[0]["conflict_type"] == "manual_neutral"
    assert records[0]["recommended_action"] == "none"


# ─── unmatched_neutral: neutral без эталона ─────────────────────────────────────


def test_unmatched_neutral_on_empty_snippet(init_db, sample_row, monkeypatch):
    """Без manual, пустой сниппет → neutral + uncertain → unmatched_neutral."""
    sample_row["snippet"] = ""

    # Защитный мок: пустой сниппет не должен приводить к LLM-вызову
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: None)

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"
    assert out["unmatched_neutral"] == 1


def test_unmatched_neutral_on_provider_error(init_db, sample_row, monkeypatch):
    """Ошибка провайдера (_label_one_llm → None) → neutral + uncertain → unmatched_neutral."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: None)

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"
    assert out["unmatched_neutral"] == 1


# ─── ok: успешная LLM-разметка без эталона ──────────────────────────────────────


def test_llm_success_counts_ok_and_no_records(init_db, sample_row, monkeypatch):
    """Успешный LLM без manual → ok, записей в журнале нет."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: "negative")

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert result[0]["sentiment"] == "negative"
    assert out["ok"] == 1
    assert out["recorded"] == 0
    assert storage.get_label_conflicts(db_path=init_db) == []


# ─── manual_conflict: строка исправляется на эталон ─────────────────────────────


def test_manual_conflict_in_deep_mode_corrects_row(init_db, sample_row, monkeypatch):
    """deep-режим, drift: строка neutral исправляется на manual positive → manual_conflict."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )
    sample_row["sentiment"] = "neutral"  # drift относительно эталона

    # Защитный мок: deep может звать LLM по neutral; вернём neutral,
    # чтобы drift сохранился до валидации (сеть не дёргаем)
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: "neutral")

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="deep", validation_out=out)

    assert len(result) == 1
    labeled = result[0]
    # Строка ИСПРАВЛЕНА на эталон
    assert labeled["sentiment"] == "positive"
    assert labeled["label"] == "positive"
    assert labeled["confidence"] == "high"
    assert out["manual_conflict"] == 1

    records = storage.get_label_conflicts(db_path=init_db)
    assert len(records) == 1
    rec = records[0]
    assert rec["conflict_type"] == "manual_conflict"
    assert rec["observed_label"] == "neutral"
    assert rec["manual_label"] == "positive"
    assert rec["recommended_action"] == "resolve_conflict"


# ─── invalid_or_unknown: битый вход ─────────────────────────────────────────────


def test_invalid_missing_domain_key(init_db, sample_row, monkeypatch):
    """Пустой domain (url='') → invalid_or_unknown, recommended_action 'fix_input'."""
    sample_row["url"] = ""
    sample_row["domain"] = ""

    # Защитный мок: если реализация дойдёт до LLM — не ходим в сеть
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: "positive")

    out: dict = {}
    rows = [sample_row]
    labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert out["invalid_or_unknown"] == 1

    records = storage.get_label_conflicts(db_path=init_db)
    assert len(records) >= 1
    rec = records[0]
    assert rec["conflict_type"] == "invalid_or_unknown"
    assert rec["recommended_action"] == "fix_input"


def test_invalid_garbage_llm_answer(init_db, sample_row, monkeypatch):
    """Мусорный ответ провайдера → neutral + uncertain → invalid_or_unknown."""
    monkeypatch.setattr(
        labeler,
        "_call_provider",
        lambda provider_id, provider_cfg, prompt, model=None: "мусорный ответ без метки",
    )
    # Защита от rate-limit пауз между (реальными) вызовами
    monkeypatch.setattr(labeler.time, "sleep", lambda x: None)

    out: dict = {}
    rows = [sample_row]
    result = labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    assert len(result) == 1
    labeled = result[0]
    assert labeled["sentiment"] == "neutral"
    assert labeled["label"] == "neutral"
    assert labeled["confidence"] == "uncertain"
    assert out["invalid_or_unknown"] == 1


# ─── Инварианты эталона и журнала ───────────────────────────────────────────────


def test_etalon_not_corrupted_by_validation(init_db, sample_row, monkeypatch):
    """Прогон с конфликтами не меняет запись manual_l1 в domain_labels."""
    storage.upsert_domain_label(
        url="https://example.com/page1",
        query="subject a",
        geo="Литва",
        sentiment="positive",
        source="manual_l1",
        db_path=init_db,
    )

    # Конфликтный прогон: вторая строка без эталона и с пустым сниппетом
    # → unmatched_neutral → запись в журнале
    conflict_row = {
        **sample_row,
        "url": "https://other.example/page",
        "domain": "other.example",
        "snippet": "",
        "position": 2,
    }
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: None)

    out: dict = {}
    rows = [sample_row, conflict_row]
    labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    # Конфликт действительно был зафиксирован
    assert out["unmatched_neutral"] == 1
    assert len(storage.get_label_conflicts(db_path=init_db)) >= 1

    # Эталон не тронут: тот же sentiment и source
    rec = storage.get_domain_label_record("example.com", "subject a", init_db)
    assert rec is not None
    assert rec["sentiment"] == "positive"
    assert rec["source"] == "manual_l1"


def test_conflicts_linked_to_run_id(init_db, sample_row, monkeypatch):
    """Записи журнала привязаны к run_id, переданному в label()."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: None)

    out: dict = {}
    rows = [sample_row]
    labeler.label(
        rows,
        db_path=init_db,
        label_mode="auto",
        run_id="run-abc-123",
        validation_out=out,
    )

    records = storage.get_label_conflicts(run_id="run-abc-123", db_path=init_db)
    assert len(records) >= 1
    assert all(r["run_id"] == "run-abc-123" for r in records)


def test_validation_out_counts(init_db, sample_row, monkeypatch):
    """validation_out содержит все счётчики; total == len(rows)."""
    monkeypatch.setattr(labeler, "_label_one_llm", lambda row, *a, **k: "positive")

    ok_row = {**sample_row}
    neutral_row = {**sample_row, "url": "https://other.example/page", "domain": "other.example", "snippet": ""}

    out: dict = {}
    rows = [ok_row, neutral_row]
    labeler.label(rows, db_path=init_db, label_mode="auto", validation_out=out)

    expected_keys = {
        "total",
        "ok",
        "manual_neutral",
        "unmatched_neutral",
        "manual_conflict",
        "invalid_or_unknown",
        "recorded",
    }
    assert expected_keys <= set(out.keys())
    assert out["total"] == len(rows) == 2


# ─── Журнал: вставка, ретеншн, валидация ────────────────────────────────────────


def _conflict_record(run_id: str, domain: str) -> dict:
    """Полная запись журнала для прямой вставки через save_label_conflicts."""
    return {
        "run_id": run_id,
        "domain": domain,
        "url": f"https://{domain}/page",
        "query": "subject a",
        "geo": "Литва",
        "searcher": "google",
        "position": 1,
        "observed_label": "neutral",
        "manual_label": None,
        "source": None,
        "confidence": "uncertain",
        "conflict_type": "unmatched_neutral",
        "recommended_action": "monitor",
    }


def test_prune_keeps_last_runs(init_db):
    """prune удаляет прогоны вне последних N по времени (по MAX(created_at))."""
    # created_at имеет секундную точность — разделяем вставки по секундам,
    # чтобы порядок прогонов был детерминированным: r1 < r2 < r3.
    storage.save_label_conflicts([_conflict_record("r1", "a.example")], db_path=init_db)
    time.sleep(1.1)
    storage.save_label_conflicts([_conflict_record("r2", "b.example")], db_path=init_db)
    time.sleep(1.1)
    storage.save_label_conflicts([_conflict_record("r3", "c.example")], db_path=init_db)

    removed = storage.prune_label_conflicts(keep_last_runs=2, db_path=init_db)

    assert removed > 0
    remaining = storage.get_label_conflicts(db_path=init_db)
    remaining_runs = {r["run_id"] for r in remaining}
    assert remaining_runs == {"r2", "r3"}


def test_save_label_conflicts_rejects_invalid_type(init_db):
    """save_label_conflicts с невалидным conflict_type → ValueError."""
    record = _conflict_record("r1", "a.example")
    record["conflict_type"] = "garbage"

    with pytest.raises(ValueError):
        storage.save_label_conflicts([record], db_path=init_db)
