# DEPRECATED: SUBJECT_BLOCKS и COLS больше не используются в reporter.py
# Они остаются здесь для обратной совместимости, но теперь:
# - reporter.py динамически строит раскладку из client.queries
# - число колонок вычисляется динамически как N*2 + (N-1)*1
# См. ADR в docs/decisions.md "Динамический reporter" (2026-07-10)
#
# Оригинальная конфигурация для client1 (4 субъекта, 16 колонок):
_DEPRECATED_SUBJECT_BLOCKS = [
    {"key": "juri sudheimer", "display": "Juri Sudheimer", "pos": 1,  "url": 2},
    {"key": "erik sudheimer", "display": "Erik Sudheimer", "pos": 6,  "url": 7},
    {"key": "sct chemicals",  "display": "SCT Chemicals",  "pos": 9,  "url": 10},
    {"key": "chempioil",      "display": "Chempioil",      "pos": 12, "url": 13},
]
_DEPRECATED_COLS = 16

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

# Известные OpenAI-совместимые endpoint'ы (chat/completions).
# Используются в POST /providers/register, когда endpoint не передан явно.
KNOWN_ENDPOINTS: dict[str, str] = {
    "opencode-zen": "https://opencode.ai/zen/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}

# Бюджетные модели OpenCode Zen для разметки (решение 2026-09-12, вариант A).
# Free-модели исключены: OpenCode гейтит free tier клиентом OpenCode
# (400 MissingSessionID "free tier can only be used in OpenCode"),
# Прямой тест провайдера 2026-09-12; details: docs/decisions.md ADR 2026-09-08.
# Пул — дешёвые платные модели chat/completions; ротация 3-strikes в labeler.
ZEN_BUDGET_MODELS: list[str] = [
    "deepseek-v4-flash",   # $0.14/$0.28 за 1M — основной
    "glm-5.3-flash",       # $0.15/$0.50 — fallback 1
    "kimi-k2.6",           # $0.95/$4.00 — fallback 2
]

# Deprecated: free-модели недоступны извне OpenCode (v1.0.4).
# Оставлены для обратной совместимости импортов/тестов, в пул не входят.
ZEN_FREE_MODELS: list[str] = [
    "mimo-v2.5-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "muse-spark-1.3-contributor-free",
    "big-pickle",
]

PROVIDERS: dict[str, dict] = {
    "opencode-zen": {
        "enabled": True,
        "priority": 1,
        "default_model": os.environ.get("OPENCODE_MODEL", "deepseek-v4-flash"),
        "models": list(ZEN_BUDGET_MODELS),
        # В песочнице endpoint подменяется на локальный мок (LLM_API_BASE),
        # в проде переменная не задаётся — работает канонический URL.
        "endpoint": os.environ.get("LLM_API_BASE", KNOWN_ENDPOINTS["opencode-zen"]),
        "api_key_env_var": "OPENCODE_API_KEY",
    },
}
DEFAULT_PROVIDER: str = "opencode-zen"
_provider_log = setup_logging(__name__)


def register_provider(provider_id: str, cfg: dict) -> bool:
    """Регистрирует нового провайдера в runtime.
    
    cfg должен содержать: enabled, priority, default_model, models, endpoint, api_key_env_var.
    Возвращает True если провайдер добавлен, False если уже существует.
    """
    if provider_id in PROVIDERS:
        return False
    required_keys = {"enabled", "priority", "default_model", "models", "endpoint", "api_key_env_var"}
    if not required_keys.issubset(cfg.keys()):
        missing = required_keys - cfg.keys()
        _provider_log.warning("register_provider: отсутствуют ключи %s для %s", missing, provider_id)
        return False
    PROVIDERS[provider_id] = cfg
    _provider_log.info("Зарегистрирован провайдер: %s", provider_id)
    return True
