
def run_check(project_id: int, depth: int, region_indexes: list[int]) -> list[int]:
    """
    Запускает проверку позиций со сбором снимка.
    Вызывает edit/positions_2/checker/go с do_snapshots=1.
    Глубину прокидывает в настройки проекта при необходимости.
    Возвращает projectsIds, отправленные на проверку.
    """

def poll_status(project_id: int, timeout_sec: int = 600) -> bool:
    """
    Опрашивает процент готовности проверки до 100% или таймаута.
    Возвращает True если готово, False если таймаут.
    Пауза между опросами 10 сек.
    """

def get_snapshot(project_id: int, region_index: int, date: str,
                 depth: int) -> list[Row]:
    """
    Получает собранный ТОП через get/snapshots_2/history.
    Возвращает list[Row] с заполненными полями кроме label (=None).
    Поле domain вычисляет из url. snippet берёт из ответа если есть.
    """
