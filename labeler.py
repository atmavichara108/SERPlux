import os
import re
import time
import logging
from dotenv import load_dotenv
import requests

load_dotenv()

import config
import storage

log = logging.getLogger(__name__)

LLM_PAUSE = 1  # секунд между вызовами LLM

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


def _normalize_provider_chain(provider_chain: str | list[str] | None) -> list[str] | None:
    """Превращает provider_chain в список id: строку через запятую или list[str]."""
    if provider_chain is None:
        return None
    if isinstance(provider_chain, str):
        ids = [p.strip() for p in provider_chain.split(",") if p.strip()]
        return ids if ids else None
    if isinstance(provider_chain, list):
        return [p.strip() for p in provider_chain if isinstance(p, str) and p.strip()]
    return None


def _get_provider_chain(provider_chain: str | list[str] | None = None) -> list[tuple[str, dict]]:
    """Возвращает список (provider_id, config) включённых провайдеров,
    отсортированный по priority. provider_chain позволяет переопределить набор id."""
    explicit_ids = _normalize_provider_chain(provider_chain)

    chain: list[tuple[str, dict]] = []
    for pid, cfg in config.PROVIDERS.items():
        if not cfg.get("enabled", False):
            continue
        if explicit_ids is not None and pid not in explicit_ids:
            continue
        chain.append((pid, cfg))

    # Если передана explicit цепочка — сохраняем её порядок; иначе сортируем по priority
    if explicit_ids is not None:
        order = {pid: idx for idx, pid in enumerate(explicit_ids)}
        chain.sort(key=lambda x: order.get(x[0], 999))
    else:
        chain.sort(key=lambda x: x[1].get("priority", 999))
    return chain


def _call_provider(provider_id: str, provider_cfg: dict, prompt: str) -> str | None:
    """Вызывает LLM-провайдера по его конфигу. Возвращает сырой ответ или None."""
    api_key = os.environ.get(provider_cfg["api_key_env_var"])
    if not api_key:
        log.warning("%s: %s не задан", provider_id, provider_cfg["api_key_env_var"])
        return None
    try:
        resp = requests.post(
            provider_cfg["endpoint"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": provider_cfg["default_model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=(10, 60),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("%s ошибка: %s", provider_id, e)
        return None


def _label_one_llm(row: dict, provider_chain: str | list[str] | None = None) -> str | None:
    """Вызывает LLM для разметки по цепочке провайдеров
    (без проверки кэша — кэш проверяет label())."""
    prompt = _build_prompt(row["query"], row["url"], row.get("snippet", ""))
    chain = _get_provider_chain(provider_chain)
    for provider_id, provider_cfg in chain:
        raw = _call_provider(provider_id, provider_cfg, prompt)
        if raw is not None:
            lbl = _parse_label(raw)
            log.info("%s: %s + '%s' -> %s", provider_id, row["url"], row["query"], lbl)
            return lbl
    log.error("Все провайдеры недоступны для %s, sentiment=None", row["url"])
    return None


LABEL_MODES = {"domains", "snippets", "full"}


def _label_domain(row: dict, client_id: str, db_path: str) -> str | None:
    """Разметка по справочнику доменов. LLM не вызывается."""
    domain = row.get("domain")
    if not domain:
        return None
    found = storage.get_domain_label(client_id, domain, db_path)
    if found is None:
        return None
    sentiment = found["sentiment"]
    log.info("из справочника доменов: %s (client=%s) -> %s", domain, client_id, sentiment)
    return sentiment


def label(
    rows: list[dict],
    db_path: str = storage.DB_PATH,
    label_mode: str = "snippets",
    force_relabel: bool = False,
    client_id: str = "default",
    provider_chain: str | list[str] | None = None,
) -> list[dict]:
    """
    Проставляет sentiment (и алиас label), а также confidence каждой строке.

    Параметры:
      - label_mode: "domains" | "snippets" | "full"
      - force_relabel: если True — игнорировать кэш, размечать заново
      - client_id: идентификатор клиента для справочника доменов
      - provider_chain: переопределение цепочки провайдеров (id через запятую или list)

    Режимы:
      - domains: справочник domain_labels, без LLM.
      - snippets: кэш (url+query) → LLM по сниппету.
      - full: заглушка, sentiment=None.
    """
    result = []
    last_real_call = 0.0

    if label_mode not in LABEL_MODES:
        log.warning("Неизвестный режим разметки '%s'", label_mode)

    for row in rows:
        # Пробрасываем режим и клиента в каждую строку для последующего insert_labels
        row["label_mode"] = label_mode
        row["client_id"] = client_id
        row["confidence"] = "high"

        if label_mode == "domains":
            sentiment = _label_domain(row, client_id, db_path)
            row["sentiment"] = sentiment
            row["label"] = sentiment  # алиас для обратной совместимости
            result.append(row)
            continue

        if label_mode == "full":
            # Заглушка: полный текст страницы — отдельная задача (v2).
            row["sentiment"] = None
            row["label"] = None
            result.append(row)
            continue

        # --- режим snippets ---
        # Проверяем кэш, если не force_relabel
        if not force_relabel:
            cached = storage.get_cached_label(row["url"], row["query"], db_path)
            if cached is not None:
                row["sentiment"] = cached
                row["label"] = cached  # алиас для обратной совместимости
                log.info("из кэша: %s + '%s' -> %s", row["url"], row["query"], cached)
                result.append(row)
                continue

        # Пауза только между реальными вызовами LLM
        now = time.time()
        elapsed = now - last_real_call
        if elapsed < LLM_PAUSE and last_real_call > 0:
            wait = LLM_PAUSE - elapsed
            log.debug("Пауза %.1fс между вызовами LLM", wait)
            time.sleep(wait)

        sentiment = _label_one_llm(row, provider_chain=provider_chain)
        row["sentiment"] = sentiment
        row["label"] = sentiment  # алиас
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
        print(f"    sentiment: {row['sentiment']}")
        print(f"    label (alias): {row['label']}")
        print()

    # Сохраняем сырые данные и метки отдельно (как в пайплайне main.py)
    storage.save(results, TEST_DB)
    storage.insert_labels(results, TEST_DB)

    print("=== Тест кэша ===")
    no_cache = storage.get_cached_label("https://unknown.com", "unknown query", TEST_DB)
    print(f"  Несуществующая пара: {no_cache} (ожидалось None)")
    assert no_cache is None

    cached = storage.get_cached_label("https://ivan-petrov.ru", "Ivan Petrov", TEST_DB)
    print(f"  Существующая пара: {cached} (ожидалось 'positive')")
    assert cached == "positive"

    other_query = storage.get_cached_label("https://ivan-petrov.ru", "Other Person", TEST_DB)
    print(f"  Тот же URL, другой query: {other_query} (ожидалось None)")
    assert other_query is None

    print("\n✓ Кэш работает корректно по паре (url + query)")

    print("\n=== Тест force_relabel ===")
    relabeled = label(fake_rows, TEST_DB, force_relabel=True)
    for row in relabeled:
        print(f"  {row['url']}: sentiment={row['sentiment']}")

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)
        print("Тестовая БД удалена: %s" % TEST_DB)
    print("\n=== Тест завершён ===")
