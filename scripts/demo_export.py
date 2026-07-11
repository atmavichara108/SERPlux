#!/usr/bin/env python3
"""
Демо-прогон exporter.py для ручной отладки.

Запуск ТОЛЬКО при SERPLUX_DEMO=1:
    SERPLUX_DEMO=1 python scripts/demo_export.py

В проде переменная SERPLUX_DEMO не выставлена — скрипт ничего не делает.
"""

import os
import sys
from datetime import datetime, timedelta

if os.environ.get("SERPLUX_DEMO") != "1":
    print("SERPLUX_DEMO != 1 — выход. Для запуска: SERPLUX_DEMO=1 python scripts/demo_export.py")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from exporter import export, CACHE_SHEET_NAME

log = config.setup_logging(__name__)

Row = dict


def main():
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

    print("=== Демо-прогон exporter.py (SERPLUX_DEMO=1) ===\n")

    print(f"1. Первый экспорт (дата={yesterday}, 3 строки)...")
    export(test_rows_1)
    print(f"   Лист '{CACHE_SHEET_NAME}': заголовок + 3 строки\n")

    print(f"2. Второй экспорт (дата={today}, 2 строки)...")
    export(test_rows_2)
    print(f"   Лист '{CACHE_SHEET_NAME}': ТОЛЬКО заголовок + 2 строки (перезапись)\n")

    print("=== Демо завершено ===")


if __name__ == "__main__":
    main()
