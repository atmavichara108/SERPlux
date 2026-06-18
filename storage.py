
def collect(config: dict) -> list[Row]:
    """
    Оркестратор сбора по всем связкам searcher × query × geo.
    Для каждого searcher: run_check -> poll_status -> get_snapshot.
    ОБРАБОТКА СБОЕВ: ошибка одной связки логируется через logging.error,
    пропускается, сбор продолжается. Не падать целиком.
    Возвращает объединённый list[Row] по всем успешным связкам.
    """
