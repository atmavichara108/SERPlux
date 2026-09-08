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


def report_etalon_coverage(rows: list[dict], db_path: str = storage.DB_PATH) -> dict:
    """Логирует покрытие выдачи ручным эталоном по уникальным (domain, query)."""
    keys = {
        (storage.normalize_domain(row.get("url", "")), storage.normalize_query(row.get("query", "")))
        for row in rows
    }
    keys.discard(("", ""))
    # Покрытие считается только по жёсткому эталону manual_l1:
    # legacy-записи snippet/page эталоном не являются (v1.1 workstream A).
    labels = {}
    for key in keys:
        rec = storage.get_domain_label_record(key[0], key[1], db_path)
        labels[key] = rec.get("sentiment") if rec is not None and rec.get("source") == "manual_l1" else None
    matched = sum(value is not None for value in labels.values())
    for domain, query in sorted(keys):
        if labels[(domain, query)] is None:
            log.info("НЕТ ЭТАЛОНА: domain=%s query=%s", domain, query)
    total = len(keys)
    return {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "coverage_pct": round(matched * 100 / total, 2) if total else 0,
    }


def _build_prompt(query: str, url: str, snippet: str) -> str:
    """Строит промпт для LLM с few-shot примерами и чёткими критериями."""
    return (
        f"Ты — аналитик репутации. Твоя задача — оценить, как ссылка в поисковой выдаче влияет на репутацию субъекта.\n"
        f"\n"
        f"СУБЪЕКТ: '{query}'\n"
        f"URL: {url}\n"
        f"СНИППЕТ: {snippet}\n"
        f"\n"
        f"КРИТЕРИИ ОЦЕНКИ:\n"
        f"\n"
        f"POSITIVE — ссылка выгодна субъекту:\n"
        f"  • Официальный сайт субъекта или его подразделений\n"
        f"  • Позитивные новости, достижения, награды\n"
        f"  • Социальные сети субъекта (LinkedIn, Facebook, Instagram и т.д.)\n"
        f"  • Благотворительность, спонсорство, позитивные инициативы\n"
        f"  • Профессиональные профили, резюме, портфолио\n"
        f"\n"
        f"NEGATIVE — ссылка вредит репутации:\n"
        f"  • Компромат, скандалы, расследования\n"
        f"  • Санкции, судебные дела, обвинения\n"
        f"  • Отмывание денег, коррупция, мошенничество\n"
        f"  • Негативные отзывы клиентов/партнёров\n"
        f"  • Критика в СМИ, разоблачения\n"
        f"\n"
        f"NEUTRAL — нейтральное упоминание:\n"
        f"  • Каталоги, справочники, реестры компаний\n"
        f"  • Отзывы без явной оценки (просто факты)\n"
        f"  • Упоминание в списках, рейтингах без контекста\n"
        f"  • Википедия, биографические данные без оценки\n"
        f"  • Пустой или нерелевантный сниппет\n"
        f"\n"
        f"ПРИМЕРЫ:\n"
        f"\n"
        f"Пример 1:\n"
        f"  Субъект: 'Ivan Petrov'\n"
        f"  URL: https://ivan-petrov.ru\n"
        f"  Сниппет: Официальный сайт Ивана Петрова. Услуги, контакты, биография\n"
        f"  Ответ: positive\n"
        f"\n"
        f"Пример 2:\n"
        f"  Субъект: 'Ivan Petrov'\n"
        f"  URL: https://sanctions-list.example/ivan-petrov\n"
        f"  Сниппет: Ivan Petrov включён в список санкций за отмывание денег\n"
        f"  Ответ: negative\n"
        f"\n"
        f"Пример 3:\n"
        f"  Субъект: 'Ivan Petrov'\n"
        f"  URL: https://spravka.example/person/ivan-petrov\n"
        f"  Сниппет: Карточка персоны: Ivan Petrov, дата рождения, адрес\n"
        f"  Ответ: neutral\n"
        f"\n"
        f"ИНСТРУКЦИЯ:\n"
        f"1. Проанализируй сниппет и URL в контексте субъекта '{query}'\n"
        f"2. Определи тональность по критериям выше\n"
        f"3. Ответь СТРОГО одним словом: positive, negative или neutral\n"
        f"4. НЕ добавляй пояснений, кавычек или дополнительного текста\n"
        f"\n"
        f"Ответ:"
    )


