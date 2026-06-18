
def load_config() -> dict:
    """
    Читает лист 'Настройки' из Google Sheet (ID из env GOOGLE_SHEET_ID).
    Возвращает dict ровно такой структуры:
    {
        "queries": list[str],          # поисковые запросы
        "geos": list[dict],            # [{"name": "Москва", "region_index": 213}, ...]
        "searchers": list[str],        # ["google", "yandex_ru", ...]
        "depth": int,                  # 10 | 20 | 50 | 100
        "label_mode": str,             # "lists" | "llm" | "hybrid" | "off"
        "project_id": int              # topvisor project_id из env
    }
    """
