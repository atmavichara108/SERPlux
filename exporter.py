import os
from typing import Any

import gspread
from dotenv import load_dotenv
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError

import config

load_dotenv()

log = config.setup_logging(__name__)

Row = dict[str, Any]

CACHE_SHEET_NAME = "Лист2"

SEARCHER_MAP = {
    "google": "Google",
    "yandex_ru": "Яндекс",
    "yandex_com": "Яндекс.com",
}

HEADER = ["Дата", "Поисковая система", "Субъект/Запрос", "Гео", "Позиция", "URL", "Домен", "Сниппет", "Метка"]


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
        log.error("Таблица %s не найдена или нет доступа. "
                  "Убедитесь, что sheet_id корректен и таблица расшарена на service account с правами Editor.",
                  sheet_id)
        return None
    except Exception as e:
        log.error("Ошибка авторизации: %s. "
                  "Проверьте credentials.json и что таблица расшарена на client_email с правами Editor.",
                  e)
        return None


def _get_or_create_cache_sheet(spreadsheet):
    """Возвращает лист 'Лист2' для кэша выдачи, создаёт при необходимости."""
    try:
        worksheet = spreadsheet.worksheet(CACHE_SHEET_NAME)
        log.info("Лист '%s' найден", CACHE_SHEET_NAME)
        return worksheet
    except WorksheetNotFound:
        log.info("Лист '%s' не найден, создаю новый", CACHE_SHEET_NAME)
        worksheet = spreadsheet.add_worksheet(title=CACHE_SHEET_NAME, rows=1000, cols=20)
        return worksheet


def _row_to_list(row: Row) -> list[str]:
    searcher_readable = SEARCHER_MAP.get(row["searcher"], row["searcher"])
    label = row.get("label")
    label_str = label if label is not None else ""
    snippet = row.get("snippet")
    snippet_str = snippet if snippet is not None else ""
    return [
        str(row["date"]),
        str(searcher_readable),
        str(row["query"]),
        str(row["geo"]),
        str(row["position"]),
        str(row["url"]),
        str(row["domain"]),
        snippet_str,
        label_str,
    ]


def export(rows: list[Row], sheet_id: str | None = None) -> None:
    """
    Выгружает кэш выдачи (positions + метки) на лист 'Лист2'.

    Лист полностью очищается перед записью (перезапись, не append).
    Лист 'Отчёт' не трогается — туда пишет reporter.build_report().
    """
    if not rows:
        log.warning("Нет строк для экспорта")
        return

    spreadsheet = _get_spreadsheet(sheet_id=sheet_id)
    if spreadsheet is None:
        return

    worksheet = _get_or_create_cache_sheet(spreadsheet)
    if worksheet is None:
        return

    try:
        # Полная очистка листа кэша перед записью
        worksheet.clear()
        log.info("Лист '%s' очищен перед записью кэша", CACHE_SHEET_NAME)

        data_to_insert = [HEADER] + [_row_to_list(row) for row in rows]

        log.info("Записываю %s строк (включая заголовок) на лист '%s'",
                 len(data_to_insert), CACHE_SHEET_NAME)

        worksheet.update(data_to_insert, "A1")

        log.info("Экспорт кэша завершён: %s строк", len(rows))

    except APIError as e:
        log.error("Ошибка Google API при записи кэша: %s", e)
    except Exception as e:
        log.error("Ошибка при экспорте кэша: %s", e)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    log = config.setup_logging(__name__)

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    test_rows_1: list[Row] = [
        {
            "date": yesterday,
            "searcher": "google",
            "query": "chempioil",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://chempioil.com",
            "domain": "chempioil.com",
            "snippet": "Official site",
            "label": "positive",
        },
        {
            "date": yesterday,
            "searcher": "google",
            "query": "chempioil",
            "geo": "Литва",
            "region_index": 1300,
            "position": 2,
            "url": "https://example.com/chempioil",
            "domain": "example.com",
            "snippet": "Review",
            "label": "neutral",
        },
        {
            "date": yesterday,
            "searcher": "yandex_ru",
            "query": "juri sudheimer",
            "geo": "Германия",
            "region_index": 1018,
            "position": 1,
            "url": "https://linkedin.com/in/juri",
            "domain": "linkedin.com",
            "snippet": "Profile",
            "label": None,
        },
    ]

    test_rows_2: list[Row] = [
        {
            "date": today,
            "searcher": "google",
            "query": "chempioil",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://chempioil.com",
            "domain": "chempioil.com",
            "snippet": "Official site",
            "label": "positive",
        },
        {
            "date": today,
            "searcher": "google",
            "query": "chempioil",
            "geo": "Литва",
            "region_index": 1300,
            "position": 3,
            "url": "https://news.com/chempioil",
            "domain": "news.com",
            "snippet": "News article",
            "label": "negative",
        },
    ]

    print("=== Тест exporter.py ===\n")

    print(f"1. Первый экспорт (дата={yesterday}, 3 строки)...")
    export(test_rows_1)
    print(f"   Проверьте таблицу: лист '{CACHE_SHEET_NAME}' должен содержать заголовок + 3 строки\n")

    print(f"2. Второй экспорт (дата={today}, 2 строки)...")
    export(test_rows_2)
    print(f"   Проверьте таблицу: лист '{CACHE_SHEET_NAME}' должен содержать ТОЛЬКО заголовок + 2 строки\n")

    print("Ожидаемая структура листа кэха:")
    print("  Строка 1: Заголовок (Дата | Поисковая система | Субъект/Запрос | ...)")
    print(f"  Строка 2: {today} | Google | chempioil | Литва | 1 | ...")
    print(f"  Строка 3: {today} | Google | chempioil | Литва | 3 | ...")
    print("\nВажно: предыдущие строки за %s должны быть удалены (перезапись)." % yesterday)
    print()

    print("=== Тест завершён ===")
    print("Откройте Google Sheet и проверьте лист '%s' визуально." % CACHE_SHEET_NAME)
