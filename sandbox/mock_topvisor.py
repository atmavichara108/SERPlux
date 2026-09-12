#!/usr/bin/env python3
"""mock_topvisor.py — детерминированный мок Topvisor API для песочницы SERPlux.

Реализует ровно те endpoints, которые использует topvisor.py (контракт v1.x):
  POST /v2/json/get/projects_2/projects     — список проектов + searchers/regions,
                                              positions_percent/status_positions
  POST /v2/json/edit/positions_2/checker/go — запуск проверки → {projectsIds: [...]}
  POST /v2/json/get/snapshots_2/history     — снимок выдачи (keywords/snapshotsData)
  POST /v2/json/get/keywords_2/keywords     — список ключевых слов

Данные — из sandbox/fixtures/snapshot.json (единый fixture для всех endpoints;
поле fixtures["keywords"] — canonical источник строк выдачи).

Запуск:
    python3 sandbox/mock_topvisor.py [--port 8011] [--fixtures sandbox/fixtures/snapshot.json]

Детерминизм: answer задаётся fixture'ой; poll_status всегда сразу 100/done
(никаких ожиданий). Никакой сети наружу, secrets не используются.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "snapshot.json"

# searcher_key → searcher-имя (1:1 с SEARCHER_MAP в topvisor.py)
SEARCHER_NAMES = {0: "yandex_ru", 1: "google", 20: "yandex_com"}


def load_fixtures(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"mock_topvisor: не удалось прочитать fixture {path}: {e}", file=sys.stderr)
        raise SystemExit(2)


class Handler(BaseHTTPRequestHandler):
    fixtures: dict = {}
    log_enabled = True

    def log_message(self, fmt, *args):  # тишина: логи идут через serpctl, не спамим
        if self.log_enabled:
            super().log_message(fmt, *args)

    # ─── helpers ──────────────────────────────────────────────────────────────

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _project_view(self, fields: list[str] | None) -> dict:
        """Проект с searchers/regions из fixture (форма list_regions)."""
        fx = self.fixtures
        project = {
            "id": fx.get("project_id", 1),
            "name": fx.get("project_name", "sandbox-project"),
            "status_positions": fx.get("status_positions", "done"),
            "positions_percent": fx.get("positions_percent", 100),
            "searchers": [],
        }
        for s in fx.get("searchers", []):
            searcher = dict(s)
            searcher["key"] = s.get("key", 1)
            searcher["name"] = s.get("name", SEARCHER_NAMES.get(s.get("key", 1), "unknown"))
            searcher["regions"] = [
                {
                    "key": r.get("key", 117),
                    "lang": r.get("lang", "lt"),
                    "device": r.get("device", 0),
                    "index": r.get("index", 1300),
                    "name": r.get("name", "Vilnius"),
                    "type": r.get("type", "city"),
                    "countryCode": r.get("countryCode", "LT"),
                    "domain": r.get("domain", "google.com"),
                }
                for r in s.get("regions", [])
            ]
            project["searchers"].append(searcher)
        if fields:
            # Форма poll_status: просим только positions_percent/status_positions
            filtered = {k: project[k] for k in fields if k in project}
            filtered["id"] = project["id"]
            return filtered
        return project

    # ─── endpoints (только POST, форма topvisor._post) ───────────────────────

    def do_POST(self):  # noqa: N802 (http.server contract)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        payload = self._read_json()
        fx = self.fixtures

        if path == "/v2/json/get/projects_2/projects":
            fields = payload.get("fields")
            if fields and "positions_percent" in fields:
                return self._reply({"result": [self._project_view(fields)]})
            return self._reply({"result": [self._project_view(None)]})

        if path == "/v2/json/edit/positions_2/checker/go":
            return self._reply({"result": {"projectsIds": [fx.get("project_id", 1)]}})

        if path == "/v2/json/get/snapshots_2/history":
            return self._reply({"result": self._snapshot_result(payload)})

        if path == "/v2/json/get/keywords_2/keywords":
            kws = [{"name": k} for k in fx.get("keyword_names", [])][: payload.get("limit", 10)]
            return self._reply({"result": kws})

        return self._reply({"error": {"message": f"unknown endpoint {path}"}}, status=404)

    # ─── snapshot builder ────────────────────────────────────────────────────

    def _snapshot_result(self, payload: dict) -> dict:
        """Собирает ответ snapshots_2/history из fixture keywords.

        Каждый fixture-keyword: {name, positions: {searcher_key: [(pos, url, domain, title, body)]}}
        Разворачивается в snapshotsData в форме, которую читает get_snapshot():
        snapshotsData["{searcher_key}:{region_index}:{position}"] = {url, domain, snippet_title, snippet_body}
        """
        fx = self.fixtures
        searcher_key = payload.get("searcher_key", 1)
        region_index = (payload.get("regions_indexes") or [1300])[0]

        keywords_out = []
        for kw in fx.get("keywords", []):
            entries = (kw.get("positions") or {}).get(str(searcher_key), [])
            if not entries:
                continue
            snapshots_data = {}
            for pos, url, domain, title, body in entries:
                key = f"{searcher_key}:{region_index}:{pos}"
                snapshots_data[key] = {
                    "url": url,
                    "domain": domain,
                    "snippet_title": title,
                    "snippet_body": body,
                }
            keywords_out.append({"name": kw.get("name", ""), "snapshotsData": snapshots_data})
        return {"keywords": keywords_out}


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock Topvisor API (sandbox)")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--fixtures", default=str(FIXTURES_PATH))
    args = parser.parse_args()

    Handler.fixtures = load_fixtures(Path(args.fixtures))
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock_topvisor: listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
