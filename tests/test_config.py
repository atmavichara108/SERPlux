"""
test_config.py — проверка консистентности config.py.

Проверяет:
- SUBJECT_BLOCKS непустой
- pos/url индексы не выходят за COLS
- индексы не пересекаются между блоками
- GEO_ORDER непустой и все гео есть в GEO_DISPLAY
- REPORT_DEPTH > 0
"""

import sys
import os

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


def test_subject_blocks_nonempty():
    """SUBJECT_BLOCKS должен содержать хотя бы один субъект."""
    assert isinstance(config.SUBJECT_BLOCKS, list)
    assert len(config.SUBJECT_BLOCKS) > 0, "SUBJECT_BLOCKS пустой"


def test_subject_blocks_required_keys():
    """Каждый блок содержит обязательные ключи нужных типов."""
    for i, block in enumerate(config.SUBJECT_BLOCKS):
        assert "key" in block,     f"SUBJECT_BLOCKS[{i}]: отсутствует 'key'"
        assert "display" in block, f"SUBJECT_BLOCKS[{i}]: отсутствует 'display'"
        assert "pos" in block,     f"SUBJECT_BLOCKS[{i}]: отсутствует 'pos'"
        assert "url" in block,     f"SUBJECT_BLOCKS[{i}]: отсутствует 'url'"
        assert isinstance(block["pos"], int), f"SUBJECT_BLOCKS[{i}]['pos'] должен быть int"
        assert isinstance(block["url"], int), f"SUBJECT_BLOCKS[{i}]['url'] должен быть int"
        assert isinstance(block["key"], str), f"SUBJECT_BLOCKS[{i}]['key'] должен быть str"
        assert block["key"].strip(), f"SUBJECT_BLOCKS[{i}]['key'] не должен быть пустым"


def test_cols_positive():
    """COLS должен быть положительным целым."""
    assert isinstance(config.COLS, int), "COLS должен быть int"
    assert config.COLS > 0, "COLS должен быть > 0"


def test_subject_indexes_within_cols():
    """pos и url индексы каждого блока не выходят за COLS (0-indexed)."""
    for i, block in enumerate(config.SUBJECT_BLOCKS):
        assert 0 <= block["pos"] < config.COLS, (
            f"SUBJECT_BLOCKS[{i}]['pos']={block['pos']} выходит за COLS={config.COLS}"
        )
        assert 0 <= block["url"] < config.COLS, (
            f"SUBJECT_BLOCKS[{i}]['url']={block['url']} выходит за COLS={config.COLS}"
        )


def test_subject_pos_url_different():
    """pos и url индексы одного блока не должны совпадать."""
    for i, block in enumerate(config.SUBJECT_BLOCKS):
        assert block["pos"] != block["url"], (
            f"SUBJECT_BLOCKS[{i}]: pos и url совпадают ({block['pos']})"
        )


def test_subject_indexes_no_cross_block_collision():
    """
    Индексы pos/url не пересекаются между разными блоками.
    Каждая колонка должна принадлежать только одному субъекту.
    """
    used: dict[int, str] = {}
    for block in config.SUBJECT_BLOCKS:
        for col_type, col_idx in [("pos", block["pos"]), ("url", block["url"])]:
            key = col_idx
            if key in used:
                pytest.fail(
                    f"Колонка {col_idx} используется в блоке '{block['key']}' ({col_type}) "
                    f"и уже занята блоком '{used[key]}'"
                )
            used[key] = block["key"]


def test_geo_order_nonempty():
    """GEO_ORDER должен содержать хотя бы одно гео."""
    assert isinstance(config.GEO_ORDER, list)
    assert len(config.GEO_ORDER) > 0, "GEO_ORDER пустой"


def test_geo_order_in_geo_display():
    """Все гео из GEO_ORDER должны быть в GEO_DISPLAY."""
    missing = [g for g in config.GEO_ORDER if g not in config.GEO_DISPLAY]
    assert not missing, (
        f"Гео из GEO_ORDER отсутствуют в GEO_DISPLAY: {missing}"
    )


def test_report_depth_positive():
    """REPORT_DEPTH должен быть положительным целым."""
    assert isinstance(config.REPORT_DEPTH, int), "REPORT_DEPTH должен быть int"
    assert config.REPORT_DEPTH > 0, "REPORT_DEPTH должен быть > 0"


def test_geo_display_values_nonempty():
    """Все значения GEO_DISPLAY — непустые строки."""
    for geo_key, display in config.GEO_DISPLAY.items():
        assert isinstance(display, str) and display.strip(), (
            f"GEO_DISPLAY['{geo_key}'] пустое или не строка: {display!r}"
        )
