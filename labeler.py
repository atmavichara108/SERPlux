import os
import re
import time
import logging
from dotenv import load_dotenv
import google.generativeai as genai
import requests

import storage

log = logging.getLogger(__name__)

# Провайдеры: порядок попыток. В будущем — выбор в интерфейсе serplux.
PROVIDER_CHAIN = ["zen", "gemini"]

ZEN_MODEL = "qwen3.6-plus"
ZEN_ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"

GEMINI_MODEL = "gemini-2.0-flash"

LLM_PAUSE = 1  # секунд между вызовами (Zen rate limit мягче Gemini)

LABEL_PATTERN = re.compile(r"\b(positive|negative|neutral)\b", re.IGNORECASE)


def _build_prompt(query: str, url: str, snippet: str) -> str:
    return (
        f"Ты анализируешь репутацию субъекта '{query}' в поисковой выдаче.\n"
        f"URL: {url}\n"
        f"Сниппет: {snippet}\n"
        f"Оцени, как эта ссылка влияет на репутацию субъекта. Ответь ОДНИМ словом:\n"
        f"positive — ссылка выгодна субъекту (офиц. сайт, позитивное упоминание, соцсети субъекта),\n"
        f"negative — вредит репутации (компромат, санкции, отмывание, скандал, негатив),\n"
        f"neutral — нейтральное упоминание (каталоги, справочники, отзывы без оценки).\n"
        f"Ответь СТРОГО одним словом без пояснений: positive, negative или neutral."
    )


def _parse_label(raw: str) -> str:
    match = LABEL_PATTERN.search(raw)
    if match:
        return match.group(1).lower()
    log.warning("LLM вернул мусор '%s', ставлю neutral", raw.strip()[:80])
    return "neutral"


def _call_zen(prompt: str) -> str | None:
    api_key = os.environ.get("OPENCODE_API_KEY")
    if not api_key:
        log.warning("OPENCODE_API_KEY не задан, Zen невозможен")
        return None
    try:
        resp = requests.post(
            ZEN_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": ZEN_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=(10, 60),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("Zen ошибка: %s", e)
        return None


def _call_gemini(prompt: str) -> str | None:
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            [prompt],
            generation_config={"temperature": 0.1, "max_output_tokens": 10},
        )
        return response.text
    except Exception as e:
        log.warning("Gemini ошибка: %s", e)
        return None


_PROVIDER_MAP = {
    "zen": _call_zen,
    "gemini": _call_gemini,
}


def _label_one(row: dict, db_path: str = storage.DB_PATH) -> str | None:
    cached = storage.get_cached_label(row["url"], row["query"], db_path)
    if cached is not None:
        log.info("из кэша: %s + '%s' -> %s", row["url"], row["query"], cached)
        return cached

    prompt = _build_prompt(row["query"], row["url"], row.get("snippet", ""))

    for provider_name in PROVIDER_CHAIN:
        call_fn = _PROVIDER_MAP[provider_name]
        raw = call_fn(prompt)
        if raw is not None:
            label = _parse_label(raw)
            log.info("%s: %s + '%s' -> %s", provider_name, row["url"], row["query"], label)
            return label

    log.error("Все провайдеры упали для %s, label=None", row["url"])
    return None


def label(rows: list[dict], db_path: str = storage.DB_PATH) -> list[dict]:
    load_dotenv()
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        genai.configure(api_key=gemini_key)

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

    print("=== Тест labeler.py (РЕАЛЬНЫЙ Zen, изолированная БД: %s) ===\n" % TEST_DB)

    results = label(fake_rows, TEST_DB)

    print("Результаты разметки:")
    for row in results:
        print(f"  {row['url']}")
        print(f"    query: {row['query']}")
        print(f"    label: {row['label']}")
        print()

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
