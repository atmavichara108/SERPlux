"""
T-00Y — тесты webhook.py: POST /run.

Проверяем:
- обратную совместимость старого контракта и дефолты
- приём и проброс новых полей client_id/label_mode/force_relabel
- валидацию label_mode
- Bearer-авторизацию
"""

import pytest
from fastapi.testclient import TestClient

import webhook


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Сбрасываем глобальное состояние и задаём тестовый секрет."""
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    webhook._last_run = {"started_at": None, "status": "idle", "message": ""}
    if webhook._run_lock.locked():
        webhook._run_lock.release()


@pytest.fixture
def client():
    return TestClient(webhook.app)


@pytest.fixture
def pipeline_spy(monkeypatch):
    """Подменяет фоновый запуск, чтобы проверить аргументы."""
    captured = {}

    def fake_run_pipeline(*args):
        captured["args"] = args

    monkeypatch.setattr(webhook, "_run_pipeline", fake_run_pipeline)
    return captured


class TestRunEndpoint:
    """Группа тестов POST /run."""

    def test_run_old_contract_uses_defaults(self, client, pipeline_spy):
        """Старый контракт работает, новые поля подставляются дефолтом."""
        resp = client.post(
            "/run",
            json={"regions_map": "map.json", "with_labels": True, "depth": 5},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"] == (
            "map.json", True, 5, "default", "domains", False,
        )

    def test_run_new_fields_passed(self, client, pipeline_spy):
        """Новые поля принимаются и пробрасываются в пайплайн."""
        resp = client.post(
            "/run",
            json={
                "regions_map": "map.json",
                "client_id": "acme",
                "label_mode": "snippets",
                "force_relabel": True,
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"] == (
            "map.json", True, 10, "acme", "snippets", True,
        )

    def test_run_default_label_mode_is_domains(self, client, pipeline_spy):
        """Если label_mode не передан, дефолт — domains."""
        resp = client.post(
            "/run",
            json={"regions_map": "map.json"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"][4] == "domains"

    @pytest.mark.parametrize("mode", ["domains", "snippets", "full"])
    def test_run_valid_label_modes(self, client, pipeline_spy, mode):
        """Все допустимые режимы разметки принимаются."""
        resp = client.post(
            "/run",
            json={"label_mode": mode},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"][4] == mode

    def test_run_invalid_label_mode_returns_422(self, client):
        """Невалидный label_mode возвращает 422 с понятной ошибкой."""
        resp = client.post(
            "/run",
            json={"label_mode": "invalid"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 422
        body = resp.json()
        detail = str(body)
        assert any(m in detail for m in ["domains", "snippets", "full"])


class TestRunAuth:
    """Тесты авторизации /run."""

    def test_run_missing_auth_returns_401(self, client):
        resp = client.post("/run", json={})
        assert resp.status_code == 401

    def test_run_invalid_auth_returns_403(self, client):
        resp = client.post(
            "/run",
            json={},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 403
