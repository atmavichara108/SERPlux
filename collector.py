import json
import logging
from datetime import date as date_type
from typing import Any

from topvisor import (
    PROJECT_ID,
    Row,
    get_snapshot,
    poll_status,
    run_check,
    snapshot_exists,
)

log = logging.getLogger(__name__)


def collect(config: dict[str, Any]) -> list[Row]:
    """
    Собирает снимки выдачи по всем связкам searcher×geo из config.

    config:
        depth: int — глубина проверки (зарезервировано)
        searchers: list[str] — ["google", "yandex_ru", "yandex_com"]
        geos: list[str] — ["Литва", "Германия", ...]

    Возвращает объединённый list[Row] по всем связкам.
    Частичный сбой: ошибка одной связки логируется, сбор продолжается.
    """
    depth = config.get("depth", 10)
    searchers = config.get("searchers", [])
    geos = config.get("geos", [])
    timeout_sec = config.get("timeout_sec", 900)

    with open("regions_map.json", "r", encoding="utf-8") as f:
        regions_map = json.load(f)

    filtered = [
        r for r in regions_map
        if r["searcher"] in searchers and r["geo_name"] in geos
    ]
    if not filtered:
        log.warning("Нет связок для searchers=%s, geos=%s", searchers, geos)
        return []

    log.info("Найдено %s связок для сбора", len(filtered))

    grouped: dict[int, list[dict]] = {}
    for region in filtered:
        sk = region["searcher_key"]
        if sk not in grouped:
            grouped[sk] = []
        grouped[sk].append(region)

    all_rows: list[Row] = []
    today = config.get("date") or date_type.today().isoformat()

    for searcher_key, regions in grouped.items():
        region_indexes = [r["region_index"] for r in regions]
        log.info("Searcher_key=%s: %s регионов, indexes=%s",
                 searcher_key, len(regions), region_indexes)

        need_check = False
        for region in regions:
            if not snapshot_exists(
                PROJECT_ID, region["region_index"], today,
                searcher_key, region["region_key"],
                region["region_lang"], region["region_device"]
            ):
                need_check = True
                break

        if need_check:
            log.info("Запуск проверки для searcher_key=%s", searcher_key)
            ids = run_check(PROJECT_ID, depth, region_indexes)
            if not ids:
                log.error("Не удалось запустить проверку для searcher_key=%s", searcher_key)
                continue
            if not poll_status(PROJECT_ID, timeout_sec=timeout_sec):
                log.error("Таймаут проверки для searcher_key=%s", searcher_key)
                continue
        else:
            log.info("Снимки за %s уже существуют, пропускаю проверку", today)

        for region in regions:
            try:
                log.info("Получение снимка: %s / %s (region_index=%s)",
                         region["searcher"], region["geo_name"], region["region_index"])
                rows = get_snapshot(
                    PROJECT_ID,
                    region["region_index"],
                    today,
                    depth,
                    searcher_key=region["searcher_key"],
                    region_key=region["region_key"],
                    region_lang=region["region_lang"],
                    region_device=region["region_device"],
                    geo=region["geo_name"],
                )
                log.info("Получено %s строк для %s / %s",
                         len(rows), region["searcher"], region["geo_name"])
                all_rows.extend(rows)
            except Exception as e:
                log.error("Ошибка при получении снимка %s / %s: %s",
                          region["searcher"], region["geo_name"], e)
                continue

    log.info("Всего собрано строк: %s", len(all_rows))
    return all_rows


if __name__ == "__main__":
    test_config = {
        "depth": 10,
        "searchers": ["google"],
        "geos": ["Литва"],
        "timeout_sec": 900,
        "date": "2026-06-19",
    }

    log.info("=== Тест collect(): %s ===", test_config)
    rows = collect(test_config)
    log.info("Результат: %s строк", len(rows))

    for i, row in enumerate(rows[:15], 1):
        log.info("%2d. query='%s' pos=%s domain=%s geo=%s",
                 i, row["query"][:30], row["position"], row["domain"][:35], row["geo"])
