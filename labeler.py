import os
import time
import logging
from dotenv import load_dotenv
import google.generativeai as genai
import requests

import storage

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
FALLBACK_MODEL = "opencode/deepseek-v4-flash-free"
FALLBACK_API_URL = "https://api.opencode.ai/v1/chat/completions"
LLM_PAUSE = 4  # секунд между вызовами, чтобы не ловить 429

VALID_LABELS = {"positive", "negative", "neutral"}

SYSTEM_PROMPT = (
    "Ты анализируешь репутацию субъекта в поисковой выдаче. "
    "Оцени, как ссылка влияет на репутацию субъекта. "
    "Ответь ОДНИМ словом: positive, negative или neutral."
)


def _build_prompt(query: str, url: str, snippet: str) -> str:
    return (
        f"Ты анализируешь репутацию субъекта '{query}' в поисковой выдаче.\n"
        f"URL: {url}\n"
        f"Сниппет: {snippet}\n"
        f"Оцени, как эта ссылка влияет на репутацию субъекта. Ответь ОДНИМ словом:\n"
        f"positive — ссылка выгодна субъекту (офиц. сайт, позитивное упоминание, соцсети субъекта),\n"
        f"negative — вредит репутации (компромат, санкции, отмывание, скандал, негатив),\n"
        f"neutral — нейтральное упоминание (каталоги, справочники, отзывы без оценки).\n"
        f"Ответь только одно слово: positive, negative или neutral."
    )


def _parse_label(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    for label in VALID_LABELS:
        if label in cleaned:
            return label
    return None


def _call_gemini(prompt: str) -> str | None:
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            [SYSTEM_PROMPT, prompt],
            generation_config={"temperature": 0.1, "max_output_tokens": 10},
        )
        return response.text
    except Exception as e:
        log.error("Gemini ошибка: %s", e)
        return None


def _call_fallback(prompt: str) -> str | None:
    api_key = os.environ.get("OPENCODE_API_KEY")
    if not api_key:
        log.error("OPENCODE_API_KEY не задан, фолбек невозможен")
        return None
    try:
        resp = requests.post(
            FALLBACK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": FALLBACK_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 10,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.error("Фолбек ошибка: %s", e)
        return None


def _label_one(row: dict, db_path: str = storage.DB_PATH) -> str | None:
    cached = storage.get_cached_label(row["url"], row["query"], db_path)
    if cached is not None:
        log.info("из кэша: %s + '%s' -> %s", row["url"], row["query"], cached)
        return cached

    prompt = _build_prompt(row["query"], row["url"], row.get("snippet", ""))

    raw = _call_gemini(prompt)
    if raw is not None:
        label = _parse_label(raw)
        if label is not None:
            log.info("Gemini: %s + '%s' -> %s", row["url"], row["query"], label)
            return label
        log.warning("Gemini вернул мусор '%s', ставлю neutral", raw.strip())
        return "neutral"

    log.warning("Gemini упал, пробую фолбек для %s", row["url"])
    raw = _call_fallback(prompt)
    if raw is not None:
        label = _parse_label(raw)
        if label is not None:
            log.info("Фолбек: %s + '%s' -> %s", row["url"], row["query"], label)
            return label
        log.warning("Фолбек вернул мусор '%s', ставлю neutral", raw.strip())
        return "neutral"

    log.error("Оба провайдера упали для %s, label=None", row["url"])
    return None


def label(rows: list[dict], db_path: str = storage.DB_PATH) -> list[dict]:
    load_dotenv()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    result = []
    last_real_call = 0.0

    for row in rows:
        cached = storage.get_cached_label(row["url"], row["query"], db_path)
        if cached is not None:
            row["label"] = cached
            log.info("из кэша: %s + '%s' -> %s", row["url"], row["query"], cached)
            result.append(row)
            continue

        now = time.time()
        elapsed = now - last_real_call
        if elapsed < LLM_PAUSE and last_real_call > 0:
            wait = LLM_PAUSE - elapsed
            log.debug("Пауза %.1fс между вызовами LLM", wait)
            time.sleep(wait)

        row["label"] = _label_one(row, db_path)
        last_real_call = time.time()
        result.append(row)

    return result


if __name__ == "__main__":
    import os as _os

    TEST_DB = "test_serplux.db"

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)
    storage._init_db(TEST_DB)

    fake_rows = [
        {
            "date": "2026-06-21",
            "searcher": "google",
            "query": "Ivan Petrov",
            "geo": "Москва",
            "region_index": 213,
            "position": 1,
            "url": "https://sanctions-list.example/ivan-petrov",
            "domain": "sanctions-list.example",
            "snippet": "Ivan Petrov включён в список санкций за отмывание денег и коррупционные схемы",
            "label": None,
        },
        {
            "date": "2026-06-21",
            "searcher": "google",
            "query": "Ivan Petrov",
            "geo": "Москва",
            "region_index": 213,
            "position": 2,
            "url": "https://ivan-petrov.ru",
            "domain": "ivan-petrov.ru",
            "snippet": "Официальный сайт Ивана Петрова. Услуги, контакты, биография",
            "label": None,
        },
        {
            "date": "2026-06-21",
            "searcher": "google",
            "query": "Ivan Petrov",
            "geo": "Москва",
            "region_index": 213,
            "position": 5,
            "url": "https://spravka.example/person/ivan-petrov",
            "domain": "spravka.example",
            "snippet": "Карточка персоны: Ivan Petrov, дата рождения, адрес регистрации",
            "label": None,
        },
    ]

    mock_responses = {
        "sanctions-list.example": "negative",
        "ivan-petrov.ru": "positive",
        "spravka.example": "neutral",
    }

    def mock_gemini(prompt: str) -> str | None:
        for domain, label in mock_responses.items():
            if domain in prompt:
                return label
        return None

    def mock_fallback(prompt: str) -> str | None:
        return mock_gemini(prompt)

    import unittest.mock as mock

    with mock.patch("__main__._call_gemini", mock_gemini):
        with mock.patch("__main__._call_fallback", mock_fallback):
            print("=== Тест labeler.py (изолированная БД: %s) ===\n" % TEST_DB)

            results = label(fake_rows, TEST_DB)

            print("Результаты разметки:")
            for row in results:
                print(f"  {row['url']}")
                print(f"    query: {row['query']}")
                print(f"    label: {row['label']}")
                print()

            assert results[0]["label"] == "negative", f"Ожидалось negative, получено {results[0]['label']}"
            assert results[1]["label"] == "positive", f"Ожидалось positive, получено {results[1]['label']}"
            assert results[2]["label"] == "neutral", f"Ожидалось neutral, получено {results[2]['label']}"
            print("✓ Все метки совпали с ожидаемыми\n")

            print("=== Тест кэша ===")
            no_cache = storage.get_cached_label("https://unknown.com", "unknown query", TEST_DB)
            print(f"  Несуществующая пара: {no_cache} (ожидалось None)")
            assert no_cache is None

            storage.save(results, TEST_DB)

            cached = storage.get_cached_label("https://ivan-petrov.ru", "Ivan Petrov", TEST_DB)
            print(f"  Существующая пара: {cached} (ожидалось 'positive')")
            assert cached == "positive"

            other_query = storage.get_cached_label("https://ivan-petrov.ru", "Other Person", TEST_DB)
            print(f"  Тот же URL, другой query: {other_query} (ожидалось None)")
            assert other_query is None

            print("\n✓ Кэш работает корректно по паре (url + query)")

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)
        print("Тестовая БД удалена: %s" % TEST_DB)
    print("\n=== Тест завершён ===")
