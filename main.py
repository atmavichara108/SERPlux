import sys
from typing import Any

import config
import storage
from collector import collect
from storage import save, insert_labels, _ensure_db
from labeler import label
from exporter import export
from reporter import build_report

log = config.setup_logging(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "depth": 10,
    "searchers": ["google", "yandex_ru", "yandex_com"],
    # Оба UK-варианта: yandex_ru → "Великобритания", google/yandex_com → "Лондон"
    # collector фильтрует по точному geo_name из regions_map.json (15 пар = 3×5)
    "geos": [
        "Литва",
        "Германия",
        "Великобритания",
        "Лондон",
        "Объединённые Арабские Эмираты",
        "Кипр",
    ],
    "timeout_sec": 900,
    "with_labels": True,
    # TODO: config из листа "Настройки" Google Sheet — этап 3
}


def run(config: dict[str, Any]) -> dict[str, Any]:
    """
    Запускает полный пайплайн.

    Возвращает dict:
        {
            "exit_code": int,
            "stats": {
                "collected": int,
                "saved_new": int,
                "labeled": int,
                "exported": int,
            }
        }
    """
    log.info("=== Старт прогона ===")
    log.info("Параметры: %s", config)

    client_id = config.get("client_id", "default")
    label_mode = config.get("label_mode", "domains")
    force_relabel = config.get("force_relabel", False)
    sheet_id = config.get("sheet_id")
    force_rebuild_report = config.get("force_rebuild_report", False)
    provider_chain = config.get("provider_chain")

    # Наполняем config значениями из профиля клиента / fallback DEFAULT_CONFIG
    runtime_config = {
        **DEFAULT_CONFIG,
        **config,
        # Явно берём из config, если заданы; иначе из DEFAULT_CONFIG выше
        "searchers": config.get("searchers") or DEFAULT_CONFIG["searchers"],
        "geos": config.get("geos") or DEFAULT_CONFIG["geos"],
    }
    if config.get("queries"):
        runtime_config["queries"] = config["queries"]
    if config.get("regions_map"):
        runtime_config["regions_map"] = config["regions_map"]

    stats = {
        "collected": 0,
        "saved_new": 0,
        "labeled": 0,
        "exported": 0,
    }

    try:
        rows = collect(runtime_config)
    except Exception as e:
        log.error("Сбой collect: %s", e)
        return {"exit_code": 1, "stats": stats}

    if not rows:
        log.warning("Нет данных для обработки")
        return {"exit_code": 0, "stats": stats}

    log.info("Собрано строк: %s", len(rows))
    stats["collected"] = len(rows)

    # Инициализируем БД перед первой записью
    _ensure_db(db_path=storage.DB_PATH)

    saved_count = 0
    try:
        saved_count = save(rows, client_id=client_id, db_path=storage.DB_PATH)
        log.info("Сохранено в БД: %s (новых)", saved_count)
        stats["saved_new"] = saved_count
    except Exception as e:
        log.error("Сбой save: %s", e)

    # Разметка тональности и сохранение меток
    labeled_count = 0
    labeled_rows = rows  # fallback: если labeler упал, используем сырые данные
    if config.get("with_labels", True):
        try:
            label_kwargs = {
                "label_mode": label_mode,
                "force_relabel": force_relabel,
                "client_id": client_id,
                "db_path": storage.DB_PATH,
            }
            if provider_chain is not None:
                label_kwargs["provider_chain"] = provider_chain
            labeled_rows = label(rows, **label_kwargs)
            labeled_count = insert_labels(labeled_rows, db_path=storage.DB_PATH)
            log.info("Размечено и сохранено меток: %s", labeled_count)
            stats["labeled"] = labeled_count
        except Exception as e:
            log.error("Сбой labeler: %s", e)
    else:
        log.info("Разметка пропущена (with_labels=False)")

    export_ok = False
    try:
        export(labeled_rows, sheet_id=sheet_id)
        export_ok = True
        log.info("Выгружено в Sheet: %s строк", len(rows))
        stats["exported"] = len(rows)
    except Exception as e:
        log.error("Сбой export: %s", e)
        stats["exported"] = 0

    # Построение отчёта
    report_ok = False
    try:
        build_report(force=force_rebuild_report, sheet_id=sheet_id, 
                     client_id=client_id, db_path=storage.DB_PATH)
        report_ok = True
        log.info("Отчёт построен")
    except Exception as e:
        log.error("Сбой reporter: %s", e)

    log.info("=== Итог прогона ===")
    log.info("Общая статистика: collected=%s saved_new=%s labeled=%s exported=%s report=%s",
             len(rows), saved_count, labeled_count,
             stats["exported"], "OK" if report_ok else "FAIL")

    # Сводка по каждому searcher (строим по labeled_rows, чтобы не зависеть от in-place mutation)
    searcher_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        s = row.get("searcher") or "unknown"
        if s not in searcher_stats:
            searcher_stats[s] = {"collected": 0, "labeled": 0, "exported": 0}
        searcher_stats[s]["collected"] += 1

    for row in labeled_rows:
        s = row.get("searcher") or "unknown"
        if s not in searcher_stats:
            searcher_stats[s] = {"collected": 0, "labeled": 0, "exported": 0}
        if row.get("sentiment") is not None:
            searcher_stats[s]["labeled"] += 1
        # exported совпадает с collected, если export успешен
        if export_ok:
            searcher_stats[s]["exported"] += 1

    if searcher_stats:
        log.info("Статистика по searcher:")
        for s, st in sorted(searcher_stats.items()):
            log.info("  %s: collected=%s labeled=%s exported=%s",
                     s, st["collected"], st["labeled"], st["exported"])

    return {"exit_code": 0, "stats": stats}


if __name__ == "__main__":
    result = run(DEFAULT_CONFIG)
    sys.exit(result["exit_code"])
