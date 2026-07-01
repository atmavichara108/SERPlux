"""
test_regions_map.py — валидация всех файлов regions_map*.json в корне проекта.

Проверяет:
- файл является валидным JSON
- содержит непустой список
- каждая запись содержит обязательные поля нужных типов
"""

import glob
import json
import os

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FIELDS = {
    "searcher": str,
    "searcher_key": int,
    "geo_name": str,
    "region_index": int,
    "region_key": int,
    "region_lang": str,
    "region_device": int,
}


def _find_regions_maps():
    """Возвращает список путей к regions_map*.json в корне проекта."""
    pattern = os.path.join(PROJECT_ROOT, "regions_map*.json")
    return glob.glob(pattern)


# Параметризуем по файлам — каждый файл отдельный тест-кейс
@pytest.fixture(params=_find_regions_maps(), ids=lambda p: os.path.basename(p))
def regions_map_path(request):
    return request.param


@pytest.fixture
def regions_map_data(regions_map_path):
    with open(regions_map_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_is_nonempty_list(regions_map_data, regions_map_path):
    """Файл должен содержать непустой список."""
    assert isinstance(regions_map_data, list), (
        f"{os.path.basename(regions_map_path)}: ожидался список, получен {type(regions_map_data)}"
    )
    assert len(regions_map_data) > 0, (
        f"{os.path.basename(regions_map_path)}: список пустой"
    )


def test_required_fields_present(regions_map_data, regions_map_path):
    """Каждая запись содержит все обязательные поля."""
    fname = os.path.basename(regions_map_path)
    for i, entry in enumerate(regions_map_data):
        for field in REQUIRED_FIELDS:
            assert field in entry, (
                f"{fname}[{i}]: отсутствует поле '{field}'"
            )


def test_field_types(regions_map_data, regions_map_path):
    """Типы полей соответствуют контракту."""
    fname = os.path.basename(regions_map_path)
    for i, entry in enumerate(regions_map_data):
        for field, expected_type in REQUIRED_FIELDS.items():
            if field not in entry:
                continue  # уже проверено в test_required_fields_present
            value = entry[field]
            assert isinstance(value, expected_type), (
                f"{fname}[{i}]: поле '{field}' = {value!r}, "
                f"ожидался {expected_type.__name__}, получен {type(value).__name__}"
            )


def test_region_index_positive(regions_map_data, regions_map_path):
    """region_index должен быть положительным целым."""
    fname = os.path.basename(regions_map_path)
    for i, entry in enumerate(regions_map_data):
        if "region_index" in entry:
            assert entry["region_index"] > 0, (
                f"{fname}[{i}]: region_index должен быть > 0, получен {entry['region_index']}"
            )


def test_searcher_values(regions_map_data, regions_map_path):
    """searcher должен быть одним из известных значений."""
    known = {"google", "yandex_ru", "yandex_com"}
    fname = os.path.basename(regions_map_path)
    for i, entry in enumerate(regions_map_data):
        if "searcher" in entry:
            assert entry["searcher"] in known, (
                f"{fname}[{i}]: неизвестный searcher '{entry['searcher']}', "
                f"допустимые: {known}"
            )


def test_no_duplicate_region_indexes(regions_map_data, regions_map_path):
    """region_index должен быть уникальным внутри одного файла."""
    fname = os.path.basename(regions_map_path)
    indexes = [e["region_index"] for e in regions_map_data if "region_index" in e]
    seen = set()
    duplicates = []
    for idx in indexes:
        if idx in seen:
            duplicates.append(idx)
        seen.add(idx)
    assert not duplicates, (
        f"{fname}: дублирующиеся region_index: {duplicates}"
    )
