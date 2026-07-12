import os
from datetime import datetime
from typing import Any

import gspread
from dotenv import load_dotenv
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError

import storage
from storage import get_history, get_client
from config import GEO_DISPLAY, GEO_ORDER, EMPTY_GEO_DEPTH, REPORT_DEPTH, setup_logging

load_dotenv()

log = setup_logging(__name__)

SEARCHER_MAP = {
    "google": "Google",
    "yandex_ru": "Яндекс",
    "yandex_com": "Яндекс.com",
}

REPORT_SHEET_NAME = "Отчёт"
MAX_VERSIONS = 10  # храним последние 10 версий отчёта

LABEL_COLORS = {
    "positive": {"red": 0.85, "green": 0.92, "blue": 0.83},
    "negative": {"red": 0.96, "green": 0.80, "blue": 0.80},
    "neutral":  {"red": 1.0,  "green": 0.95, "blue": 0.80},
}


def _get_geo_display(geo: str) -> str:
    return GEO_DISPLAY.get(geo, geo)


def _get_spreadsheet(sheet_id: str | None = None):
    credentials_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID")

    if not sheet_id:
        log.error("GOOGLE_SHEET_ID не установлен в .env и не передан в параметрах")
        return None

    if not os.path.exists(credentials_path):
        log.error("Файл credentials не найден: %s", credentials_path)
        return None

    try:
        gc = gspread.service_account(filename=credentials_path)
        # Таймаут на все HTTP-вызовы Google API (connect, read) — защита от зависания
        gc.http_client.timeout = (10, 60)
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


def _build_subject_layout(queries: list[dict]) -> dict:
    """
    Строит раскладку колонок из списка субъектов (queries).

    Правило буферов (канон Лист1):
    - Субъект 1: колонка B (geo+номера+заливка), колонка C (имя+URL) → индексы 1, 2
    - Буфер после первого субъекта: 3 колонки (D, E, F)
    - Каждый следующий субъект: 2 колонки (geo/номера + имя/URL), перед ним 1 буферная колонка
    
    Возвращает dict:
    {
        "num_subjects": N,
        "cols": total column count,
        "subjects": [
            {"key": "...", "display": "...", "pos": idx, "url": idx+1},
            ...
        ]
    }
    """
    subjects = []
    col_idx = 1  # колонка B (0-indexed; колонка A=0 — пустая)

    for i, query in enumerate(queries):
        key = query.get("key", "")
        display = query.get("display", "")

        subjects.append({
            "key": key,
            "display": display,
            "pos": col_idx,      # левая колонка: гео + номера позиций + заливка
            "url": col_idx + 1,  # правая колонка: имя субъекта + URL
        })

        col_idx += 2  # pos + url
        if i == 0 and i < len(queries) - 1:
            col_idx += 3  # буфер 3 колонки после первого субъекта (D, E, F)
        elif i > 0 and i < len(queries) - 1:
            col_idx += 1  # буфер 1 колонка перед следующим субъектом

    total_cols = col_idx
    return {
        "num_subjects": len(queries),
        "cols": total_cols,
        "subjects": subjects,
    }


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


def _is_version_header(row: list[str]) -> bool:
    """
    Проверяет, является ли строка заголовком версии отчёта.
    Признак: первая ячейка начинается с "Позиции " и содержит " на ".
    Пример: "Позиции Google на 11.7.2026"
    """
    if not row or not row[0]:
        return False
    cell = str(row[0])
    return cell.startswith("Позиции ") and " на " in cell


def _count_versions(worksheet) -> int:
    """Считает число версий отчёта на листе по заголовкам."""
    try:
        values = worksheet.get_all_values()
        count = 0
        for row in values:
            if _is_version_header(row):
                count += 1
        return count
    except Exception as e:
        log.error("Ошибка при подсчёте версий: %s", e)
        return 0


