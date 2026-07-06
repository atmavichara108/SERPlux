import json
import logging
import os
from datetime import date as date_type
from typing import Any

from topvisor import (
    Row,
    get_project_id,
    get_snapshot,
    poll_status,
    run_check,
    snapshot_exists,
)


def _get_project_id(config: dict[str, Any]) -> int:
    """project_id из config['project_id'], fallback на env/get_project_id()."""
    if config.get("project_id") is not None:
        return int(config["project_id"])
    return get_project_id()

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
    project_id = _get_project_id(config)

    # Путь к карте регионов: аргумент config > env REGIONS_MAP > дефолт
    regions_map_path = (
        config.get("regions_map")
        or os.environ.get("REGIONS_MAP")
        or "regions_map.json"
    )
    with open(regions_map_path, "r", encoding="utf-8") as f:
        regions_map = json.load(f)

    filtered = [
        r for r in regions_map
        if r["searcher"] in searchers and r["geo_name"] in geos
    ]
    if not filtered:
        log.warning("Нет связок для searchers=%s, geos=%s", searchers, geos)
        return []

    log.info("Найдено %s связок для сбора", len(filtered))

    all_rows: list[Row] = []
    today = config.get("date") or date_type.today().isoformat()

    # Проверяем наличие снапшотов для ВСЕХ регионов
    missing = [
        r for r in filtered
        if not snapshot_exists(
            project_id, r["region_index"], today,
            r["searcher_key"], r["region_key"],
            r["region_lang"], r["region_device"]
        )
    ]

    if missing:
        log.info("Отсутствуют снапшоты для %s из %s регионов, запускаю одну проверку",
                 len(missing), len(filtered))
        missing_indexes = [r["region_index"] for r in missing]
        ids = run_check(project_id, depth, missing_indexes)
        if not ids:
            log.warning("run_check не вернул id (возможно, проверка уже запущена)")
        if not poll_status(project_id, timeout_sec=timeout_sec):
            log.error("Таймаут ожидания проверки")
            return all_rows
    else:
        log.info("Все снапшоты за %s уже существуют, пропускаю проверку", today)

    # Скачиваем get_snapshot для ВСЕХ регионов
    # (run_check в Topvisor обновляет весь проект, а не подмножество)
    for region in filtered:
        try:
            log.info("Получение снимка: %s / %s (region_index=%s)",
                     region["searcher"], region["geo_name"], region["region_index"])
            rows = get_snapshot(
                project_id,
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
