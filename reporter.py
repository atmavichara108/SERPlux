import logging
import os
from datetime import datetime
from typing import Any

import gspread
from dotenv import load_dotenv
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError

from storage import get_history
from config import SUBJECT_DISPLAY, GEO_DISPLAY, GEO_ORDER, EMPTY_GEO_DEPTH, REPORT_DEPTH

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEARCHER_MAP = {
    "google": "Google",
    "yandex_ru": "Яндекс",
    "yandex_com": "Яндекс.com",
}

REPORT_SHEET_NAME = "Отчёт"

COLS = 16

SUBJECT_BLOCKS = [
    {"key": "juri sudheimer", "display": "Juri Sudheimer", "pos": 1,  "url": 2},
    {"key": "erik sudheimer", "display": "Erik Sudheimer", "pos": 6,  "url": 7},
    {"key": "sct chemicals",  "display": "SCT Chemicals",  "pos": 9,  "url": 10},
    {"key": "chempioil",      "display": "Chempioil",      "pos": 12, "url": 13},
]

LABEL_COLORS = {
    "positive": {"red": 0.85, "green": 0.92, "blue": 0.83},
    "negative": {"red": 0.96, "green": 0.80, "blue": 0.80},
    "neutral":  {"red": 1.0,  "green": 0.95, "blue": 0.80},
}


def _get_geo_display(geo: str) -> str:
    return GEO_DISPLAY.get(geo, geo)


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


def _apply_label_colors(spreadsheet, sheet_id: int,
                        format_cells: list[tuple[int, int, dict]]) -> None:
    if not format_cells:
        return

    requests = []
    for row_idx, col_idx, color in format_cells:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color,
                    },
                },
                "fields": "userEnteredFormat.backgroundColor",
            },
        })

    body = {"requests": requests}
    try:
        spreadsheet.batch_update(body)
        log.info("Применено %s цветовых разметок", len(format_cells))
    except Exception as e:
        log.error("Ошибка при применении цветов: %s", e)


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

    known_keys = {s["key"] for s in SUBJECT_BLOCKS}
    rows = [r for r in rows if r["query"] in known_keys]

    if not rows:
        log.warning("Нет данных известных субъектов за дату %s", date)
        return

    log.info("Построение отчёта за %s: %s строк", date, len(rows))

    raw_grouped: dict[str, dict[str, dict[str, dict[int, tuple[str, str | None]]]]] = {}
    for row in rows:
        searcher = row["searcher"]
        geo = row["geo"]
        query = row["query"]
        pos = row["position"]
        url = row["url"]
        label = row.get("label")
        raw_grouped.setdefault(searcher, {}).setdefault(geo, {}).setdefault(query, {})[pos] = (url, label)

    report_data: list[list[str]] = []
    format_cells: list[tuple[int, int, dict]] = []

    for searcher in sorted(raw_grouped.keys()):
        searcher_readable = SEARCHER_MAP.get(searcher, searcher)
        date_formatted = _format_date(date)

        display_groups: dict[str, dict[str, dict[int, tuple[str, str | None]]]] = {}
        geo_max_pos: dict[str, int] = {}
        for raw_geo, queries in raw_grouped[searcher].items():
            display = _get_geo_display(raw_geo)
            if display not in display_groups:
                display_groups[display] = {}
            for query, positions in queries.items():
                if query not in display_groups[display]:
                    display_groups[display][query] = {}
                display_groups[display][query].update(positions)
            max_p = max(
                (p for qp in display_groups[display].values() for p in qp),
                default=0,
            )
            geo_max_pos[display] = max_p

        report_data.append([f"Позиции {searcher_readable} на {date_formatted}"] + [""] * (COLS - 1))
        report_data.append([""] * COLS)

        hdr_row = [""] * COLS
        for sb in SUBJECT_BLOCKS:
            hdr_row[sb["url"]] = sb["display"]
        report_data.append(hdr_row)

        for geo_key in GEO_ORDER:
            geo_display = _get_geo_display(geo_key)

            geo_row = [""] * COLS
            for sb in SUBJECT_BLOCKS:
                geo_row[sb["pos"]] = geo_display
            report_data.append(geo_row)

            geo_data = display_groups.get(geo_display, {})
            max_pos = geo_max_pos.get(geo_display, 0)
            if max_pos == 0:
                max_pos = EMPTY_GEO_DEPTH

            for pos in range(1, min(max_pos, REPORT_DEPTH) + 1):
                row = [""] * COLS
                for sb in SUBJECT_BLOCKS:
                    qkey = sb["key"]
                    if qkey in geo_data and pos in geo_data[qkey]:
                        row[sb["pos"]] = str(pos)
                        url_val, label_val = geo_data[qkey][pos]
                        row[sb["url"]] = url_val
                        if label_val is not None and label_val in LABEL_COLORS:
                            row_idx = len(report_data)
                            format_cells.append((row_idx, sb["pos"], LABEL_COLORS[label_val]))
                report_data.append(row)

            report_data.append([""] * COLS)

        report_data.append([""] * COLS)
        report_data.append([""] * COLS)

    spreadsheet = _get_spreadsheet()
    if spreadsheet is None:
        return

    worksheet = _get_or_create_report_sheet(spreadsheet)

    try:
        log.info("Вставляю %s строк отчёта на лист '%s'", len(report_data), REPORT_SHEET_NAME)
        worksheet.insert_rows(report_data, 1)

        sheet_id = worksheet.id
        _apply_label_colors(spreadsheet, sheet_id, format_cells)

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
    print("- 16-колоночная матрица: pos/URL для каждого субъекта + разделители")
    print("- Блоки по ПС, секции по гео, точный формат заказчика")
    print("- Ячейки позиций залиты цветом по метке: зелёный/красный/жёлтый")
