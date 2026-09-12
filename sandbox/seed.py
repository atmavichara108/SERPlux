#!/usr/bin/env python3
"""seed.py — наполнение песочницы SERPlux детерминированными данными.

Создаёт SQLite БД (путь из SANDBOX_DB_PATH, default ./sandbox/generated/serplux.db):
  - schema через storage._init_db (та же, что в проде)
  - клиент "default": профиль с субъектами/гео из fixture snapshot.json
    (заполняет Sheet-less контур: build_report берёт queries/regions_map из клиента)
  - пустые labels/positions: первый прогон pipeline сам их заполнит

Запуск (локально или в контейнере):
    python3 sandbox/seed.py [--db sandbox/generated/serplux.db]
Детерминизм: одна fixture — один и тот же seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# repo root на sys.path (seed запускается и из корня, и из sandbox/)
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import storage  # noqa: E402 (repo root должен быть на sys.path до импорта)

SANDBOX_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = SANDBOX_ROOT / "generated" / "serplux.db"
FIXTURES = SANDBOX_ROOT / "fixtures" / "snapshot.json"


def seed(db_path: str, fixtures_path: Path) -> dict:
    fx = json.loads(fixtures_path.read_text(encoding="utf-8"))

    # Родительский каталог БД (sandbox/generated/) — создаём до sqlite3.connect
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # _init_db создаёт схему (идемпотентно — CREATE TABLE IF NOT EXISTS)
    storage._init_db(db_path)

    # Субъекты/гео из fixture keywords/regions → профиль клиента "default".
    # Это Sheet-less контур: build_report берёт queries/regions_map отсюда.
    queries = [{"key": k["name"], "display": k["name"].upper()} for k in fx.get("keywords", [])]
    geos = []
    regions_map = []
    for s in fx.get("searchers", []):
        for r in s.get("regions", []):
            geo_name = r.get("geo_name") or r.get("name", "Vilnius")
            if geo_name not in geos:
                geos.append(geo_name)
            regions_map.append({
                "searcher_name": r.get("domain", ""),
                "geo_name": geo_name,
                "region_index": r.get("index", 1300),
                "top": r.get("top", 50),
            })

    conn = storage._get_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO clients
               (client_id, client_name, project_id, sheet_id, searchers, geos, regions_map, queries)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(client_id) DO UPDATE SET
                 client_name=excluded.client_name,
                 project_id=excluded.project_id,
                 sheet_id=excluded.sheet_id,
                 searchers=excluded.searchers,
                 geos=excluded.geos,
                 regions_map=excluded.regions_map,
                 queries=excluded.queries""",
            (
                "default",
                "Sandbox Client",
                json.dumps(fx.get("project_id", 1)),
                "",  # sheet_id пуст: SANDBOX_MODE пишет в JSON, не в Sheets
                json.dumps(["google", "yandex_ru"]),
                json.dumps(geos),
                json.dumps(regions_map),
                json.dumps(queries),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Справочник доменных меток: отдельные соединения per-call (upsert_domain_label
    # сам управляет транзакциями BEGIN IMMEDIATE); вход через публичный API.
    labels_fx_path = SANDBOX_ROOT / "fixtures" / "labels.json"
    label_count = 0
    if labels_fx_path.exists():
        labels_fx = json.loads(labels_fx_path.read_text(encoding="utf-8"))
        from storage import upsert_domain_label
        for label, domains in (
            ("positive", labels_fx.get("positives", [])),
            ("negative", labels_fx.get("negatives", [])),
            ("neutral", labels_fx.get("neutral", [])),
        ):
            for domain in domains:
                for q in queries:
                    upsert_domain_label(
                        domain, q["key"],
                        sentiment=label,            # keyword-arg (позиционные *args — только legacy 2/3-арг вызовы)
                        source="manual_l1",
                        db_path=db_path,
                    )
                    label_count += 1

    # Счётчики: свежее соединение (основное закрыто в finally выше)
    conn2 = storage._get_conn(db_path)
    try:
        counts = {
            "clients": conn2.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "domain_labels": conn2.execute("SELECT COUNT(*) FROM domain_labels").fetchone()[0],
        }
    finally:
        conn2.close()
    return {"db": str(db_path), "labels_written": label_count, **counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed sandbox DB (SERPlux sandbox)")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--fixtures", default=str(FIXTURES))
    args = parser.parse_args()

    result = seed(args.db, Path(args.fixtures))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
