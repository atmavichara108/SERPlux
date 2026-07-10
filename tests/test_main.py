"""
T-00Z — тесты main.py: проброс параметров в storage и labeler.

Проверяем:
- client_id передаётся в save()
- label_mode/force_relabel/client_id передаются в label()
- with_labels=False пропускает разметку
"""

from unittest.mock import MagicMock

import pytest

import main as main_module


@pytest.fixture
def sample_rows():
    return [
        {
            "date": "2026-07-03",
            "searcher": "google",
            "query": "subject X",
            "geo": "Литва",
            "region_index": 1300,
            "position": 1,
            "url": "https://example.com/page",
            "domain": "example.com",
            "snippet": "snippet",
        }
    ]


@pytest.fixture
def mock_pipeline(monkeypatch, sample_rows):
    """Мокает все шаги пайплайна, кроме самой оркестрации main.run."""
    monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
    monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
    monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
    label_spy = MagicMock(return_value=sample_rows)
    monkeypatch.setattr(main_module, "label", label_spy)
    monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
    monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)
    monkeypatch.setattr(main_module, "build_report", lambda force=False, sheet_id=None: None)
    return label_spy


class TestMainPipelineParams:
    """Группа тестов проброса параметров в main.run."""

    def test_run_passes_client_id_to_save(self, monkeypatch, sample_rows):
        """client_id из config попадает в storage.save()."""
        monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        save_spy = MagicMock(return_value=len(sample_rows))
        monkeypatch.setattr(main_module, "save", save_spy)
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)
        monkeypatch.setattr(main_module, "build_report", lambda force=False, sheet_id=None: None)

        result = main_module.run({"client_id": "client-a"})
        assert result["exit_code"] == 0
        save_spy.assert_called_once()
        assert save_spy.call_args.kwargs["client_id"] == "client-a"

    def test_run_passes_label_params_to_labeler(self, mock_pipeline, sample_rows):
        """label_mode, force_relabel, client_id и provider_chain пробрасываются в labeler.label()."""
        label_spy = mock_pipeline
        result = main_module.run(
            {
                "with_labels": True,
                "client_id": "acme",
                "label_mode": "auto",
                "force_relabel": True,
                "provider_chain": "zen",
            }
        )
        assert result["exit_code"] == 0
        label_spy.assert_called_once()
        assert label_spy.call_args.args[0] == sample_rows
        kwargs = label_spy.call_args.kwargs
        assert kwargs["client_id"] == "acme"
        assert kwargs["label_mode"] == "auto"
        assert kwargs["force_relabel"] is True
        assert kwargs["provider_chain"] == "zen"

    def test_run_label_defaults(self, mock_pipeline):
        """Если параметры не заданы, используются дефолты."""
        label_spy = mock_pipeline
        result = main_module.run({"with_labels": True})
        assert result["exit_code"] == 0
        kwargs = label_spy.call_args.kwargs
        assert kwargs["client_id"] == "default"
        assert kwargs["label_mode"] == "auto"
        assert kwargs["force_relabel"] is False

    def test_run_with_labels_false_skips_label(self, mock_pipeline):
        """with_labels=False отключает вызов labeler."""
        label_spy = mock_pipeline
        result = main_module.run({"with_labels": False})
        assert result["exit_code"] == 0
        label_spy.assert_not_called()

    def test_run_returns_stats(self, monkeypatch, sample_rows):
        """main.run() возвращает dict с exit_code и stats."""
        monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        monkeypatch.setattr(main_module, "save", lambda rows, client_id="default", db_path=None: len(rows))
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows, db_path=None: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)
        monkeypatch.setattr(main_module, "build_report", lambda force=False, sheet_id=None: None)

        result = main_module.run({"client_id": "acme"})
        assert isinstance(result, dict)
        assert result["exit_code"] == 0
        assert result["stats"]["collected"] == 1
        assert result["stats"]["saved_new"] == 1
        assert result["stats"]["labeled"] == 1

    def test_run_passes_sheet_id_to_export_and_report(self, monkeypatch, sample_rows):
        """sheet_id из config попадает в export() и build_report()."""
        monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))

        export_spy = MagicMock()
        report_spy = MagicMock()
        monkeypatch.setattr(main_module, "export", export_spy)
        monkeypatch.setattr(main_module, "build_report", report_spy)

        result = main_module.run({"client_id": "acme", "sheet_id": "acme-sheet-id"})
        assert result["exit_code"] == 0

        export_spy.assert_called_once()
        assert export_spy.call_args.kwargs["sheet_id"] == "acme-sheet-id"

        report_spy.assert_called_once()
        assert report_spy.call_args.kwargs["sheet_id"] == "acme-sheet-id"

    def test_run_passes_force_rebuild_report_to_report(self, monkeypatch, sample_rows):
        """force_rebuild_report из config попадает в build_report()."""
        monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)

        report_spy = MagicMock()
        monkeypatch.setattr(main_module, "build_report", report_spy)

        result = main_module.run({"client_id": "acme", "force_rebuild_report": True})
        assert result["exit_code"] == 0
        assert report_spy.call_args.kwargs["force"] is True

    def test_run_passes_searchers_geos_project_id_to_collector(self, monkeypatch, sample_rows):
        """searchers, geos, project_id из config попадают в collect()."""
        collect_spy = MagicMock(return_value=sample_rows)
        monkeypatch.setattr(main_module, "collect", collect_spy)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)
        monkeypatch.setattr(main_module, "build_report", lambda force=False, sheet_id=None: None)

        result = main_module.run({
            "client_id": "acme",
            "project_id": 999,
            "searchers": ["google"],
            "geos": ["Литва"],
            "regions_map": "regions_map_acme.json",
        })
        assert result["exit_code"] == 0

        collect_spy.assert_called_once()
        cfg = collect_spy.call_args.args[0]
        assert cfg["project_id"] == 999
        assert cfg["searchers"] == ["google"]
        assert cfg["geos"] == ["Литва"]
        assert cfg["regions_map"] == "regions_map_acme.json"

    def test_run_uses_default_searchers_geos_when_missing(self, monkeypatch, sample_rows):
        """Если searchers/geos не заданы, используются DEFAULT_CONFIG."""
        collect_spy = MagicMock(return_value=sample_rows)
        monkeypatch.setattr(main_module, "collect", collect_spy)
        monkeypatch.setattr(main_module, "_ensure_db", lambda db_path=None: None)
        monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows, sheet_id=None: None)
        monkeypatch.setattr(main_module, "build_report", lambda force=False, sheet_id=None: None)

        result = main_module.run({"client_id": "default"})
        assert result["exit_code"] == 0

        cfg = collect_spy.call_args.args[0]
        assert cfg["searchers"] == ["google", "yandex_ru", "yandex_com"]
        assert "Литва" in cfg["geos"]
