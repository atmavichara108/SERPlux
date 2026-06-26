SUBJECT_BLOCKS = [
    {"key": "adsterra",            "display": "Adsterra",            "pos": 1,  "url": 2},
    {"key": "adsterra linkedin",   "display": "Adsterra LinkedIn",   "pos": 6,  "url": 7},
    {"key": "adsterra crunchbase", "display": "Adsterra Crunchbase", "pos": 9,  "url": 10},
    {"key": "adsterra review",     "display": "Adsterra review",     "pos": 12, "url": 13},
    {"key": "adsterra scam",       "display": "Adsterra scam",       "pos": 15, "url": 16},
]

COLS = 18

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
    "Кипр",
    "Индонезия",
    "Камбоджа",
    "Вьетнам",
    "Япония",
    "Таиланд",
]

# Глубина отображения в матрице-отчёте; в будущем — опция в интерфейсе serplux
REPORT_DEPTH = 10

# Пустые гео-секции рисуем на REPORT_DEPTH строк (не больше)
EMPTY_GEO_DEPTH = REPORT_DEPTH
