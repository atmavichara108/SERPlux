import logging
import os
from datetime import datetime
from typing import Any

import gspread
from dotenv import load_dotenv
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError

from storage import get_history
from config import SUBJECT_DISPLAY, GEO_DISPLAY

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEARCHER_MAP = {
    "google": "Google",
    "yandex_ru": "Яндекс",
    "yandex_com": "Яндекс.com",
}

REPORT_SHEET_NAME = "Отчёт"


def _get_spreadsheet():
    credentials_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not sheet_id:
        log.error("GOOGLE_SHEET_ID не установлен в .env")
        return None

    if not os.path.exists(credentials_path):
        log.error("Файл credentials не найден: %s", credentials_path)
        return None

    try:
        gc = gspread.service_account(filename=credentials_path)
        spreadsheet = gc.open_by_key(sheet_id)
        return spreadsheet
    except SpreadsheetNotFound:
        log.error("Таблица %s не найдена или нет доступа", sheet_id)
        return None
    except Exception as e:
        log.error("Ошибка авторизации: %s", e)
        return None


def _get_or_create_report_sheet(spreadsheet):
    try:
        worksheet = spreadsheet.worksheet(REPORT_SHEET_NAME)
        log.info("Лист '%s' найден", REPORT_SHEET_NAME)
        return worksheet
    except WorksheetNotFound:
        log.info("Лист '%s' не найден, создаю новый", REPORT_SHEET_NAME)
        worksheet = spreadsheet.add_worksheet(title=REPORT_SHEET_NAME, rows=1000, cols=26)
        return worksheet


def _format_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}.{dt.month}.{dt.year}"


def build_report(date: str | None = None) -> None:
    if date is None:
        all_rows = get_history()
        if not all_rows:
            log.warning("Нет данных в базе")
            return
        date = all_rows[0]["date"]
        log.info("Дата не указана, использую последнюю: %s", date)

    assert date is not None

    rows = get_history(filters={"date": date})
    if not rows:
        log.warning("Нет данных за дату %s", date)
        return

    log.info("Построение отчёта за %s: %s строк", date, len(rows))

    grouped: dict[str, dict[str, dict[str, dict[int, str]]]] = {}
    all_queries: set[str] = set()
    
    for row in rows:
        searcher = row["searcher"]
        geo = row["geo"]
        query = row["query"]
        position = row["position"]
        url = row["url"]

        if searcher not in grouped:
            grouped[searcher] = {}
        if geo not in grouped[searcher]:
            grouped[searcher][geo] = {}
        if query not in grouped[searcher][geo]:
            grouped[searcher][geo][query] = {}

        grouped[searcher][geo][query][position] = url
        all_queries.add(query)
    
    # Sort queries by SUBJECT_DISPLAY order, fallback unknowns to end
    def sort_key(query: str) -> tuple[int, str]:
        if query in SUBJECT_DISPLAY:
            return (SUBJECT_DISPLAY[query][0], query)
        # Unknown subjects go to end
        return (999, query)
    
    sorted_queries = sorted(all_queries, key=sort_key)

    report_data: list[list[str]] = []

    for searcher in sorted(grouped.keys()):
        searcher_readable = SEARCHER_MAP.get(searcher, searcher)
        date_formatted = _format_date(date)
        report_data.append([f"Позиции {searcher_readable} на {date_formatted}"])

        geo_data = grouped[searcher]
        for geo in sorted(geo_data.keys()):
            # Use GEO_DISPLAY mapping, fallback to raw geo name
            geo_display = GEO_DISPLAY.get(geo, geo)
            report_data.append([geo_display])

            # Filter queries for this geo and sort by SUBJECT_DISPLAY order
            queries_for_geo = set(geo_data[geo].keys())
            queries_sorted = sorted(
                queries_for_geo,
                key=lambda q: (SUBJECT_DISPLAY[q][0], q) if q in SUBJECT_DISPLAY else (999, q)
            )
            
            # Header row: display names or raw query names
            header_row = [""]
            for query in queries_sorted:
                display_name = SUBJECT_DISPLAY.get(query, (999, query))[1] if query in SUBJECT_DISPLAY else query
                header_row.append(display_name)
            report_data.append(header_row)

            max_position = max(
                pos
                for query_data in geo_data[geo].values()
                for pos in query_data.keys()
            )

            for pos in range(1, max_position + 1):
                row_data = [str(pos)]
                for query in queries_sorted:
                    url = geo_data[geo][query].get(pos, "")
                    row_data.append(url)
                report_data.append(row_data)

        report_data.append([])

    spreadsheet = _get_spreadsheet()
    if spreadsheet is None:
        return

    worksheet = _get_or_create_report_sheet(spreadsheet)

    try:
        log.info("Вставляю %s строк отчёта на лист '%s'", len(report_data), REPORT_SHEET_NAME)
        worksheet.insert_rows(report_data, 1)
        log.info("Отчёт успешно построен")
    except APIError as e:
        log.error("Ошибка Google API при записи отчёта: %s", e)
    except Exception as e:
        log.error("Ошибка при построении отчёта: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== Тест reporter.py ===\n")
    print("Построение отчёта по данным из базы (без топвизора)...\n")

    build_report()

    print("\n=== Тест завершён ===")
    print("Откройте Google Sheet и проверьте лист 'Отчёт':")
    print("- Блоки по ПС (Google, Яндекс, Яндекс.com)")
    print("- Секции по гео внутри каждого блока")
    print("- Матрица: позиции × субъекты с URL в ячейках")