def _parse_label(raw: str | None) -> str | None:
    """Извлекает sentiment из LLM-ответа.

    v1.1 (workstream A): мусорный ответ → None (честный маркер
    категории invalid_or_unknown), а не ложный neutral.
    """
    match = LABEL_PATTERN.search(raw or "")
    if match:
        return match.group(1).lower()
    log.warning("LLM вернул мусор '%s' — метка не распознана", (raw or "").strip()[:80])
    return None


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


def _call_provider(provider_id: str, provider_cfg: dict, prompt: str, model: str | None = None) -> str | None:
    """
    Вызывает LLM-провайдера по его конфигу. Возвращает сырой ответ или None.

    При получении HTTP 429 или сетевой ошибке делает до 3 попыток
    с экспоненциальной задержкой (3с, 6с, 12с).
    """
    api_key = os.environ.get(provider_cfg["api_key_env_var"])
    if not api_key:
        log.warning("%s: %s не задан", provider_id, provider_cfg["api_key_env_var"])
        return None

    max_retries = 3
    base_delay = 3  # секунды

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                provider_cfg["endpoint"],
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or provider_cfg["default_model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=(10, 60),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            # 429 и 5xx — временные проблемы, делаем retry
            if status_code == 429 or (status_code is not None and status_code >= 500):
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "%s HTTP %s (попытка %s/%s), повтор через %ss",
                        provider_id, status_code, attempt, max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
            log.warning("%s HTTP ошибка: %s", provider_id, e)
            return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                log.warning(
                    "%s сетевая ошибка (попытка %s/%s), повтор через %ss: %s",
                    provider_id, attempt, max_retries, delay, e,
                )
                time.sleep(delay)
                continue
            log.warning("%s сетевая ошибка после %s попыток: %s", provider_id, max_retries, e)
            return None
        except Exception as e:
            # Ошибки парсинга/структуры ответа — не retry
            log.warning("%s ошибка: %s", provider_id, e)
            return None
    return None


def _label_one_llm(
    row: dict,
    provider_chain: str | list[str] | None = None,
    model: str | None = None,
    invalid_ref: list | None = None,
) -> str | None:
    """Вызывает LLM для разметки по цепочке провайдеров
    (без проверки кэша — кэш проверяет label()).

    invalid_ref: опциональный счётчик [n]; инкрементируется, если провайдер
    вернул нечитаемый ответ (v1.1: категория invalid_or_unknown).
    """
    prompt = _build_prompt(row["query"], row["url"], row.get("snippet", ""))
    chain = _get_provider_chain(provider_chain)
    for provider_id, provider_cfg in chain:
        raw = _call_provider(provider_id, provider_cfg, prompt, model=model)
        if raw is not None:
            lbl = _parse_label(raw)
            if lbl is None:
                # Мусорный ответ провайдера — учитываем как invalid,
                # наружу отдаём None (fallback neutral ставит группа)
                if invalid_ref is not None:
                    invalid_ref[0] += 1
                return None
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
    model: str | None = None,
) -> list[dict]:
    """
    Режим AUTO: эталон domain_labels → сниппет → neutral при ошибке.

    Логика:
      1. Проверяем эталон: get_domain_label(url, query) → берём manual_l1
      2. Нет в кэше → разметка по сниппету через LLM
      3. LLM не уверена (пустой сниппет / ошибка провайдера) → sentiment=neutral
      4. Результат LLM не записывается в domain_labels.
    """
    result = []
    searcher = group_rows[0].get("searcher") or "unknown"
    geo = group_rows[0].get("geo") or "unknown"

    invalid_ref = [0]  # счётчик мусорных ответов LLM (v1.1: invalid_or_unknown)
    stats = {
        "total": len(group_rows),
        "cache_hit": 0,      # Взяли из эталона domain_labels (manual_l1)
        "snippet_success": 0, # Разметили по сниппету успешно
        "snippet_fallback_neutral": 0,  # Сниппет пуст → neutral
        "provider_error": 0,  # Счётчик ошибок провайдера
        "invalid_llm": 0,     # Мусорный ответ LLM (v1.1)
        "invalid_key": 0,     # Пустой domain/query (v1.1)
    }

    log.info("AUTO: разметка группы searcher=%s geo=%s строк=%s",
             searcher, geo, len(group_rows))

    for row in group_rows:
        row["label_mode"] = "auto"
        row["client_id"] = client_id
        # confidence будет переопределён ниже: high для успешного LLM,
        # uncertain для пустого сниппета / ошибки провайдера
        row["confidence"] = "high"

        url = row.get("url") or ""
        domain = storage.normalize_domain(url)
        query = row.get("query") or ""
        snippet = row.get("snippet", "")

        log.info("AUTO: url=%s query='%s' geo=%s — etalon lookup", url, query, geo)

        # Шаг 1: Проверяем эталон (domain, query), geo не участвует в ключе.
        # manual_l1 — жёсткий референс: выигрывает всегда, force_relabel его
        # не обходит (v1.1 precedence rule 5); force_relabel сбрасывает только
        # автоматический кэш, которого в auto-режиме больше нет.
        record = None
        if domain and query:
            record = storage.get_domain_label_record(url, query, db_path)
        if record is not None and record.get("source") == "manual_l1" and record.get("sentiment"):
            row["sentiment"] = record["sentiment"]
            row["label"] = record["sentiment"]
            row["confidence"] = "high"
            row["label_source"] = "manual_l1"
            stats["cache_hit"] += 1
            log.info("AUTO etalon HIT (manual_l1): url=%s -> %s", url, record["sentiment"])
            result.append(row)
            continue

        log.info("AUTO etalon MISS: url=%s", url)

        # Шаг 1b: Невалидный ключ — не гоняем строку в LLM
        # (v1.1: категория invalid_or_unknown).
        if not domain or not query:
            log.warning("AUTO invalid key: url=%r query=%r -> neutral (uncertain)", url, query)
            row["sentiment"] = "neutral"
            row["label"] = "neutral"
            row["confidence"] = "uncertain"
            row["label_source"] = "fallback_invalid_key"
            stats["invalid_key"] += 1
            result.append(row)
            continue

        # Шаг 2: Если сниппет пуст — ставим neutral (маркер неуверенности)
        if not snippet or not snippet.strip():
            log.warning("AUTO empty snippet: url=%s query='%s' -> neutral (uncertain)",
                        url, query)
            row["sentiment"] = "neutral"
            row["label"] = "neutral"
            row["confidence"] = "uncertain"
            row["label_source"] = "fallback_empty_snippet"
            stats["snippet_fallback_neutral"] += 1
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

        invalid_before = invalid_ref[0]
        sentiment = _label_one_llm(
            row, provider_chain=provider_chain, model=model, invalid_ref=invalid_ref,
        )

        if sentiment is None:
            sentiment = "neutral"
            row["confidence"] = "uncertain"
            if invalid_ref[0] > invalid_before:
                # Мусорный ответ провайдера — invalid_or_unknown, не provider_error
                log.warning("AUTO invalid LLM answer: url=%s query='%s' -> neutral (uncertain)",
                            url, query)
                row["label_source"] = "fallback_invalid_llm"
                stats["invalid_llm"] += 1
            else:
                log.warning("AUTO provider ERROR: url=%s query='%s' -> neutral (uncertain)",
                            url, query)
                row["label_source"] = "fallback_provider_error"
                stats["provider_error"] += 1
        else:
            stats["snippet_success"] += 1
            row["label_source"] = "llm"
            log.info("AUTO LLM: url=%s query='%s' -> %s", url, query, sentiment)

        row["sentiment"] = sentiment
        row["label"] = sentiment
        last_real_call_ref[0] = time.time()

        # Авторазметка не является эталоном и никогда не записывается в domain_labels.

        result.append(row)

    log.info(
        "AUTO searcher=%s geo=%s: total=%s cache_hit=%s snippet_success=%s "
        "snippet_fallback_neutral=%s provider_error=%s invalid_llm=%s invalid_key=%s",
        searcher, geo, stats["total"], stats["cache_hit"], stats["snippet_success"],
        stats["snippet_fallback_neutral"], stats["provider_error"],
        stats["invalid_llm"], stats["invalid_key"],
    )
    return result


