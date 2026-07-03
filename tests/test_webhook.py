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


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    """Создаёт изолированную БД и подменяет storage.DB_PATH для тестов /clients."""
    db_path = str(tmp_path / "clients.db")
    import storage
    storage._init_db(db_path)
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    return db_path


class TestClientsEndpoint:
    """Тесты CRUD-эндпоинтов /clients."""

    def test_list_clients_empty_only_default(self, client, client_db):
        """GET /clients возвращает только дефолтного клиента."""
        resp = client.get("/clients", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["client_id"] == "default"

    def test_list_clients_with_data(self, client, client_db):
        """GET /clients возвращает созданного клиента вместе с default."""
        import storage
        storage.create_client("acme", "Acme Corp", project_id=123, sheet_id="abc", db_path=client_db)

        resp = client.get("/clients", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        by_id = {c["client_id"]: c for c in body}
        assert "acme" in by_id
        assert by_id["acme"] == {
            "client_id": "acme",
            "client_name": "Acme Corp",
            "project_id": 123,
            "sheet_id": "abc",
        }

    def test_create_client(self, client, client_db):
        """POST /clients создаёт клиента и возвращает 201."""
        resp = client.post(
            "/clients",
            json={"client_id": "new", "client_name": "New Client", "project_id": 42},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 201
        assert resp.json()["client_id"] == "new"

    def test_create_client_optional_sheet_id(self, client, client_db):
        """POST /clients без sheet_id работает."""
        resp = client.post(
            "/clients",
            json={"client_id": "min", "client_name": "Min"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 201
        assert resp.json()["sheet_id"] is None

    def test_create_client_duplicate_returns_409(self, client, client_db):
        """Повторный POST с тем же client_id возвращает 409."""
        client.post(
            "/clients",
            json={"client_id": "dup", "client_name": "Dup"},
            headers={"Authorization": "Bearer test-secret"},
        )
        resp = client.post(
            "/clients",
            json={"client_id": "dup", "client_name": "Dup 2"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 409

    def test_get_client_existing(self, client, client_db):
        """GET /clients/{id} возвращает профиль существующего клиента."""
        import storage
        storage.create_client("one", "One", project_id=7, sheet_id="sh", db_path=client_db)

        resp = client.get("/clients/one", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        assert resp.json() == {
            "client_id": "one",
            "client_name": "One",
            "project_id": 7,
            "sheet_id": "sh",
        }

    def test_get_client_missing_returns_404(self, client, client_db):
        """GET /clients/{id} для несуществующего клиента возвращает 404."""
        resp = client.get("/clients/ghost", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 404

    def test_update_client(self, client, client_db):
        """PUT /clients/{id} обновляет поля клиента."""
        import storage
        storage.create_client("upd", "Old", project_id=1, sheet_id="old", db_path=client_db)

        resp = client.put(
            "/clients/upd",
            json={"client_name": "New", "project_id": 2, "sheet_id": "new"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "client_id": "upd",
            "client_name": "New",
            "project_id": 2,
            "sheet_id": "new",
        }

    def test_update_client_missing_returns_404(self, client, client_db):
        """PUT /clients/{id} для несуществующего клиента возвращает 404."""
        resp = client.put(
            "/clients/ghost",
            json={"client_name": "Ghost"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 404

    def test_clients_missing_auth_returns_401(self, client, client_db):
        """Все /clients-эндпоинты требуют авторизации."""
        assert client.get("/clients").status_code == 401
        assert client.post("/clients", json={"client_id": "x", "client_name": "X"}).status_code == 401
        assert client.get("/clients/x").status_code == 401
        assert client.put("/clients/x", json={}).status_code == 401


class TestProvidersEndpoint:
    """Тесты read-only эндпоинта /providers."""

    def test_list_providers_returns_open_code_zen(self, client):
        """GET /providers возвращает opencode-zen с корректными полями."""
        resp = client.get("/providers", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
        zen = next((p for p in body if p["id"] == "opencode-zen"), None)
        assert zen is not None
        assert zen["enabled"] is True
        assert zen["priority"] == 1
        assert zen["default_model"] == "deepseek-v4-flash-free"
        assert "deepseek-v4-flash-free" in zen["models"]

    def test_list_providers_missing_auth_returns_401(self, client):
        """GET /providers без Bearer возвращает 401."""
        resp = client.get("/providers")
        assert resp.status_code == 401
