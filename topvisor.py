import os
import time
import logging
from urllib.parse import urlparse
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

BASE_URL = "https://api.topvisor.com/v2/json"

Row = dict[str, Any]

SEARCHER_MAP = {
    0: "yandex_ru",
    1: "google",
    20: "yandex_com",
}

# Lazy credentials — не читаются при импорте
_credentials: dict[str, Any] = {}


def _get_env(key: str) -> str:
    """Безопасное получение переменной окружения с понятным сообщением."""
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"Переменная окружения {key} не установлена. "
            f"Проверьте файл .env (см. .env.example)"
        )
    return value


def _get_credentials() -> dict[str, Any]:
    """Lazy загрузка credentials. Не падает при импорте модуля."""
    if not _credentials:
        _credentials["user_id"] = _get_env("TOPVISOR_USER_ID")
        _credentials["api_key"] = _get_env("TOPVISOR_API_KEY")
        _credentials["project_id"] = int(_get_env("TOPVISOR_PROJECT_ID"))
    return _credentials


def get_project_id() -> int:
    """Публичный геттер project_id."""
    return _get_credentials()["project_id"]


def _get_headers() -> dict[str, str]:
    """Создание headers с lazy загрузкой credentials."""
    creds = _get_credentials()
    return {
        "User-Id": creds["user_id"],
        "Authorization": f"Bearer {creds['api_key']}",
        "Content-Type": "application/json",
    }


def _post(service: str, method: str, payload: dict) -> dict | None:
    """
    POST запрос к topvisor API с обработкой ошибок.
    Возвращает result или None при ошибке.
    """
    url = f"{BASE_URL}/{service}/{method}"
    try:
        resp = requests.post(url, json=payload, headers=_get_headers(), timeout=(10, 60))
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        log.error("Timeout при запросе к %s/%s", service, method)
        return None
    except requests.exceptions.ConnectionError:
        log.error("Ошибка соединения при запросе к %s/%s", service, method)
        return None
    except requests.exceptions.RequestException as e:
        log.error("Ошибка запроса к %s/%s: %s", service, method, e)
        return None

    data = resp.json()
    if "error" in data:
        log.error("API error: %s", data["error"])
        return None
    if "errors" in data and data["errors"]:
        log.error("API errors: %s", data["errors"])
        return None
    return data.get("result")


def list_regions() -> list[dict[str, Any]]:
    """
    Получает карту параметров всех регионов проекта.
    Возвращает плоский список dict, по одному на каждую связку ПС×регион:
      searcher_key, searcher_name, region_key, region_lang, region_device,
      region_index, name, type, country_code, domain
    """
    result = _post("get", "projects_2/projects", {
        "show_searchers_and_regions": 2,
        "filters": [{"name": "id", "operator": "EQUALS", "values": [_get_credentials()["project_id"]]}],
    })
    if not result:
        log.error("Проект %s не найден", _get_credentials()["project_id"])
        return []
    project = result[0] if isinstance(result, list) else result
    searchers = project.get("searchers", [])
    region_map: list[dict[str, Any]] = []
    for s in searchers:
        searcher_key = s.get("key")
        searcher_name = s.get("name", "")
        for r in s.get("regions", []):
            region_map.append({
                "searcher_key": searcher_key,
                "searcher_name": searcher_name,
                "region_key": r.get("key"),
                "region_lang": r.get("lang"),
                "region_device": r.get("device"),
                "region_index": r.get("index"),
                "name": r.get("name"),
                "type": r.get("type"),
                "country_code": r.get("countryCode"),
                "domain": r.get("domain"),
            })
    log.info("Проект %s (id=%s): %s связок ПС×регион",
             project.get("name"), project.get("id"), len(region_map))
    return region_map


def run_check(project_id: int, depth: int, region_indexes: list[int]) -> list[int]:
    """
    Запускает проверку позиций со сбором снимка.
    Вызывает edit/positions_2/checker/go с do_snapshots=1.
    Параметр depth зарезервирован для будущего использования.
    Возвращает projectsIds, отправленные на проверку.
    """
    log.info("Запуск проверки: project=%s, regions=%s, depth=%s", 
             project_id, region_indexes, depth)
    result = _post("edit", "positions_2/checker/go", {
        "filters": [{"name": "id", "operator": "EQUALS", "values": [project_id]}],
        "regions_indexes": region_indexes,
        "do_snapshots": True,
    })
    if result is None:
        log.error("Не удалось запустить проверку")
        return []
    ids = result.get("projectsIds", [])
    log.info("Запущена проверка проектов: %s", ids)
    return ids