def _trim_old_versions(spreadsheet, worksheet, max_versions: int = MAX_VERSIONS) -> None:
    """
    Удаляет самые старые версии отчёта снизу, если их больше max_versions.
    Версии считаются по заголовкам "Позиции ... на ...".
    """
    try:
        values = worksheet.get_all_values()
        if not values:
            return

        # Находим индексы строк с заголовками версий (0-indexed)
        version_rows = []
        for i, row in enumerate(values):
            if _is_version_header(row):
                version_rows.append(i)

        if len(version_rows) <= max_versions:
            return

        # Удаляем самые старые версии (последние в списке)
        rows_to_delete = version_rows[max_versions:]
        log.info("Удаляю %s старых версий отчёта (строки %s)", 
                 len(rows_to_delete), rows_to_delete)

        # Собираем диапазоны для удаления (снизу вверх, чтобы индексы не съехали)
        # Каждая версия — от заголовка до следующего заголовка (или конца листа)
        delete_requests = []
        for idx in reversed(rows_to_delete):
            # Находим конец версии (следующий заголовок или конец листа)
            next_version_idx = None
            for v in version_rows:
                if v > idx:
                    next_version_idx = v
                    break
            
            end_idx = next_version_idx if next_version_idx is not None else len(values)
            
            delete_requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": idx,
                        "endIndex": end_idx,
                    }
                }
            })

        if delete_requests:
            body = {"requests": delete_requests}
            spreadsheet.batch_update(body)
            log.info("Удалено %s старых версий отчёта", len(delete_requests))

    except Exception as e:
        log.error("Ошибка при обрезке старых версий: %s", e)


