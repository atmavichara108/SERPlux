"""
T-00E — тесты exporter.py: защита от зависания Google Sheets API.
"""

from unittest.mock import MagicMock, patch

import pytest

import exporter


class TestExportTimeout:
    """Проверяем, что gspread-клиент настраивается с таймаутом."""

    def test_get_spreadsheet_sets_http_timeout(self, monkeypatch):
        """_get_spreadsheet устанавливает http_client.timeout для всех вызовов."""
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        monkeypatch.setenv("GOOGLE_SHEET_ID", "test-sheet-id")

        fake_spreadsheet = MagicMock()
        fake_client = MagicMock()
        fake_client.open_by_key.return_value = fake_spreadsheet

        with patch("exporter.os.path.exists", return_value=True):
            with patch("exporter.gspread.service_account", return_value=fake_client):
                result = exporter._get_spreadsheet()

        assert result is fake_spreadsheet
        assert fake_client.http_client.timeout == (10, 60)


class TestExportCacheSheet:
    """Проверяем, что export пишет кэш только на лист 'Лист2'."""

    def test_export_uses_cache_sheet_and_clears_it(self, monkeypatch):
        """export очищает лист 'Лист2' и записывает туда заголовок + данные."""
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        monkeypatch.setenv("GOOGLE_SHEET_ID", "test-sheet-id")

        fake_worksheet = MagicMock()
        fake_spreadsheet = MagicMock()
        fake_spreadsheet.worksheet.return_value = fake_worksheet
        fake_client = MagicMock()
        fake_client.open_by_key.return_value = fake_spreadsheet

        test_rows = [
            {
                "date": "2026-07-10",
                "searcher": "google",
                "query": "chempioil",
                "geo": "Литва",
                "region_index": 1300,
                "position": 1,
                "url": "https://chempioil.com",
                "domain": "chempioil.com",
                "snippet": "Official site",
                "label": "positive",
            }
        ]

        with patch("exporter.os.path.exists", return_value=True):
            with patch("exporter.gspread.service_account", return_value=fake_client):
                exporter.export(test_rows)

        fake_spreadsheet.worksheet.assert_called_once_with("Лист2")
        fake_worksheet.clear.assert_called_once()
        fake_worksheet.update.assert_called_once()
        args, kwargs = fake_worksheet.update.call_args
        data = args[0]
        assert data[0] == exporter.HEADER
        assert len(data) == 2  # заголовок + 1 строка