def poll_status(project_id: int, timeout_sec: int = 600) -> bool:
    """
    Опрашивает процент готовности проверки до 100% или таймаута.
    Возвращает True если готово, False при таймауте или ошибке.
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
                 depth: int, searcher_key: int = 1, region_key: int = 117,
                 region_lang: str = "lt", region_device: int = 0,
                 geo: str = "") -> list[Row]:
    """
    Получает собранный ТОП через get/snapshots_2/history.
    Возвращает list[Row] с заполненными полями кроме label (=None).
    
    Параметры региона (searcher_key, region_key, region_lang, region_device)
    должны соответствовать настройкам проекта в topvisor.
    Параметр depth зарезервирован для будущего использования.
    """
    log.info("Получение снимка: project=%s, region=%s, date=%s, searcher_key=%s",
             project_id, region_index, date, searcher_key)
    result = _post("get", "snapshots_2/history", {
        "project_id": project_id,
        "regions_indexes": [region_index],
        "searcher_key": searcher_key,
        "region_key": region_key,
        "region_lang": region_lang,
        "region_device": region_device,
        "date1": date,
        "date2": date,
        "history_fields": ["url", "domain", "snippet_title", "snippet_body"],
    })
    if result is None:
        log.error("snapshots_2/history вернул None")
        return []
    keywords = result.get("keywords", []) if isinstance(result, dict) else []
    searcher_name = SEARCHER_MAP.get(searcher_key, "unknown")
    rows: list[Row] = []
    for kw in keywords:
        name = kw.get("name", "")
        snapshots_data = kw.get("snapshotsData", {})
        if not isinstance(snapshots_data, dict):
            continue
        for key, val in snapshots_data.items():
            if not isinstance(val, dict):
                continue
            parts = key.split(":")
            if len(parts) < 2:
                continue
            try:
                position = int(parts[1])
            except (ValueError, IndexError):
                continue
            url = val.get("url", "")
            if not url:
                continue
            domain = val.get("domain") or urlparse(url).netloc or ""
            snippet = f"{val.get('snippet_title', '')} {val.get('snippet_body', '')}".strip()
            rows.append({
                "date": date,
                "searcher": searcher_name,
                "query": name,
                "geo": geo,
                "region_index": region_index,
                "position": position,
                "url": url,
                "domain": domain,
                "snippet": snippet,
                "label": None,
            })
    rows.sort(key=lambda r: (r["query"], r["position"]))
    log.info("Получено %s строк из снимка", len(rows))
    return rows


def list_keywords(project_id: int, limit: int = 10) -> list[str]:
    """
    Возвращает список ключевых запросов проекта.
    Используется для диагностики и тестирования.
    """
    result = _post("get", "keywords_2/keywords", {
        "project_id": project_id,
        "fields": ["name"],
        "limit": limit,
    })
    if not result:
        return []
    keywords = [kw.get("name", "") for kw in result if kw.get("name")]
    return keywords


def snapshot_exists(project_id: int, region_index: int, date: str,
                    searcher_key: int = 1, region_key: int = 117,
                    region_lang: str = "lt", region_device: int = 0) -> bool:
    """
    Проверяет наличие снимка за указанную дату.
    Используется для идемпотентности — избежания повторных проверок.
    """
    result = _post("get", "snapshots_2/history", {
        "project_id": project_id,
        "regions_indexes": [region_index],
        "searcher_key": searcher_key,
        "region_key": region_key,
        "region_lang": region_lang,
        "region_device": region_device,
        "date1": date,
        "date2": date,
        "history_fields": ["url"],
    })
    if result is None:
        return False
    keywords = result.get("keywords", []) if isinstance(result, dict) else []
    for kw in keywords:
        if kw.get("snapshotsData"):
            return True
    return False


if __name__ == "__main__":
    import sys
    from datetime import date as date_type

    REGION_INDEX = int(os.environ.get("TOPVISOR_REGION_INDEX", "1300"))
    SEARCHER_KEY = int(os.environ.get("TOPVISOR_SEARCHER_KEY", "1"))
    REGION_KEY = int(os.environ.get("TOPVISOR_REGION_KEY", "117"))
    REGION_LANG = os.environ.get("TOPVISOR_REGION_LANG", "lt")
    REGION_DEVICE = int(os.environ.get("TOPVISOR_REGION_DEVICE", "0"))
    GEO = os.environ.get("TOPVISOR_GEO", "Литва")
    DEPTH = int(os.environ.get("TOPVISOR_DEPTH", "10"))

    if len(sys.argv) > 1 and sys.argv[1] == "--list-regions":
        regions = list_regions()
        print(f"\n{'searcher_key':>12} {'searcher_name':<12} {'region_key':>10} "
              f"{'region_lang':>11} {'region_device':>13} {'region_index':>12} "
              f"{'name':<25} {'type':<8} {'country':<8} {'domain':<10}")
        print("-" * 140)
        for r in regions:
            print(f"{r['searcher_key']:>12} {r['searcher_name']:<12} {r['region_key']:>10} "
                  f"{r['region_lang']:>11} {r['region_device']:>13} {r['region_index']:>12} "
                  f"{r['name']:<25} {r['type']:<8} {r['country_code']:<8} {r['domain']:<10}")
        print(f"\nВсего связок: {len(regions)}")
        sys.exit(0)

    today = date_type.today().isoformat()
    log.info("=== Вертикальный срез: %s, %s (region_index=%s) ===",
             SEARCHER_MAP.get(SEARCHER_KEY, "unknown"), GEO, REGION_INDEX)

    project_id = _get_credentials()["project_id"]

    if snapshot_exists(project_id, REGION_INDEX, today, SEARCHER_KEY,
                       REGION_KEY, REGION_LANG, REGION_DEVICE):
        log.info("Снимок за %s уже существует, пропускаю проверку", today)
    else:
        log.info("Снимок за %s отсутствует, запускаю проверку", today)
        ids = run_check(project_id, DEPTH, [REGION_INDEX])
        if not ids:
            log.error("Не удалось запустить проверку")
            sys.exit(1)
        if not poll_status(project_id, timeout_sec=600):
            log.error("Таймаут ожидания проверки")
            sys.exit(1)

    rows = get_snapshot(project_id, REGION_INDEX, today, DEPTH,
                        SEARCHER_KEY, REGION_KEY, REGION_LANG, REGION_DEVICE, GEO)
    log.info("Получено строк: %s", len(rows))

    for i, row in enumerate(rows[:15], 1):
        log.info("%2d. query='%s' pos=%s domain=%s",
                 i, row["query"][:30], row["position"], row["domain"][:35])
