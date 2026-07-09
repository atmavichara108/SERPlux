import os
import re
import time
from dotenv import load_dotenv
import requests

load_dotenv()

import config
import storage

log = config.setup_logging(__name__)

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
    """Извлекает sentiment из LLM-ответа, fallback на neutral."""
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
    # Все провайдеры недоступны — возвращаем None, не neutral
    log.error("Все провайдеры недоступны для %s", row["url"])
    return None


LABEL_MODES = {"auto", "deep"}


def _label_group_auto(
    group_rows: list[dict],
    force_relabel: bool,
    client_id: str,
    db_path: str,
    provider_chain: str | list[str] | None,
    last_real_call_ref: list[float],
) -> list[dict]:
    """
    Режим AUTO: кэш domain_labels → сниппет → neutral при ошибке.
    
    Логика:
      1. Проверяем кэш: get_domain_label(domain, query, geo) → берём из справочника
      2. Нет в кэше → разметка по сниппету через LLM
      3. LLM не уверена (пустой сниппет / ошибка провайдера) → sentiment=neutral, source=snippet
      4. Результат → upsert domain_labels (source='snippet'), уважая manual_l1
    """
    result = []
    searcher = group_rows[0].get("searcher") or "unknown"
    geo = group_rows[0].get("geo") or "unknown"

    stats = {
        "total": len(group_rows),
        "cache_hit": 0,      # Взяли из кэша domain_labels
        "snippet_success": 0, # Разметили по сниппету успешно
        "snippet_fallback_neutral": 0,  # Сниппет пуст или LLM ошибка → neutral
        "provider_error": 0,  # Счётчик ошибок провайдера
    }

    log.info("AUTO: разметка группы searcher=%s geo=%s строк=%s",
             searcher, geo, len(group_rows))

    for row in group_rows:
        row["label_mode"] = "auto"
        row["client_id"] = client_id
        row["confidence"] = "high"

        domain = row.get("domain")
        query = row.get("query") or ""
        snippet = row.get("snippet", "")

        # Шаг 1: Проверяем кэш domain_labels (domain, query, geo)
        if domain and not force_relabel:
            cached_sentiment = storage.get_domain_label(domain, query, geo, db_path)
            if cached_sentiment is not None:
                row["sentiment"] = cached_sentiment
                row["label"] = cached_sentiment
                stats["cache_hit"] += 1
                log.debug("AUTO кэш-хит: %s/%s/%s -> %s", domain, query, geo, cached_sentiment)
                result.append(row)
                continue

        # Шаг 2: Если сниппет пуст — ставим neutral
        if not snippet or not snippet.strip():
            log.warning("AUTO: пустой сниппет для url=%s query='%s', ставлю neutral",
                        row.get("url", "—"), query)
            row["sentiment"] = "neutral"
            row["label"] = "neutral"
            stats["snippet_fallback_neutral"] += 1
            # Сохраняем в кэш (источник snippet — нейтральный фоллбэк)
            if domain:
                storage.upsert_domain_label(domain, query, geo, "neutral", "snippet", db_path)
            result.append(row)
            continue

        # Шаг 3: Разметка по сниппету через LLM
        # Пауза между реальными вызовами
        now = time.time()
        last_real_call = last_real_call_ref[0]
        elapsed = now - last_real_call
        if elapsed < LLM_PAUSE and last_real_call > 0:
            wait = LLM_PAUSE - elapsed
            log.debug("AUTO: пауза %.1fс между вызовами LLM", wait)
            time.sleep(wait)

        sentiment = _label_one_llm(row, provider_chain=provider_chain)
        
        # Если LLM не ответила или ошибка провайдера
        if sentiment is None:
            log.warning("AUTO: ошибка провайдера для url=%s query='%s', ставлю neutral",
                        row.get("url", "—"), query)
            sentiment = "neutral"
            stats["provider_error"] += 1
        else:
            stats["snippet_success"] += 1

        row["sentiment"] = sentiment
        row["label"] = sentiment
        last_real_call_ref[0] = time.time()
        
        # Сохраняем в кэш domain_labels
        if domain:
            storage.upsert_domain_label(domain, query, geo, sentiment, "snippet", db_path)
        
        result.append(row)

    log.info(
        "AUTO searcher=%s geo=%s: total=%s cache_hit=%s snippet_success=%s "
        "snippet_fallback_neutral=%s provider_error=%s",
        searcher, geo, stats["total"], stats["cache_hit"], stats["snippet_success"],
        stats["snippet_fallback_neutral"], stats["provider_error"]
    )
    return result


