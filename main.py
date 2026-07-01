import logging
import sys
from typing import Any

from collector import collect
from storage import save, update_labels, _ensure_db
from labeler import label
from exporter import export
from reporter import build_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

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


def run(config: dict[str, Any]) -> int:
    log.info("=== Старт прогона ===")
    log.info("Параметры: %s", config)

    try:
        rows = collect(config)
    except Exception as e:
        log.error("Сбой collect: %s", e)
        return 1

    if not rows:
        log.warning("Нет данных для обработки")
        return 0

    log.info("Собрано строк: %s", len(rows))

    # Инициализируем БД перед первой записью
    _ensure_db()

    saved_count = 0
    try:
        saved_count = save(rows)
        log.info("Сохранено в БД: %s (новых)", saved_count)
    except Exception as e:
        log.error("Сбой save: %s", e)

    # Разметка тональности и сохранение меток
    labeled_count = 0
    labeled_rows = rows  # fallback: если labeler упал, используем сырые данные
    if config.get("with_labels", True):
        try:
            labeled_rows = label(rows)
            labeled_count = update_labels(labeled_rows)
            log.info("Размечено и обновлено меток: %s", labeled_count)
        except Exception as e:
            log.error("Сбой labeler: %s", e)
    else:
        log.info("Разметка пропущена (with_labels=False)")

    export_ok = False
    try:
        export(labeled_rows)
        export_ok = True
        log.info("Выгружено в Sheet: %s строк", len(rows))
    except Exception as e:
        log.error("Сбой export: %s", e)

    # Построение отчёта
    report_ok = False
    try:
        build_report()
        report_ok = True
        log.info("Отчёт построен")
    except Exception as e:
        log.error("Сбой reporter: %s", e)

    log.info("=== Итог прогона ===")
    if export_ok:
        log.info("Собрано: %s | Сохранено (новых): %s | Меток: %s | Выгружено: %s | Отчёт: %s",
                  len(rows), saved_count, labeled_count, len(rows), "OK" if report_ok else "FAIL")
    else:
        log.info("Собрано: %s | Сохранено (новых): %s | Меток: %s | Выгрузка не удалась | Отчёт: %s",
                  len(rows), saved_count, labeled_count, "OK" if report_ok else "FAIL")
    return 0


if __name__ == "__main__":
    exit_code = run(DEFAULT_CONFIG)
    sys.exit(exit_code)