def _label_group_deep(
    group_rows: list[dict],
    client_id: str,
    db_path: str,
    provider_chain: str | list[str] | None,
    last_real_call_ref: list[float],
    model: str | None = None,
) -> list[dict]:
    """
    Режим DEEP: разметка по контенту страницы, только для neutral.
    
    Логика:
      1. Отбираем только строки с sentiment='neutral'
      2. Заходим на страницу (URL), размечаем по контенту
      3. positive/negative НЕ трогаем
      4. Результат не записывается в domain_labels.
      
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
            query = row.get("query") or ""
            url = row.get("url")
            # TODO: Заходим на страницу по URL, размечаем по контенту
            # Пока это заглушка — оставляем neutral
            log.debug("DEEP: neutral URL=%s ждёт разметки по контенту (заглушка)", url)
            # sentiment остаётся "neutral"
            # После реализации контент-разметки:
            # sentiment = _label_by_page_content(url, query, provider_chain)
            stats["page_relabeled"] += 1
        
        row["label"] = sentiment
        result.append(row)

    log.info(
        "DEEP searcher=%s geo=%s: total=%s neutral_found=%s page_relabeled=%s untouched=%s",
        searcher, geo, stats["total"], stats["neutral_found"], stats["page_relabeled"], stats["untouched"]
    )
    return result


def _validate_labels(
    rows: list[dict],
    run_id: str | None,
    db_path: str,
    out: dict | None = None,
) -> dict:
    """
    Post-label валидатор (v1.1 workstream A).

    Для каждой строки детерминированно сравнивает produced sentiment с ручным
    эталоном manual_l1 по ключу (domain, query) и классифицирует neutral.
    Категории журнала label_conflicts:
      - manual_neutral: эталон явно говорит neutral;
      - unmatched_neutral: эталона нет, fallback/LLM дал neutral;
      - manual_conflict: результат ≠ manual_l1 (строка исправляется на эталон,
        конфликт фиксируется в журнале — не молча);
      - invalid_or_unknown: пустой ключ/URL или мусорный ответ LLM.
    """
    counts = {
        "total": len(rows),
        "ok": 0,
        "manual_neutral": 0,
        "unmatched_neutral": 0,
        "manual_conflict": 0,
        "invalid_or_unknown": 0,
        "unlabeled": 0,
    }
    records: list[dict] = []

    for row in rows:
        url = row.get("url") or ""
        query = row.get("query") or ""
        domain = storage.normalize_domain(url)
        qnorm = storage.normalize_query(query)
        observed = row.get("sentiment")
        confidence = row.get("confidence") or "high"
        source = row.get("label_source") or ""

        manual = None
        if domain and qnorm:
            manual = storage.get_domain_label_record(url, query, db_path)
        manual_sentiment = None
        if manual is not None and manual.get("source") == "manual_l1":
            manual_sentiment = manual.get("sentiment")

        base = {
            "run_id": run_id,
            "domain": domain,
            "url": url,
            "query": qnorm or query,
            "geo": row.get("geo"),
            "searcher": row.get("searcher"),
            "position": row.get("position"),
            "observed_label": observed,
            "manual_label": manual_sentiment,
            "source": source,
            "confidence": confidence,
        }

        if not domain or not qnorm:
            counts["invalid_or_unknown"] += 1
            records.append({**base, "conflict_type": "invalid_or_unknown",
                            "recommended_action": "fix_input"})
            continue

        if observed not in ("positive", "negative", "neutral"):
            if observed is None:
                # Строка без попытки разметки (deep pass-through) — считаем
                # отдельно, чтобы сумма категорий сходилась с total
                counts["unlabeled"] += 1
            else:
                counts["invalid_or_unknown"] += 1
                records.append({**base, "conflict_type": "invalid_or_unknown",
                                "recommended_action": "fix_input"})
            continue

        if manual_sentiment is not None:
            if observed != manual_sentiment:
                counts["manual_conflict"] += 1
                records.append({**base, "conflict_type": "manual_conflict",
                                "recommended_action": "resolve_conflict"})
                # Precedence rule 1: manual_l1 выигрывает; правка не молча —
                # конфликт зафиксирован в журнале выше.
                row["sentiment"] = manual_sentiment
                row["label"] = manual_sentiment
                row["confidence"] = "high"
                row["label_source"] = "manual_l1"
            elif observed == "neutral":
                counts["manual_neutral"] += 1
                records.append({**base, "conflict_type": "manual_neutral",
                                "recommended_action": "none"})
            else:
                counts["ok"] += 1
        elif observed == "neutral":
            if source == "fallback_invalid_llm":
                counts["invalid_or_unknown"] += 1
                records.append({**base, "conflict_type": "invalid_or_unknown",
                                "recommended_action": "fix_input"})
            else:
                counts["unmatched_neutral"] += 1
                records.append({**base, "conflict_type": "unmatched_neutral",
                                "recommended_action": "review_snippet"})
        else:
            counts["ok"] += 1

    if records:
        storage.save_label_conflicts(records, db_path)
        # Ретеншн только для прогонных записей: у прямых вызовов label()
        # без run_id журнал не чистим (dev/отладка). Ретеншн некритичен —
        # сбой prune не должен валить разметку (частичный сбой = логируем).
        if run_id:
            try:
                storage.prune_label_conflicts(keep_last_runs=50, db_path=db_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("prune_label_conflicts не выполнен: %s", exc)

    log.info(
        "VALIDATION: total=%s ok=%s manual_neutral=%s unmatched_neutral=%s "
        "manual_conflict=%s invalid_or_unknown=%s unlabeled=%s recorded=%s",
        counts["total"], counts["ok"], counts["manual_neutral"],
        counts["unmatched_neutral"], counts["manual_conflict"],
        counts["invalid_or_unknown"], counts["unlabeled"], len(records),
    )
    if out is not None:
        out.clear()
        out.update(counts)
        out["recorded"] = len(records)
    return counts


def label(
    rows: list[dict],
    db_path: str = storage.DB_PATH,
    label_mode: str = "auto",
    force_relabel: bool = False,
    client_id: str = "default",
    provider_chain: str | list[str] | None = None,
    model: str | None = None,
    run_id: str | None = None,
    validation_out: dict | None = None,
    stats_out: dict | None = None,
) -> list[dict]:
    """
    Проставляет sentiment (и алиас label) каждой строке.

    Параметры:
      - label_mode: "auto" (дефолт) | "deep"
      - force_relabel: сбрасывает автоматический кэш; manual_l1 эталон
        НЕ обходит (v1.1 precedence rule 5)
      - client_id: идентификатор клиента для positions/labels
      - provider_chain: переопределение цепочки провайдеров (id через запятую или list)
      - model: конкретная модель LLM (override default_model провайдера)
      - run_id: идентификатор прогона для журнала валидации (v1.1)
      - validation_out: мутабельный dict; заполняется счётчиками валидации
        {total, ok, manual_neutral, unmatched_neutral, manual_conflict,
        invalid_or_unknown, unlabeled, recorded}
      - stats_out: опциональный мутабельный dict (v1.0.3); заполняется
        breakdown разметки по label_source:
        {etalon_hit, llm_success, fallback_empty_snippet, fallback_provider_error,
        fallback_invalid_llm, fallback_invalid_key, invalid_key, total}
        Контракт label() → list[rows] не меняется; при stats_out=None поведение
        прежнее (breakdown только в логах).

    Режимы:
      - auto: get_domain_label_record (manual_l1) → LLM (snippet) → neutral (fallback on error).
              Результат LLM не записывается в domain_labels.
      - deep: обрабатывает только строки с sentiment='neutral',
              размечает по контенту страницы (URL).
              Результат page не записывается в domain_labels.

    После разметки выполняется post-label валидация (_validate_labels):
    сравнение с эталоном, классификация neutral/конфликтов, запись журнала
    label_conflicts, исправление строк на эталон при manual_conflict.
    """
    if label_mode not in LABEL_MODES:
        log.warning("Неизвестный режим разметки '%s', используем 'auto'", label_mode)
        label_mode = "auto"

    if label_mode == "auto":
        report_etalon_coverage(rows, db_path)

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
                model=model,
            ))
        elif label_mode == "deep":
            result.extend(_label_group_deep(
                group_rows,
                client_id=client_id,
                db_path=db_path,
                provider_chain=provider_chain,
                last_real_call_ref=last_real_call_ref,
                model=model,
            ))

    total_success = sum(1 for r in result if r.get("sentiment") is not None)
    log.info("Разметка завершена: %s/%s строк с sentiment", total_success, len(rows))

    # v1.0.3: breakdown разметки по label_source — наружу для stats прогона.
    # Источник истины — row["label_source"], проставленный _label_group_auto.
    if stats_out is not None:
        breakdown = {
            "etalon_hit": 0,
            "llm_success": 0,
            "fallback_empty_snippet": 0,
            "fallback_provider_error": 0,
            "fallback_invalid_llm": 0,
            "fallback_invalid_key": 0,
            "invalid_key": 0,
            "total": len(result),
        }
        for r in result:
            src = r.get("label_source")
            if src == "manual_l1":
                breakdown["etalon_hit"] += 1
            elif src == "llm":
                breakdown["llm_success"] += 1
            elif src in breakdown:
                breakdown[src] += 1
        stats_out.clear()
        stats_out.update(breakdown)
        log.info("LABELING BREAKDOWN: %s", breakdown)

    # Post-label валидация (v1.1 workstream A): сравнение с эталоном,
    # классификация neutral/конфликтов, журнал label_conflicts.
    _validate_labels(result, run_id=run_id, db_path=db_path, out=validation_out)
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

    print("=== Тест labeler.py (режим AUTO, мок LLM, изолированная БД: %s) ===\n" % TEST_DB)

    # Подменяем LLM на детерминированный мок, чтобы не расходовать токены в примере
    def _fake_label_one_llm(row, provider_chain=None, model=None, invalid_ref=None):
        url = row.get("url", "")
        if "ivan-petrov.ru" in url:
            return "positive"
        if "sanctions" in url:
            return "negative"
        return "neutral"

    _label_one_llm_real = _label_one_llm
    # Подмена имени в глобальном пространстве __main__ (при запуске как скрипт
    # import labeler as _labeler_mod создаёт дубль модуля, поэтому globals()).
    globals()["_label_one_llm"] = _fake_label_one_llm

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

    print("=== Тест эталона domain_labels (домен) ===")
    # Вставляем тестовую метку в domain_labels.
    storage.upsert_domain_label("https://test-domain.com/path", "test query", sentiment="positive", source="manual_l1", db_path=TEST_DB)
    cached = storage.get_domain_label("https://test-domain.com/path/", "test query", TEST_DB)
    print(f"  Вставленная метка: {cached} (ожидалось 'positive')")
    assert cached == "positive"

    # Проверяем, что manual_l1 не перезаписывается
    storage.upsert_domain_label("https://test-domain.com/path", "test query", sentiment="negative", source="snippet", db_path=TEST_DB)
    cached = storage.get_domain_label("https://test-domain.com/path/", "test query", TEST_DB)
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

    # Восстанавливаем реальный LLM-вызов
    globals()["_label_one_llm"] = _label_one_llm_real

    if _os.path.exists(TEST_DB):
        _os.remove(TEST_DB)
        print("\nТестовая БД удалена: %s" % TEST_DB)
    print("\n=== Тест завершён ===")
