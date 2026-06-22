import logging
import sys
from typing import Any

from collector import collect
from storage import save
from exporter import export

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

    saved_count = 0
    try:
        saved_count = save(rows)
        log.info("Сохранено в БД: %s (новых)", saved_count)
    except Exception as e:
        log.error("Сбой save: %s", e)

    export_ok = False
    try:
        export(rows)
        export_ok = True
        log.info("Выгружено в Sheet: %s строк", len(rows))
    except Exception as e:
        log.error("Сбой export: %s", e)

    log.info("=== Итог прогона ===")
    if export_ok:
        log.info("Собрано: %s | Сохранено (новых): %s | Выгружено: %s",
                 len(rows), saved_count, len(rows))
    else:
        log.info("Собрано: %s | Сохранено (новых): %s | Выгрузка не удалась",
                 len(rows), saved_count)
    return 0


if __name__ == "__main__":
    exit_code = run(DEFAULT_CONFIG)
    sys.exit(exit_code)
