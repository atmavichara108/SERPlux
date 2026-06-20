import os
import logging
from typing import Any

import gspread
from dotenv import load_dotenv
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound, APIError

load_dotenv()

log = logging.getLogger(__name__)

Row = dict[str, Any]

SEARCHER_MAP = {
    "google": "Google",
    "yandex_ru": "Яндекс",
    "yandex_com": "Яндекс.com",
}

HEADER = ["Дата", "Поисковая система", "Субъект/Запрос", "Гео", "Позиция", "URL", "Домен", "Сниппет", "Метка"]


def _get_sheet():
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
        worksheet = spreadsheet.sheet1
        return worksheet
    except SpreadsheetNotFound:
        log.error("Таблица %s не найдена или нет доступа. "
                  "Убедитесь, что sheet_id корректен и таблица расшарена на service account с правами Editor.",
                  sheet_id)
        return None
    except WorksheetNotFound:
        log.error("Лист не найден в таблице %s", sheet_id)
        return None
    except APIError as e:
        log.error("Ошибка Google API: %s", e)
        return None
    except Exception as e:
        log.error("Ошибка авторизации: %s. "
                  "Проверьте credentials.json и что таблица расшарена на client_email с правами Editor.",
                  e)
        return None


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


def export(rows: list[Row]) -> None:
    if not rows:
        log.warning("Нет строк для экспорта")
        return

    worksheet = _get_sheet()
    if worksheet is None:
        return

    try:
        existing_values = worksheet.get_all_values()
        has_header = len(existing_values) > 0 and existing_values[0] == HEADER

        if not has_header:
            log.info("Лист пустой или нет заголовка — добавляю заголовок")
            worksheet.insert_row(HEADER, 1)
            insert_position = 2
        else:
            insert_position = 2

        data_to_insert = [_row_to_list(row) for row in rows]

        log.info("Вставляю %s строк на позицию %s (новые сверху, старые уезжают вниз)",
                 len(data_to_insert), insert_position)

        worksheet.insert_rows(data_to_insert, insert_position)

        log.info("Экспорт завершён: %s строк добавлено", len(rows))

    except APIError as e:
        log.error("Ошибка Google API при записи: %s", e)
    except Exception as e:
        log.error("Ошибка при экспорте: %s", e)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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
    print("   Проверьте таблицу: заголовок + 3 строки\n")

    print(f"2. Второй экспорт (дата={today}, 2 строки)...")
    export(test_rows_2)
    print("   Проверьте таблицу: заголовок + 2 новые строки СВЕРХУ + 3 старые строки СНИЗУ\n")

    print("Ожидаемая структура листа:")
    print("  Строка 1: Заголовок (Дата | Поисковая система | Субъект/Запрос | ...)")
    print(f"  Строка 2: {today} | Google | chempioil | Литва | 1 | ...")
    print(f"  Строка 3: {today} | Google | chempioil | Литва | 3 | ...")
    print(f"  Строка 4: {yesterday} | Google | chempioil | Литва | 1 | ...")
    print(f"  Строка 5: {yesterday} | Google | chempioil | Литва | 2 | ...")
    print(f"  Строка 6: {yesterday} | Яндекс | juri sudheimer | Германия | 1 | ...")
    print()

    print("=== Тест завершён ===")
    print("Откройте Google Sheet и проверьте порядок строк визуально.")
