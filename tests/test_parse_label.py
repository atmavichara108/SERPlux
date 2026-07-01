"""
test_parse_label.py — юнит-тесты парсера метки из ответа LLM.

Тестирует функцию labeler._parse_label() изолированно:
- без вызова реального API
- без обращения к БД
- только логика извлечения positive/negative/neutral из строки
"""

import sys
import os

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "stub")
    monkeypatch.setenv("DB_PATH", ":memory:")


@pytest.fixture
def parse_label():
    """Импортирует _parse_label из labeler после установки env-заглушек."""
    import labeler
    return labeler._parse_label


# ─── Чистые ответы ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("positive", "positive"),
    ("negative", "negative"),
    ("neutral",  "neutral"),
    ("Positive", "positive"),   # регистр
    ("NEGATIVE", "negative"),
    ("Neutral",  "neutral"),
])
def test_clean_response(parse_label, raw, expected):
    """Чистый однословный ответ парсится корректно."""
    assert parse_label(raw) == expected


# ─── Ответ с лишним текстом / размышлениями ───────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("The answer is positive because the site is official.", "positive"),
    ("Based on the snippet, I would say: negative", "negative"),
    ("This is a neutral mention in a directory.", "neutral"),
    ("  positive  ", "positive"),                          # пробелы
    ("positive\n", "positive"),                            # перенос строки
    ("Result: POSITIVE!", "positive"),                     # восклицательный знак
    ("Ответ: negative (санкционный список)", "negative"),  # кириллица вокруг
    ("neutral — нейтральное упоминание", "neutral"),       # тире и кириллица
    ("I think it's positive, not negative", "positive"),   # первое совпадение
])
def test_response_with_extra_text(parse_label, raw, expected):
    """Метка извлекается из ответа с лишним текстом."""
    assert parse_label(raw) == expected


# ─── Мусорные ответы → neutral (fallback) ─────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "yes",
    "no",
    "good",
    "bad",
    "1",
    "N/A",
    "positiv",    # опечатка
    "negativ",
    "нейтральный",  # только кириллица без английского слова
])
def test_garbage_response_returns_neutral(parse_label, raw):
    """Мусорный ответ → fallback 'neutral'."""
    result = parse_label(raw)
    assert result == "neutral", (
        f"Ожидался fallback 'neutral' для {raw!r}, получен {result!r}"
    )
