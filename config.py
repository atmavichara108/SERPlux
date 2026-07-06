SUBJECT_BLOCKS = [
    {"key": "juri sudheimer", "display": "Juri Sudheimer", "pos": 1,  "url": 2},
    {"key": "erik sudheimer", "display": "Erik Sudheimer", "pos": 6,  "url": 7},
    {"key": "sct chemicals",  "display": "SCT Chemicals",  "pos": 9,  "url": 10},
    {"key": "chempioil",      "display": "Chempioil",      "pos": 12, "url": 13},
]

COLS = 16

GEO_DISPLAY = {
    # Точные ключи из regions_map.json
    "Литва": "Lithuania",
    "Германия": "Germany",
    "Великобритания": "United Kingdom",
    "Лондон": "United Kingdom",
    "Объединённые Арабские Эмираты": "United Arab Emirates",
    "Кипр": "Cyprus",
    "Индонезия": "Indonesia",
    "Камбоджа": "Cambodia",
    "Вьетнам": "Vietnam",
    "Япония": "Japan",
    "Таиланд": "Thailand",
    # Legacy-ключи для совместимости со старыми данными
    "ОАЭ": "United Arab Emirates",
    "Объединённые Эмираты": "United Arab Emirates",
    "Кипр Eng": "Cyprus Eng",
    "Кипр Greek": "Cyprus Greek",
}

GEO_ORDER: list[str] = [
    "Литва",
    "Германия",
    "Великобритания",
    "Лондон",
    "Объединённые Арабские Эмираты",
    "Кипр",
]

# Глубина отображения в матрице-отчёте; в будущем — опция в интерфейсе serplux
REPORT_DEPTH = 10

# Пустые гео-секции рисуем на REPORT_DEPTH строк (не больше)
EMPTY_GEO_DEPTH = REPORT_DEPTH

# ─── Логирование ──────────────────────────────────────────────────────────────

import logging
import os
import sys


def setup_logging(name: str | None = None) -> logging.Logger:
    """
    Единая настройка логирования.

    - Уровень из env LOG_LEVEL (дефолт INFO)
    - Вывод в stdout (для docker compose logs)
    - Формат: timestamp | module | level | message

    Возвращает логгер с заданным именем (или root).
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger = logging.getLogger(name) if name else logging.getLogger()
    logger.setLevel(level)

    # Убираем дублирующиеся обработчики при повторном вызове
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in logger.handlers):
        logger.addHandler(handler)

    # Не прокидываем логи корневому логгеру, если имя задано
    if name:
        logger.propagate = False

    return logger


# ─── Провайдеры LLM ───────────────────────────────────────────────────────────

PROVIDERS: dict[str, dict] = {
    "opencode-zen": {
        "enabled": True,
        "priority": 1,
        "default_model": "deepseek-v4-flash-free",
        "models": ["deepseek-v4-flash-free"],
        "endpoint": "https://opencode.ai/zen/v1/chat/completions",
        "api_key_env_var": "OPENCODE_API_KEY",
    },
}
DEFAULT_PROVIDER: str = "opencode-zen"
