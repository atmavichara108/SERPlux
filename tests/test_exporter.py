"""
T-00E — тесты exporter.py: защита от зависания Google Sheets API.
"""

from unittest.mock import MagicMock, patch

import pytest

import exporter


class TestExportTimeout:
    """Проверяем, что gspread-клиент настраивается с таймаутом."""

    def test_get_sheet_sets_http_timeout(self, monkeypatch):
        """_get_sheet устанавливает http_client.timeout для всех вызовов."""
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        monkeypatch.setenv("GOOGLE_SHEET_ID", "test-sheet-id")

        fake_worksheet = MagicMock()
        fake_spreadsheet = MagicMock()
        fake_spreadsheet.sheet1 = fake_worksheet
        fake_client = MagicMock()
        fake_client.open_by_key.return_value = fake_spreadsheet

        with patch("exporter.os.path.exists", return_value=True):
            with patch("exporter.gspread.service_account", return_value=fake_client):
                result = exporter._get_sheet()

        assert result is fake_worksheet
        assert fake_client.http_client.timeout == (10, 60)
