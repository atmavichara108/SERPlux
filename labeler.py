
def label(rows: list[Row], mode: str) -> list[Row]:
    """
    Размечает rows по тональности. Возвращает те же rows с полем label.
    mode:
      "off"    -> ничего не делает, label остаётся None
      "lists"  -> сверяет url с листами Позитивные/Негативные (из storage)
      "llm"    -> для url без кэша зовёт llm_label_batch, кэш через storage
      "hybrid" -> сначала lists, для оставшихся None -> llm
    ВСЕГДА сначала проверяет get_cached_label(url). Закэшированные не трогает.
    """

def llm_label_batch(rows: list[Row]) -> list[Row]:
    """
    Размечает пачку через Gemini Flash (GEMINI_API_KEY).
    Вход модели: title/snippet + домен. НЕ грузить полную страницу на старте.
    Батч по 15 url за запрос. Возвращает rows с label из
    {"positive","negative","neutral"}.
    """