def _label_group_deep(
    group_rows: list[dict],
    client_id: str,
    db_path: str,
    provider_chain: str | list[str] | None,
    last_real_call_ref: list[float],
) -> list[dict]:
    """
    Режим DEEP: разметка по контенту страницы, только для neutral.
    
    Логика:
      1. Отбираем только строки с sentiment='neutral'
      2. Заходим на страницу (URL), размечаем по контенту
      3. positive/negative НЕ трогаем
      4. Результат → upsert domain_labels source='page', уважая manual_l1
      
    На текущем этапе: заглушка (заполнить контентом в v2).
    """
    result = []
    searcher = group_rows[0].get("searcher") or "unknown"
    geo = group_rows[0].get("geo") or "unknown"

    stats = {
        "total": len(group_rows),
        "neutral_found": 0,
        "page_relabeled": 0,
        "untouched": 0,
    }

    log.info("DEEP: разметка группы searcher=%s geo=%s строк=%s",
             searcher, geo, len(group_rows))

    for row in group_rows:
        row["label_mode"] = "deep"
        row["client_id"] = client_id

        sentiment = row.get("sentiment")
        
        # Пропускаем, если уже positive или negative
        if sentiment in ("positive", "negative"):
            stats["untouched"] += 1
            row["label"] = sentiment
            result.append(row)
            continue

        # Обрабатываем только neutral
        if sentiment == "neutral":
            stats["neutral_found"] += 1
            domain = row.get("domain")
            query = row.get("query") or ""
            url = row.get("url")
            
            # TODO: Заходим на страницу по URL, размечаем по контенту
            # Пока это заглушка — оставляем neutral
            log.debug("DEEP: neutral URL=%s ждёт разметки по контенту (заглушка)", url)
            # sentiment остаётся "neutral"
            # После реализации контент-разметки:
            # sentiment = _label_by_page_content(url, query, provider_chain)
            # storage.upsert_domain_label(domain, query, geo, sentiment, "page", db_path)
            stats["page_relabeled"] += 1
        
        row["label"] = sentiment
        result.append(row)

    log.info(
        "DEEP searcher=%s geo=%s: total=%s neutral_found=%s page_relabeled=%s untouched=%s",
        searcher, geo, stats["total"], stats["neutral_found"], stats["page_relabeled"], stats["untouched"]
    )
    return result


def label(
    rows: list[dict],
    db_path: str = storage.DB_PATH,
    label_mode: str = "auto",
    force_relabel: bool = False,
    client_id: str = "default",
    provider_chain: str | list[str] | None = None,
) -> list[dict]:
    """
    Проставляет sentiment (и алиас label) каждой строке.

    Параметры:
      - label_mode: "auto" (дефолт) | "deep"
      - force_relabel: если True — игнорировать кэш, размечать заново
      - client_id: идентификатор клиента для domain_labels
      - provider_chain: переопределение цепочки провайдеров (id через запятую или list)

    Режимы:
      - auto: get_domain_label (cache) → LLM (snippet) → neutral (fallback on error).
              Результат → upsert domain_labels (source='snippet').
      - deep: обрабатывает только строки с sentiment='neutral',
              размечает по контенту страницы (URL).
              Результат → upsert domain_labels (source='page').
    """
    if label_mode not in LABEL_MODES:
        log.warning("Неизвестный режим разметки '%s', используем 'auto'", label_mode)
        label_mode = "auto"

    # Группируем по searcher×geo для структурного логирования
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row.get("searcher") or "unknown", row.get("geo") or "unknown")
        groups.setdefault(key, []).append(row)

    log.info("Начало разметки: %s строк, mode=%s, групп=%s", len(rows), label_mode, len(groups))

    result = []
    last_real_call_ref = [0.0]
    
    for (searcher, geo), group_rows in sorted(groups.items()):
        if label_mode == "auto":
            result.extend(_label_group_auto(
                group_rows,
                force_relabel=force_relabel,
                client_id=client_id,
                db_path=db_path,
                provider_chain=provider_chain,
                last_real_call_ref=last_real_call_ref,
            ))
        elif label_mode == "deep":
            result.extend(_label_group_deep(
                group_rows,
                client_id=client_id,
                db_path=db_path,
                provider_chain=provider_chain,
                last_real_call_ref=last_real_call_ref,
            ))

    total_success = sum(1 for r in result if r.get("sentiment") is not None)
    log.info("Разметка завершена: %s/%s строк с sentiment", total_success, len(rows))
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

    print("=== Тест labeler.py (режим AUTO, РЕАЛЬНЫЙ Zen, изолированная БД: %s) ===\n" % TEST_DB)

    results = label(fake_rows, TEST_DB, label_mode="auto")

    print("Результаты разметки (AUTO):")
    for row in results:
        print(f"  {row['url']}")
        print(f"    query: {row['query']}")
        print(f"    sentiment: {row['sentiment']}")
        print(f"    label (alias): {row['label']}")
        print()

    # Сохраняем сырые данные и метки отдельно (как в пайплайне main.py)
    storage.save(results, TEST_DB)
    storage.insert_labels(results, TEST_DB)

    print("=== Тест кэша domain_labels ===")
    # Вставляем тестовую метку в domain_labels
    storage.upsert_domain_label("test-domain.com", "test query", "TestGeo", "positive", "manual_l1", TEST_DB)
    cached = storage.get_domain_label("test-domain.com", "test query", "TestGeo", TEST_DB)
    print(f"  Вставленная метка: {cached} (ожидалось 'positive')")
    assert cached == "positive"

    # Проверяем, что manual_l1 не перезаписывается
    storage.upsert_domain_label("test-domain.com", "test query", "TestGeo", "negative", "snippet", TEST_DB)
    cached = storage.get_domain_label("test-domain.com", "test query", "TestGeo", TEST_DB)
    print(f"  После попытки перезаписать snippet: {cached} (ожидалось 'positive')")
    assert cached == "positive", "manual_l1 был перезаписан!"

    print("\n✓ Кэш domain_labels работает корректно, приоритет manual_l1 соблюдён")

    print("\n=== Тест force_relabel ===")
    relabeled = label(fake_rows, TEST_DB, label_mode="auto", force_relabel=True)
    for row in relabeled:
        print(f"  {row['url']}: sentiment={row['sentiment']}")

    print("\n=== Тест режима DEEP ===")
    # Предварительно размечаем в AUTO, потом пробуем DEEP
    auto_results = label(fake_rows, TEST_DB, label_mode="auto")
    deep_results = label(auto_results, TEST_DB, label_mode="deep")
    for row in deep_results:
        print(f"  {row['url']}: sentiment={row['sentiment']} (mode={row.get('label_mode')})")

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)
        print("\nТестовая БД удалена: %s" % TEST_DB)
    print("\n=== Тест завершён ===")
