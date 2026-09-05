"""
T-00C — тесты collector.py: работа с regions_map из профиля клиента
и проброс depth в вызовы topvisor.
"""

import json
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import collector
import topvisor


def test_get_regions_map_uses_list_directly():
    """Если config['regions_map'] — список, использовать его напрямую."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    result = collector._get_regions_map({"regions_map": regions})
    assert result == regions


def test_get_regions_map_reads_file_for_string(tmp_path, monkeypatch):
    """Если config['regions_map'] — строка, читать файл."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    map_path = tmp_path / "regions_map_test.json"
    map_path.write_text(json.dumps(regions), encoding="utf-8")

    result = collector._get_regions_map({"regions_map": str(map_path)})
    assert result == regions


def test_get_regions_map_fallback_to_env(tmp_path, monkeypatch):
    """Если regions_map не задан — фоллбэк на env REGIONS_MAP."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    map_path = tmp_path / "regions_map_env.json"
    map_path.write_text(json.dumps(regions), encoding="utf-8")
    monkeypatch.setenv("REGIONS_MAP", str(map_path))

    result = collector._get_regions_map({})
    assert result == regions


def test_get_regions_map_fallback_to_default_file(tmp_path, monkeypatch):
    """Если regions_map не задан и env нет — фоллбэк на regions_map.json в корне."""
    # Убираем env REGIONS_MAP, если был
    monkeypatch.delenv("REGIONS_MAP", raising=False)
    result = collector._get_regions_map({})
    assert isinstance(result, list)
    assert len(result) > 0
    assert all("searcher" in r and "geo_name" in r for r in result)


# ─── Проброс depth в вызовы topvisor ──────────────────────────────────────────

_REGIONS = [
    {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
     "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
]


def test_collect_passes_depth_to_run_check(monkeypatch):
    """collect() должен передавать config['depth'] в run_check (запуск проверки)."""
    cfg = {"depth": 50, "searchers": ["google"], "geos": ["Литва"],
           "regions_map": _REGIONS, "project_id": 123, "date": "2026-06-19"}

    captured = {}

    def fake_snapshot_exists(*a, **k):
        return False  # снимков нет → будет запущена проверка

    def fake_run_check(project_id, depth, region_indexes):
        captured["run_check"] = (project_id, depth, region_indexes)
        return [project_id]

    def fake_poll_status(*a, **k):
        return True

    def fake_get_snapshot(*a, **k):
        return []

    monkeypatch.setattr(collector, "snapshot_exists", fake_snapshot_exists)
    monkeypatch.setattr(collector, "run_check", fake_run_check)
    monkeypatch.setattr(collector, "poll_status", fake_poll_status)
    monkeypatch.setattr(collector, "get_snapshot", fake_get_snapshot)

    collector.collect(cfg)
    assert captured["run_check"][0] == 123
    assert captured["run_check"][1] == 50
    assert captured["run_check"][2] == [1300]


def test_collect_passes_depth_to_get_snapshot(monkeypatch):
    """collect() должен передавать config['depth'] в get_snapshot (скачивание снимка)."""
    cfg = {"depth": 50, "searchers": ["google"], "geos": ["Литва"],
           "regions_map": _REGIONS, "project_id": 123, "date": "2026-06-19"}

    captured = {}

    def fake_snapshot_exists(*a, **k):
        return True  # снимок уже есть → run_check не вызывается

    def fake_get_snapshot(project_id, region_index, today, depth, **k):
        captured["get_snapshot"] = (project_id, region_index, today, depth)
        return []

    monkeypatch.setattr(collector, "snapshot_exists", fake_snapshot_exists)
    monkeypatch.setattr(collector, "get_snapshot", fake_get_snapshot)

    collector.collect(cfg)
    assert captured["get_snapshot"][0] == 123
    assert captured["get_snapshot"][1] == 1300
    assert captured["get_snapshot"][3] == 50


# ─── depth НЕ попадает в payload (Topvisor API не поддерживает глубину) ──────


def test_run_check_payload_has_no_depth_field(monkeypatch):
    """checker/go не должен получать выдуманное поле depth/limit в payload."""
    captured = {}

    def fake_post(service, method, payload):
        captured["payload"] = payload
        return {"projectsIds": [123]}

    monkeypatch.setattr(topvisor, "_post", fake_post)

    ids = topvisor.run_check(123, 50, [1300])
    assert ids == [123]
    assert "depth" not in captured["payload"]
    assert "limit" not in captured["payload"]
    assert captured["payload"]["do_snapshots"] is True


def test_get_snapshot_payload_has_no_depth_field(monkeypatch):
    """snapshots_2/history не должен получать выдуманное поле depth в payload."""
    captured = {}

    def fake_post(service, method, payload):
        captured["payload"] = payload
        return {"keywords": []}

    monkeypatch.setattr(topvisor, "_post", fake_post)

    rows = topvisor.get_snapshot(123, 1300, "2026-06-19", 50,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")
    assert rows == []
    assert "depth" not in captured["payload"]
    assert "limit" not in captured["payload"]


# ─── WARNING при depth > фактической возможности API ─────────────────────────


def test_run_check_warns_when_depth_gt_10(monkeypatch, caplog):
    """При depth > 10 run_check логирует явный WARNING о том, что API не
    принимает глубину на уровне запроса."""
    monkeypatch.setattr(topvisor, "_post",
                        lambda *a, **k: {"projectsIds": [123]})
    # setup_logging ставит propagate=False — включаем для захвата caplog
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        topvisor.run_check(123, 50, [1300])

    assert any("не поддерживает глубину" in r.message for r in caplog.records)


def test_get_snapshot_warns_when_real_depth_less_than_requested(monkeypatch, caplog):
    """Если фактическая глубина выдачи меньше запрошенного depth — WARNING с evidence."""
    fake_result = {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {
                "site:1": {"url": "http://a.example/1",
                           "snippet_title": "", "snippet_body": ""},
                "site:2": {"url": "http://b.example/2",
                           "snippet_title": "", "snippet_body": ""},
            },
        }],
    }
    monkeypatch.setattr(topvisor, "_post", lambda *a, **k: fake_result)
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        rows = topvisor.get_snapshot(123, 1300, "2026-06-19", 50,
                                     searcher_key=1, region_key=117,
                                     region_lang="lt", region_device=0, geo="Литва")

    assert len(rows) == 2
    assert any("Глубина выдачи меньше запрошенной" in r.message for r in caplog.records)


def test_no_warning_when_depth_equals_real_depth(monkeypatch, caplog):
    """При depth <= фактической глубине выдачи WARNING отсутствует."""
    fake_result = {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {
                "site:1": {"url": "http://a.example/1",
                           "snippet_title": "", "snippet_body": ""},
                "site:2": {"url": "http://b.example/2",
                           "snippet_title": "", "snippet_body": ""},
            },
        }],
    }
    monkeypatch.setattr(topvisor, "_post", lambda *a, **k: fake_result)
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        topvisor.get_snapshot(123, 1300, "2026-06-19", 2,
                              searcher_key=1, region_key=117,
                              region_lang="lt", region_device=0, geo="Литва")

    assert not any("Глубина выдачи меньше запрошенной" in r.message for r in caplog.records)
