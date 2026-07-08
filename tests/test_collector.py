"""
T-00C — тесты collector.py: работа с regions_map из профиля клиента.
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import collector


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
