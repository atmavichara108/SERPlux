"""
Тесты SANDBOX-веток exporter.py и reporter.py (Фаза 1.2c песочницы SERPlux).

SANDBOX_MODE=1: вместо Google Sheets пишутся JSON-снимки (export.json/report.json).
Тесты проверяют обе ветки: sandbox пишет файл с корректной структурой,
gspread НЕ вызывается; обычный режим работает как раньше.
"""

import json

from unittest.mock import MagicMock, patch

import exporter
import reporter


class TestExporterSandbox:
    """SANDBOX_MODE=1: export() пишет JSON, gspread не трогается."""

    def test_sandbox_export_writes_json(self, tmp_path, monkeypatch):
        """export() при SANDBOX_MODE=1 пишет {written_at, count, rows} в JSON."""
        out = tmp_path / "export.json"
        monkeypatch.setattr(exporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(exporter, "SANDBOX_EXPORT_PATH", str(out))

        rows = [{
            "date": "2026-09-07", "searcher": "google", "query": "q1",
            "geo": "Литва", "position": 1, "url": "https://a.example.com/",
            "domain": "a.example.com", "snippet": "s", "label": "positive",
        }]
        exporter.export(rows)

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["count"] == 1
        assert data["rows"][0]["domain"] == "a.example.com"
        assert "written_at" in data

    def test_sandbox_export_skips_gspread(self, tmp_path, monkeypatch):
        """При SANDBOX_MODE=1 gspread не вызывается вовсе."""
        out = tmp_path / "export.json"
        monkeypatch.setattr(exporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(exporter, "SANDBOX_EXPORT_PATH", str(out))

        with patch("exporter.gspread.service_account") as fake_sa:
            exporter.export([{"date": "d", "searcher": "google", "query": "q",
                              "geo": "g", "position": 1, "url": "https://u/",
                              "domain": "u", "snippet": "", "label": None}])
            fake_sa.assert_not_called()

    def test_sandbox_export_empty_rows_noop(self, tmp_path, monkeypatch):
        """Пустой список строк → export() не пишет файл (как и в обычной ветке)."""
        out = tmp_path / "export.json"
        monkeypatch.setattr(exporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(exporter, "SANDBOX_EXPORT_PATH", str(out))

        exporter.export([])
        assert not out.exists()

    def test_sandbox_export_creates_parent_dirs(self, tmp_path, monkeypatch):
        """Отсутствующий родительский каталог создаётся автоматически."""
        out = tmp_path / "deep" / "nested" / "export.json"
        monkeypatch.setattr(exporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(exporter, "SANDBOX_EXPORT_PATH", str(out))

        exporter.export([{"date": "d", "searcher": "google", "query": "q",
                          "geo": "g", "position": 1, "url": "https://u/",
                          "domain": "u", "snippet": "", "label": None}])
        assert json.loads(out.read_text(encoding="utf-8"))["count"] == 1

    def test_normal_mode_untouched(self, monkeypatch):
        """SANDBOX_MODE выключен: export идёт по классической gspread-ветке."""
        monkeypatch.setattr(exporter, "SANDBOX_MODE", False)
        with patch("exporter._get_spreadsheet") as fake_ss:
            with patch("exporter._get_or_create_cache_sheet") as fake_ws:
                fake_ss.return_value = MagicMock()
                fake_ws.return_value = MagicMock()
                exporter.export([{"date": "d", "searcher": "google", "query": "q",
                                  "geo": "g", "position": 1, "url": "https://u/",
                                  "domain": "u", "snippet": "", "label": None}])
                fake_ss.assert_called_once()
                fake_ws.return_value.update.assert_called_once()


class TestReporterSandbox:
    """SANDBOX_MODE=1: build_report пишет JSON-снимок матрицы."""

    def test_sandbox_report_writes_json(self, tmp_path, monkeypatch):
        """build_report() при SANDBOX_MODE пишет {written_at, date, matrix, format_cells}."""
        out = tmp_path / "report.json"
        monkeypatch.setattr(reporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(reporter, "SANDBOX_REPORT_PATH", str(out))

        with patch.object(reporter, "get_client") as fake_client, \
             patch.object(reporter, "get_history") as fake_history:
            fake_client.return_value = {
                "queries": [{"key": "sct chemicals", "display": "SCT"}],
                "regions_map": [{"geo_name": "Литва"}],
                "searchers": ["google"],
            }
            fake_history.return_value = [
                {"date": "2026-09-07", "searcher": "google", "geo": "Литва",
                 "query": "sct chemicals", "position": 1,
                 "url": "https://a.example.com/", "domain": "a.example.com",
                 "snippet": "s", "label": "positive"},
            ]
            reporter.build_report(date="2026-09-07")

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["date"] == "2026-09-07"
        assert data["rows_count"] > 0
        assert isinstance(data["matrix"], list)
        # формат-ячейки содержат positive-заливку
        assert any(fc["color"] == reporter.LABEL_COLORS["positive"] for fc in data["format_cells"])

    def test_sandbox_report_skips_gspread(self, tmp_path, monkeypatch):
        """При SANDBOX_MODE=1 gspread не вызывается."""
        out = tmp_path / "report.json"
        monkeypatch.setattr(reporter, "SANDBOX_MODE", True)
        monkeypatch.setattr(reporter, "SANDBOX_REPORT_PATH", str(out))

        with patch.object(reporter, "get_client") as fake_client, \
             patch.object(reporter, "get_history") as fake_history, \
             patch("reporter.gspread.service_account") as fake_sa:
            fake_client.return_value = {
                "queries": [{"key": "q", "display": "Q"}],
                "regions_map": [{"geo_name": "Литва"}],
                "searchers": ["google"],
            }
            fake_history.return_value = [
                {"date": "2026-09-07", "searcher": "google", "geo": "Литва",
                 "query": "q", "position": 1, "url": "https://u/",
                 "domain": "u", "snippet": "", "label": None},
            ]
            reporter.build_report(date="2026-09-07")
            fake_sa.assert_not_called()

    def test_normal_mode_untouched(self, monkeypatch):
        """SANDBOX_MODE выключен: build_report идёт по классической ветке Sheets."""
        monkeypatch.setattr(reporter, "SANDBOX_MODE", False)
        with patch.object(reporter, "get_client") as fake_client, \
             patch.object(reporter, "get_history") as fake_history, \
             patch("reporter._get_spreadsheet") as fake_ss:
            fake_client.return_value = {
                "queries": [{"key": "q", "display": "Q"}],
                "regions_map": [{"geo_name": "Литва"}],
                "searchers": ["google"],
            }
            fake_history.return_value = [
                {"date": "2026-09-07", "searcher": "google", "geo": "Литва",
                 "query": "q", "position": 1, "url": "https://u/",
                 "domain": "u", "snippet": "", "label": None},
            ]
            fake_ss.return_value = None  # не найдена — build_report вернётся молча
            reporter.build_report(date="2026-09-07")
            fake_ss.assert_called_once()
