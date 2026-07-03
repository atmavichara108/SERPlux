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
    monkeypatch.setattr(main_module, "_ensure_db", lambda: None)
    monkeypatch.setattr(main_module, "save", lambda rows, client_id="default": len(rows))
    label_spy = MagicMock(return_value=sample_rows)
    monkeypatch.setattr(main_module, "label", label_spy)
    monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
    monkeypatch.setattr(main_module, "export", lambda rows: None)
    monkeypatch.setattr(main_module, "build_report", lambda: None)
    return label_spy


class TestMainPipelineParams:
    """Группа тестов проброса параметров в main.run."""

    def test_run_passes_client_id_to_save(self, monkeypatch, sample_rows):
        """client_id из config попадает в storage.save()."""
        monkeypatch.setattr(main_module, "collect", lambda config: sample_rows)
        monkeypatch.setattr(main_module, "_ensure_db", lambda: None)
        save_spy = MagicMock(return_value=len(sample_rows))
        monkeypatch.setattr(main_module, "save", save_spy)
        monkeypatch.setattr(main_module, "label", lambda rows, **kwargs: sample_rows)
        monkeypatch.setattr(main_module, "insert_labels", lambda rows: len(rows))
        monkeypatch.setattr(main_module, "export", lambda rows: None)
        monkeypatch.setattr(main_module, "build_report", lambda: None)

        exit_code = main_module.run({"client_id": "client-a"})
        assert exit_code == 0
        save_spy.assert_called_once()
        assert save_spy.call_args.kwargs["client_id"] == "client-a"

    def test_run_passes_label_params_to_labeler(self, mock_pipeline, sample_rows):
        """label_mode, force_relabel и client_id пробрасываются в labeler.label()."""
        label_spy = mock_pipeline
        exit_code = main_module.run(
            {
                "with_labels": True,
                "client_id": "acme",
                "label_mode": "snippets",
                "force_relabel": True,
            }
        )
        assert exit_code == 0
        label_spy.assert_called_once()
        assert label_spy.call_args.args[0] == sample_rows
        kwargs = label_spy.call_args.kwargs
        assert kwargs["client_id"] == "acme"
        assert kwargs["label_mode"] == "snippets"
        assert kwargs["force_relabel"] is True

    def test_run_label_defaults(self, mock_pipeline):
        """Если параметры не заданы, используются дефолты."""
        label_spy = mock_pipeline
        exit_code = main_module.run({"with_labels": True})
        assert exit_code == 0
        kwargs = label_spy.call_args.kwargs
        assert kwargs["client_id"] == "default"
        assert kwargs["label_mode"] == "domains"
        assert kwargs["force_relabel"] is False

    def test_run_with_labels_false_skips_label(self, mock_pipeline):
        """with_labels=False отключает вызов labeler."""
        label_spy = mock_pipeline
        exit_code = main_module.run({"with_labels": False})
        assert exit_code == 0
        label_spy.assert_not_called()
