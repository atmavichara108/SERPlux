import os
import time
import logging
from urllib.parse import urlparse
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://api.topvisor.com/v2/json"
USER_ID = os.environ["TOPVISOR_USER_ID"]
API_KEY = os.environ["TOPVISOR_API_KEY"]
PROJECT_ID = int(os.environ["TOPVISOR_PROJECT_ID"])

HEADERS = {
    "User-Id": USER_ID,
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

Row = dict[str, Any]


def _post(service: str, method: str, payload: dict) -> dict:
    url = f"{BASE_URL}/{service}/{method}"
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"API error: {data['error']}")
    if "errors" in data and data["errors"]:
        log.error("API errors: %s", data["errors"])
    if method == "snapshots_2/history":
        log.info("DEBUG snapshots_2/history full response: %s", str(data)[:1000])
    return data.get("result", {})


def list_regions() -> None:
    """Выводит список ПС и регионов проекта для выбора region_index."""
    result = _post("get", "projects_2/projects", {
        "show_searchers_and_regions": 2,
        "filters": [{"name": "id", "operator": "EQUALS", "values": [PROJECT_ID]}],
    })
    if not result:
        log.error("Проект %s не найден", PROJECT_ID)
        return
    project = result[0] if isinstance(result, list) else result
    searchers = project.get("searchers", [])
    print(f"\nПроект: {project.get('name')} (id={project.get('id')})\n")
    for s in searchers:
        print(f"Поисковая система: {s.get('name')} (key={s.get('key')})")
        print(f"  Full searcher data: {s}")
        for r in s.get("regions", []):
            print(f"  region_index={r.get('index'):>4}  {r.get('name')}")
            print(f"    Full region data: {r}")
    print()


def run_check(project_id: int, depth: int, region_indexes: list[int]) -> list[int]:
    """
    Запускает проверку позиций со сбором снимка.
    Вызывает edit/positions_2/checker/go с do_snapshots=1.
    Глубину прокидывает в настройки проекта при необходимости.
    Возвращает projectsIds, отправленные на проверку.
    """
    result = _post("edit", "positions_2/checker/go", {
        "filters": [{"name": "id", "operator": "EQUALS", "values": [project_id]}],
        "regions_indexes": region_indexes,
        "do_snapshots": True,
    })
    ids = result.get("projectIds", [])
    log.info("Запущена проверка проектов: %s", ids)
    return ids


def poll_status(project_id: int, timeout_sec: int = 600) -> bool:
    """
    Опрашивает процент готовности проверки до 100% или таймаута.
    Возвращает True если готово, False если таймаут.
    Пауза между опросами 10 сек.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = _post("get", "projects_2/projects", {
            "fields": ["positions_percent", "status_positions"],
            "filters": [{"name": "id", "operator": "EQUALS", "values": [project_id]}],
        })
        if result:
            proj = result[0] if isinstance(result, list) else result
            pct = proj.get("positions_percent", 0)
            status = proj.get("status_positions", "")
            log.info("Проект %s: percent=%s, status=%s", project_id, pct, status)
            if pct == 100 or status == "done":
                return True
        time.sleep(10)
    log.warning("Таймаут %s сек для проекта %s", timeout_sec, project_id)
    return False


def get_snapshot(project_id: int, region_index: int, date: str,
                 depth: int) -> list[Row]:
    """
    Получает собранный ТОП через get/snapshots_2/history.
    Возвращает list[Row] с заполненными полями кроме label (=None).
    Поле domain вычисляет из url. snippet берёт из ответа если есть.
    """
    result = _post("get", "snapshots_2/history", {
        "project_id": project_id,
        "regions_indexes": [region_index],
        "searcher_key": 1,
        "region_key": 117,
        "region_lang": "lt",
        "region_device": 0,
        "date1": date,
        "date2": date,
        "history_fields": ["url", "domain", "snippet_title", "snippet_body"],
    })
    if result is None:
        log.error("snapshots_2/history вернул None")
        return []
    log.info("Snapshot result keys: %s", list(result.keys()) if isinstance(result, dict) else type(result))
    if isinstance(result, dict):
        log.info("Snapshot keywords count: %s", len(result.get("keywords", [])))
        if result.get("keywords"):
            log.info("First keyword sample: %s", list(result["keywords"][0].keys()) if result["keywords"] else "empty")
    keywords = result.get("keywords", []) if isinstance(result, dict) else []
    rows: list[Row] = []
    for kw in keywords:
        name = kw.get("name", "")
        positions_data = kw.get("positions", {})
        for pos_key, pos_val in positions_data.items():
            if not isinstance(pos_val, dict):
                continue
            url = pos_val.get("url", "")
            domain = pos_val.get("domain") or (urlparse(url).netloc if url else "")
            snippet_title = pos_val.get("snippet_title", "")
            snippet_body = pos_val.get("snippet_body", "")
            snippet = f"{snippet_title} {snippet_body}".strip()
            position = pos_val.get("position")
            if position is None:
                continue
            rows.append({
                "date": date,
                "searcher": "google",
                "query": name,
                "geo": "",
                "region_index": region_index,
                "position": int(position),
                "url": url,
                "domain": domain,
                "snippet": snippet,
                "label": None,
            })
    rows.sort(key=lambda r: r["position"])
    log.info("Получено %s строк из снимка", len(rows))
    return rows


def list_keywords(project_id: int, limit: int = 10) -> list[str]:
    """Возвращает список ключевых запросов проекта."""
    result = _post("get", "keywords_2/keywords", {
        "project_id": project_id,
        "fields": ["name"],
        "limit": limit,
    })
    if not result:
        return []
    keywords = [kw.get("name", "") for kw in result if kw.get("name")]
    return keywords


if __name__ == "__main__":
    from datetime import date as date_type

    REGION_INDEX = 1300
    DEPTH = 10

    print("=== Вертикальный срез: Google, Литва (region_index=1300) ===\n")

    print("1. Получаю список запросов проекта...")
    keywords = list_keywords(PROJECT_ID, limit=5)
    if not keywords:
        log.error("В проекте нет ключевых запросов")
        raise SystemExit(1)
    print(f"   Запросы проекта: {keywords}")
    print(f"   Беру первый запрос: '{keywords[0]}'\n")

    print("2. Запускаю проверку со снимком...")
    ids = run_check(PROJECT_ID, DEPTH, [REGION_INDEX])
    if not ids:
        log.error("Не удалось запустить проверку")
        raise SystemExit(1)
    print(f"   Проверка запущена для проектов: {ids}\n")

    print("3. Ожидаю готовности (поллинг)...")
    if not poll_status(PROJECT_ID, timeout_sec=600):
        log.error("Таймаут ожидания")
        raise SystemExit(1)
    print("   Проверка завершена!\n")

    print("4. Получаю снимок выдачи...")
    today = date_type.today().isoformat()
    rows = get_snapshot(PROJECT_ID, REGION_INDEX, today, DEPTH)
    print(f"   Получено строк: {len(rows)}\n")

    print("5. Первые 10 результатов:")
    print("-" * 80)
    for i, row in enumerate(rows[:10], 1):
        print(f"{i:2}. pos={row['position']:>3} | {row['domain'][:40]:<40} | {row['url'][:60]}")
    print("-" * 80)
