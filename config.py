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