def build_report(date: str | None = None, force: bool = False, sheet_id: str | None = None,
                 client_id: str = "default", db_path: str = storage.DB_PATH) -> None:
    # force устарел: параметр оставлен в сигнатуре для обратной совместимости.
    # Загружаем профиль клиента для получения списка субъектов и гео
    client = get_client(client_id, db_path=db_path)
    if not client:
        log.error("Клиент %s не найден в БД", client_id)
        return
    
    queries = client.get("queries", [])
    regions_map = client.get("regions_map", [])
    
    if not queries:
        log.warning("У клиента %s нет субъектов (queries)", client_id)
        return
    
    # Получаем дату если не указана
    if date is None:
        all_rows = get_history(db_path=db_path)
        if not all_rows:
            log.warning("Нет данных в базе")
            return
        date = all_rows[0]["date"]
        log.info("Дата не указана, использую последнюю: %s", date)

    if date is None:
        raise ValueError("Дата не определена и нет данных в базе")

    rows = get_history(filters={"date": date}, db_path=db_path)
    if not rows:
        log.warning("Нет данных за дату %s", date)
        return

    # Фильтруем по известным субъектам из профиля клиента
    known_keys = {q["key"] for q in queries}
    rows = [r for r in rows if r["query"] in known_keys]

    if not rows:
        log.warning("Нет данных известных субъектов за дату %s", date)
        return

    # Строим раскладку колонок из профиля
    subject_layout = _build_subject_layout(queries)
    num_subjects = subject_layout["num_subjects"]
    cols = subject_layout["cols"]
    subject_blocks = subject_layout["subjects"]
    
    # Извлекаем порядок гео из regions_map клиента (уникальные geo_name)
    geo_set = set()
    for region in regions_map if isinstance(regions_map, list) else []:
        if isinstance(region, dict):
            geo_set.add(region.get("geo_name", ""))
    
    # Если regions_map не в виде списка или пустой, используем GEO_ORDER как fallback
    if not geo_set:
        geo_order = GEO_ORDER
    else:
        # Порядок из GEO_ORDER, но только те гео, которые есть в regions_map клиента
        geo_order = [g for g in GEO_ORDER if g in geo_set]
        # Добавляем оставшиеся гео из regions_map
        remaining = [g for g in sorted(geo_set) if g not in geo_order]
        geo_order.extend(remaining)

    log.info("Построение отчёта за %s: %s строк, %s субъектов, %s колонок", 
             date, len(rows), num_subjects, cols)

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
        for raw_geo, queries_data in raw_grouped[searcher].items():
            display = _get_geo_display(raw_geo)
            if display not in display_groups:
                display_groups[display] = {}
            for query, positions in queries_data.items():
                if query not in display_groups[display]:
                    display_groups[display][query] = {}
                display_groups[display][query].update(positions)
            max_p = max(
                (p for qp in display_groups[display].values() for p in qp),
                default=0,
            )
            geo_max_pos[display] = max_p

        report_data.append([f"Позиции {searcher_readable} на {date_formatted}"] + [""] * (cols - 1))
        report_data.append([""] * cols)

        hdr_row = [""] * cols
        for sb in subject_blocks:
            hdr_row[sb["url"]] = sb["display"]
        report_data.append(hdr_row)

        for geo_key in geo_order:
            geo_display = _get_geo_display(geo_key)

            geo_row = [""] * cols
            for sb in subject_blocks:
                geo_row[sb["pos"]] = geo_display
            report_data.append(geo_row)

            geo_data = display_groups.get(geo_display, {})
            max_pos = geo_max_pos.get(geo_display, 0)
            if max_pos == 0:
                max_pos = EMPTY_GEO_DEPTH

            for pos in range(1, min(max_pos, REPORT_DEPTH) + 1):
                row = [""] * cols
                for sb in subject_blocks:
                    qkey = sb["key"]
                    if qkey in geo_data and pos in geo_data[qkey]:
                        row[sb["pos"]] = str(pos)
                        url_val, label_val = geo_data[qkey][pos]
                        row[sb["url"]] = url_val
                        if label_val is not None and label_val in LABEL_COLORS:
                            row_idx = len(report_data)
                            format_cells.append((row_idx, sb["pos"], LABEL_COLORS[label_val]))
                report_data.append(row)

            report_data.append([""] * cols)

        report_data.append([""] * cols)
        report_data.append([""] * cols)

    spreadsheet = _get_spreadsheet(sheet_id=sheet_id)
    if spreadsheet is None:
        return

    worksheet = _get_or_create_report_sheet(spreadsheet)

    try:
        new_rows_count = len(report_data)
        log.info("Новый блок отчёта: %s строк", new_rows_count)

        # Проверяем, есть ли уже данные на листе
        existing_values = worksheet.get_all_values()
        has_existing_data = bool(existing_values and any(any(cell for cell in row) for row in existing_values))

        if has_existing_data:
            # Вставляем новый блок сверху через insertDimension
            log.info("Вставляю %s строк сверху (старые данные сдвинутся вниз)", new_rows_count)
            
            insert_request = {
                "insertDimension": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": new_rows_count,
                    },
                    "inheritFromBefore": False,
                }
            }
            
            body = {"requests": [insert_request]}
            spreadsheet.batch_update(body)
            log.info("Вставлено %s строк сверху, старые данные сдвинуты вниз", new_rows_count)

        # Записываем новый блок в A1
        log.info("Записываю новый блок отчёта в A1:%s", new_rows_count)
        worksheet.update(report_data, "A1")

        # Применяем заливку к новому блоку (строки 0..new_rows_count-1)
        worksheet_id = worksheet.id
        _apply_label_colors(spreadsheet, worksheet_id, format_cells)

        # Обрезаем старые версии, если их больше MAX_VERSIONS
        _trim_old_versions(spreadsheet, worksheet, MAX_VERSIONS)

        log.info("Отчёт успешно построен (накопительный режим, макс. %s версий)", MAX_VERSIONS)
    except APIError as e:
        log.error("Ошибка Google API при записи отчёта: %s", e)
    except Exception as e:
        log.error("Ошибка при построении отчёта: %s", e)


if __name__ == "__main__":
    setup_logging()

    print("=== Тест reporter.py ===\n")
    print("Построение отчёта по данным из базы (без топвизора)...\n")

    build_report(client_id="default", db_path=storage.DB_PATH)

    print("\n=== Тест завершён ===")
    print("Откройте Google Sheet и проверьте лист 'Отчёт':")
    print("- Динамическая матрица: N субъектов из профиля клиента")
    print("- Блоки по ПС, секции по гео, из regions_map клиента")
    print("- Ячейки позиций залиты цветом по метке: зелёный/красный/жёлтый")
