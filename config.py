SUBJECT_DISPLAY = {
    "juri sudheimer": (1, "Juri Sudheimer"),
    "erik sudheimer": (2, "Erik Sudheimer"),
    "sct chemicals":  (3, "SCT Chemicals"),
    "chempioil":      (4, "Chempioil"),
}

GEO_DISPLAY = {
    # Точные ключи из regions_map.json
    "Литва": "Lithuania",
    "Германия": "Germany",
    "Великобритания": "United Kingdom",
    "Лондон": "United Kingdom",
    "Объединённые Арабские Эмираты": "United Arab Emirates",
    "Кипр": "Cyprus",
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
