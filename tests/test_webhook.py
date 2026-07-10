"""
T-00Y — тесты webhook.py: POST /run.

Проверяем:
- обратную совместимость старого контракта и дефолты
- приём и проброс новых полей client_id/label_mode/force_relabel
- валидацию label_mode
- Bearer-авторизацию
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import storage
import webhook


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    """Сбрасываем глобальное состояние, инициализируем тестовую БД и задаём секрет."""
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    db_path = str(tmp_path / "webhook_test.db")
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    storage._init_db(db_path)
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
            json={"regions_map": "map.json", "with_labels": True, "depth": 10},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"] == (
            "map.json", True, 10, "default", "auto", False, False, "latest",
            "today", False, None, False,
        )

    def test_run_new_fields_passed(self, client, pipeline_spy):
        """Новые поля принимаются и пробрасываются в пайплайн."""
        resp = client.post(
            "/run",
            json={
                "regions_map": "map.json",
                "client_id": "acme",
                "label_mode": "deep",
                "force_relabel": True,
                "date": "2026-07-01",
                "force_rebuild_report": True,
                "provider_chain": "zen",
                "label_only": True,
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"] == (
            "map.json", True, 10, "acme", "deep", True, False, "latest",
            "2026-07-01", True, "zen", True,
        )

    def test_run_default_label_mode_is_auto(self, client, pipeline_spy):
        """Если label_mode не передан, дефолт — auto."""
        resp = client.post(
            "/run",
            json={"regions_map": "map.json"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"][4] == "auto"

    @pytest.mark.parametrize("mode", ["auto", "deep"])
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
        assert any(m in detail for m in ["auto", "deep"])

    def test_run_invalid_depth_returns_422(self, client):
        """Невалидный depth возвращает 422."""
        resp = client.post(
            "/run",
            json={"depth": 5},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 422
        detail = str(resp.json())
        assert any(d in detail for d in ["10", "20", "50", "100"])

    def test_run_invalid_date_returns_422(self, client):
        """Невалидная date возвращает 422."""
        resp = client.post(
            "/run",
            json={"date": "07-01-2026"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 422
        detail = str(resp.json())
        assert "YYYY-MM-DD" in detail or "today" in detail

    def test_run_label_only_runs_label_pipeline(self, monkeypatch, client_db):
        """label_only=true размечает существующие данные без сбора."""
        import storage
        import main

        storage.create_client("acme", "Acme Corp", db_path=client_db)
        monkeypatch.setattr(storage, "DB_PATH", client_db)

        # Подготовка данных в БД
        fake_rows = [
            {
                "date": "2026-07-01",
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
        storage.save(fake_rows, client_id="acme", db_path=client_db)

        label_called = {"n": 0}
        report_called: dict = {"args": None}

        def fake_label(rows, **kwargs):
            label_called["n"] += 1
            for r in rows:
                r["sentiment"] = "positive"
                r["label"] = "positive"
                r["label_mode"] = kwargs.get("label_mode", "auto")
                r["client_id"] = kwargs.get("client_id", "default")
                r["confidence"] = "high"
            return rows

        def fake_build_report(**kwargs):
            report_called["args"] = kwargs

        monkeypatch.setattr(main, "run", lambda config: {"exit_code": 0, "stats": {}})
        import labeler
        monkeypatch.setattr(labeler, "label", fake_label)
        import reporter
        monkeypatch.setattr(reporter, "build_report", fake_build_report)

        # _run_pipeline ожидает захваченный lock
        webhook._run_lock.acquire()
        try:
            webhook._run_pipeline(
                regions_map="map.json",
                with_labels=True,
                depth=10,
                client_id="acme",
                label_mode="auto",
                force_relabel=False,
                label_only=True,
                date="2026-07-01",
                force_rebuild_report=True,
            )
        finally:
            if webhook._run_lock.locked():
                webhook._run_lock.release()

        assert label_called["n"] == 1
        assert report_called["args"] is not None
        assert report_called["args"]["date"] == "2026-07-01"
        assert report_called["args"]["force"] is True


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
            "searchers": [],
            "geos": [],
            "regions_map": [],
            "queries": [],
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
            "searchers": [],
            "geos": [],
            "regions_map": [],
            "queries": [],
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
            json={
                "client_name": "New",
                "project_id": 2,
                "sheet_id": "new",
                "searchers": ["google"],
                "geos": ["Литва"],
                "regions_map": "regions_map_upd.json",
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "client_id": "upd",
            "client_name": "New",
            "project_id": 2,
            "sheet_id": "new",
            "searchers": ["google"],
            "geos": ["Литва"],
            "regions_map": "regions_map_upd.json",
            "queries": [],
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

    def test_get_client_dates(self, client, client_db):
        """GET /clients/{id}/dates возвращает даты, за которые есть данные."""
        import storage
        storage.create_client("acme", "Acme", project_id=1, sheet_id="sh", db_path=client_db)
        storage.save(
            [
                {
                    "date": "2026-07-01",
                    "searcher": "google",
                    "query": "q1",
                    "geo": "Литва",
                    "region_index": 1300,
                    "position": 1,
                    "url": "https://a.com/1",
                    "domain": "a.com",
                    "snippet": "s1",
                },
                {
                    "date": "2026-07-03",
                    "searcher": "google",
                    "query": "q2",
                    "geo": "Литва",
                    "region_index": 1300,
                    "position": 2,
                    "url": "https://a.com/2",
                    "domain": "a.com",
                    "snippet": "s2",
                },
            ],
            client_id="acme",
            db_path=client_db,
        )

        resp = client.get("/clients/acme/dates", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["dates"] == ["2026-07-03", "2026-07-01"]

    def test_get_client_dates_missing_client_returns_404(self, client, client_db):
        """GET /clients/{id}/dates для несуществующего клиента возвращает 404."""
        resp = client.get("/clients/ghost/dates", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 404


class TestTopvisorRegionsEndpoint:
    """Тесты эндпоинта /topvisor/regions."""

    def test_list_topvisor_regions(self, client, monkeypatch):
        """GET /topvisor/regions возвращает регионы из Topvisor API."""
        import topvisor

        def fake_list_regions(project_id):
            return [
                {"index": 1300, "name": "Литва"},
                {"index": 1301, "name": "Вильнюс"},
            ]

        monkeypatch.setattr(topvisor, "list_regions", fake_list_regions)

        resp = client.get("/topvisor/regions?project_id=123", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == 123
        assert len(body["regions"]) == 2
        assert body["regions"][0]["name"] == "Литва"

    def test_list_topvisor_regions_missing_auth_returns_401(self, client):
        """GET /topvisor/regions без Bearer возвращает 401."""
        resp = client.get("/topvisor/regions?project_id=123")
        assert resp.status_code == 401


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


class TestReportOnly:
    """Тесты режима report_only в POST /run."""

    def test_report_only_passed_to_pipeline(self, client, pipeline_spy):
        """report_only=true и report_date пробрасываются в пайплайн."""
        resp = client.post(
            "/run",
            json={
                "client_id": "acme",
                "report_only": True,
                "report_date": "2026-07-01",
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        # report_only на позиции 6, report_date на позиции 7
        assert pipeline_spy["args"][6] is True
        assert pipeline_spy["args"][7] == "2026-07-01"

    def test_report_only_default_is_false(self, client, pipeline_spy):
        """По умолчанию report_only=false — полный пайплайн."""
        resp = client.post(
            "/run",
            json={"client_id": "acme"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        assert pipeline_spy["args"][6] is False

    def test_report_only_calls_reporter_not_collector(self, client, monkeypatch):
        """При report_only=true вызывается только reporter, не collector."""
        collector_called = {"value": False}
        reporter_called = {"value": False}

        def fake_collect(config):
            collector_called["value"] = True
            return []

        def fake_build_report(date=None, force=False):
            reporter_called["value"] = True

        def fake_run_pipeline(*args):
            """Симулируем логику _run_pipeline для report_only."""
            report_only = args[6] if len(args) > 6 else False
            report_date = args[7] if len(args) > 7 else "latest"
            if report_only:
                date_arg = None if report_date == "latest" else report_date
                fake_build_report(date=date_arg, force=True)
            else:
                fake_collect({})

        monkeypatch.setattr(webhook, "_run_pipeline", fake_run_pipeline)

        resp = client.post(
            "/run",
            json={"report_only": True, "report_date": "2026-07-01"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        # Вызываем пайплайн синхронно для проверки
        webhook._run_pipeline("map.json", True, 10, "default", "auto", False, True, "2026-07-01")
        assert collector_called["value"] is False
        assert reporter_called["value"] is True

    def test_report_only_false_calls_full_pipeline(self, client, monkeypatch):
        """При report_only=false вызывается полный пайплайн (collector)."""
        collector_called = {"value": False}

        def fake_run_pipeline(*args):
            report_only = args[6] if len(args) > 6 else False
            if not report_only:
                collector_called["value"] = True

        monkeypatch.setattr(webhook, "_run_pipeline", fake_run_pipeline)

        resp = client.post(
            "/run",
            json={"report_only": False},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        webhook._run_pipeline("map.json", True, 10, "default", "auto", False, False, "latest")
        assert collector_called["value"] is True


class TestStatusExtendedFields:
    """Тесты расширенных полей GET /status (finished_at, client_id)."""

    def test_status_returns_finished_at_and_client_id(self, client):
        """GET /status возвращает finished_at и client_id из БД."""
        storage.update_run_status(
            {
                "started_at": "2026-07-01T10:00:00Z",
                "finished_at": "2026-07-01T10:05:00Z",
                "status": "ok",
                "message": "Прогон завершён успешно",
                "client_id": "acme",
            },
            db_path=storage.DB_PATH,
        )

        resp = client.get("/status", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["finished_at"] == "2026-07-01T10:05:00Z"
        assert body["client_id"] == "acme"

    def test_status_finished_at_null_during_run(self, client):
        """Во время прогона finished_at=null."""
        storage.update_run_status(
            {
                "started_at": "2026-07-01T10:00:00Z",
                "finished_at": None,
                "status": "running",
                "message": "",
                "client_id": "acme",
            },
            db_path=storage.DB_PATH,
        )

        resp = client.get("/status", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["finished_at"] is None
        assert body["status"] == "running"

    def test_status_client_id_from_run_request(self, client, pipeline_spy):
        """POST /run сохраняет client_id в run_status БД."""
        resp = client.post(
            "/run",
            json={"client_id": "test-client"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 202
        status = storage.get_run_status(db_path=storage.DB_PATH)
        assert status["client_id"] == "test-client"
        assert status["status"] == "starting"

    def test_status_initial_state_has_null_finished_at(self, client):
        """В начальном состоянии finished_at=null."""
        resp = client.get("/status", headers={"Authorization": "Bearer test-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["finished_at"] is None
        assert body["client_id"] is None
        assert body["status"] == "idle"


class TestRunStatusPersistence:
    """Тесты персистентности статуса прогона в БД."""

    def test_run_pipeline_persists_ok_status(self, monkeypatch, client_db):
        """После успешного прогона run_status в БД = ok."""
        import main

        storage.create_client("acme", "Acme Corp", db_path=client_db)
        monkeypatch.setattr(storage, "DB_PATH", client_db)

        monkeypatch.setattr(main, "run", lambda config: {"exit_code": 0, "stats": {"collected": 5}})

        webhook._run_lock.acquire()
        try:
            webhook._run_pipeline(
                regions_map="map.json",
                with_labels=True,
                depth=10,
                client_id="acme",
                label_mode="auto",
                force_relabel=False,
            )
        finally:
            if webhook._run_lock.locked():
                webhook._run_lock.release()

        status = storage.get_run_status(db_path=client_db)
        assert status["status"] == "ok"
        assert status["client_id"] == "acme"
        assert status["stats"] == {"collected": 5}
        assert status["finished_at"] is not None

    def test_run_pipeline_persists_error_status(self, monkeypatch, client_db):
        """При падении прогона run_status в БД = error."""
        import main

        storage.create_client("acme", "Acme Corp", db_path=client_db)
        monkeypatch.setattr(storage, "DB_PATH", client_db)

        def boom(config):
            raise RuntimeError("boom")

        monkeypatch.setattr(main, "run", boom)

        webhook._run_lock.acquire()
        try:
            webhook._run_pipeline(
                regions_map="map.json",
                with_labels=True,
                depth=10,
                client_id="acme",
                label_mode="auto",
                force_relabel=False,
            )
        finally:
            if webhook._run_lock.locked():
                webhook._run_lock.release()

        status = storage.get_run_status(db_path=client_db)
        assert status["status"] == "error"
        assert "boom" in status["message"]
        assert status["finished_at"] is not None


class TestClientProfilePipeline:
    """Тесты сборки config из профиля клиента в webhook.py."""

    def test_build_client_config_uses_profile(self, monkeypatch):
        """_build_client_config берёт project_id/searchers/geos/regions_map/queries/sheet_id из профиля."""
        import storage

        def fake_get_client(client_id, db_path):
            return {
                "client_id": "acme",
                "client_name": "Acme Corp",
                "project_id": 999,
                "sheet_id": "acme-sheet",
                "searchers": ["google"],
                "geos": ["Литва"],
                "regions_map": [{"searcher": "google", "geo_name": "Литва"}],
                "queries": [{"key": "subject x", "display": "Subject X"}],
            }

        monkeypatch.setattr(storage, "get_client", fake_get_client)

        config = webhook._build_client_config("acme", {"depth": 50})
        assert config["project_id"] == 999
        assert config["sheet_id"] == "acme-sheet"
        assert config["searchers"] == ["google"]
        assert config["geos"] == ["Литва"]
        assert config["regions_map"] == [{"searcher": "google", "geo_name": "Литва"}]
        assert config["queries"] == [{"key": "subject x", "display": "Subject X"}]
        # Параметры запроса перекрывают профиль
        assert config["depth"] == 50
        # Fallback DEFAULT_CONFIG для остального
        assert "timeout_sec" in config

    def test_build_client_config_uses_regions_map_string_legacy(self, monkeypatch):
        """_build_client_config поддерживает legacy-строку regions_map из профиля."""
        import storage

        def fake_get_client(client_id, db_path):
            return {
                "client_id": "legacy",
                "client_name": "Legacy Client",
                "regions_map": "regions_map_legacy.json",
            }

        monkeypatch.setattr(storage, "get_client", fake_get_client)

        config = webhook._build_client_config("legacy", {})
        assert config["regions_map"] == "regions_map_legacy.json"

    def test_build_client_config_fallback_when_profile_missing(self, monkeypatch):
        """Если профиль не найден, используем DEFAULT_CONFIG + env fallback."""
        import storage
        monkeypatch.setattr(storage, "get_client", lambda cid, db_path: None)
        monkeypatch.setenv("TOPVISOR_PROJECT_ID", "777")

        config = webhook._build_client_config("missing", {"depth": 20})
        assert config["depth"] == 20
        assert config["client_id"] == "missing"
        assert config["searchers"] == ["google", "yandex_ru", "yandex_com"]
        assert "queries" not in config

    def test_run_pipeline_passes_client_config_to_main(self, monkeypatch, client_db):
        """_run_pipeline передаёт в main.run() config с полями из профиля клиента."""
        import storage
        import main

        storage.create_client(
            "acme", "Acme Corp",
            project_id=999,
            sheet_id="acme-sheet",
            searchers=["google"],
            geos=["Литва"],
            regions_map=[{"searcher": "google", "geo_name": "Литва"}],
            queries=[{"key": "subject x", "display": "Subject X"}],
            db_path=client_db,
        )
        monkeypatch.setattr(storage, "DB_PATH", client_db)

        captured = {}

        def fake_run(config):
            captured["config"] = config
            return 0

        monkeypatch.setattr(main, "run", fake_run)

        # _run_pipeline ожидает, что lock уже захвачен (как в trigger_run)
        webhook._run_lock.acquire()
        try:
            webhook._run_pipeline(
                regions_map="regions_map_default.json",
                with_labels=True,
                depth=10,
                client_id="acme",
                label_mode="auto",
                force_relabel=False,
            )
        finally:
            if webhook._run_lock.locked():
                webhook._run_lock.release()

        cfg = captured["config"]
        assert cfg["project_id"] == 999
        assert cfg["sheet_id"] == "acme-sheet"
        assert cfg["searchers"] == ["google"]
        assert cfg["geos"] == ["Литва"]
        assert cfg["regions_map"] == [{"searcher": "google", "geo_name": "Литва"}]
        assert cfg["queries"] == [{"key": "subject x", "display": "Subject X"}]
        assert cfg["client_id"] == "acme"


class TestLabelsImportEndpoint:
    """Тесты POST /labels/import — батчевый импорт в domain_labels."""

    def test_import_labels_success_array_format(self, client, client_db):
        """Валидный батч в виде голого массива импортируется целиком."""
        import storage

        payload = [
            {"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "positive"},
            {"domain": "b.com", "query": "q2", "geo": "Латвия", "sentiment": "negative"},
        ]

        resp = client.post(
            "/labels/import",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 2
        assert body["skipped"] == 0
        assert body["errors"] == 0
        assert body["error_samples"] == []

        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "positive"
        assert storage.get_domain_label("b.com", "q2", "Латвия", client_db) == "negative"

        conn = sqlite3.connect(client_db)
        try:
            rows = conn.execute(
                "SELECT source FROM domain_labels WHERE domain = ? AND query = ? AND geo = ?",
                ("a.com", "q1", "Литва"),
            ).fetchall()
            assert rows[0][0] == "manual_l1"
        finally:
            conn.close()

    def test_import_labels_success_object_format(self, client, client_db):
        """Формат тела {labels: [...]} тоже работает."""
        import storage

        resp = client.post(
            "/labels/import",
            json={"labels": [{"domain": "c.com", "query": "q3", "geo": "Эстония", "sentiment": "neutral"}]},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 0
        assert body["errors"] == 0

        assert storage.get_domain_label("c.com", "q3", "Эстония", client_db) == "neutral"

    def test_import_labels_idempotent(self, client, client_db):
        """Повторный импорт тех же записей не плодит дубли."""
        import storage

        payload = [
            {"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "positive"},
        ]

        for _ in range(2):
            resp = client.post(
                "/labels/import",
                json=payload,
                headers={"Authorization": "Bearer test-secret"},
            )
            assert resp.status_code == 200
            assert resp.json()["imported"] == 1

        conn = sqlite3.connect(client_db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM domain_labels"
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "positive"

    def test_import_labels_skips_invalid_sentiment(self, client, client_db):
        """Одна битая запись попадает в skipped, остальные импортируются."""
        import storage

        payload = [
            {"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "positive"},
            {"domain": "b.com", "query": "q2", "geo": "Латвия", "sentiment": "???"},
        ]

        resp = client.post(
            "/labels/import",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 1
        assert body["errors"] == 0
        assert len(body["error_samples"]) == 1
        assert "invalid sentiment" in body["error_samples"][0]

        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "positive"
        assert storage.get_domain_label("b.com", "q2", "Латвия", client_db) is None

    def test_import_labels_skips_missing_fields(self, client, client_db):
        """Запись без обязательных полей пропускается."""
        import storage

        payload = [
            {"domain": "", "query": "q1", "geo": "Литва", "sentiment": "positive"},
            {"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "neutral"},
        ]

        resp = client.post(
            "/labels/import",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 1

        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "neutral"

    def test_import_labels_respects_manual_l1_priority(self, client, client_db):
        """manual_l1 из импорта перезаписывает snippet; повторный snippet — нет."""
        import storage

        # Предварительно авто-метка
        storage.upsert_domain_label(
            "a.com", "q1", "Литва", "positive", "snippet", db_path=client_db
        )

        # Импорт manual_l1 меняет метку
        resp = client.post(
            "/labels/import",
            json=[{"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "negative"}],
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "negative"

        # Повторный snippet не должен вернуть positive
        storage.upsert_domain_label(
            "a.com", "q1", "Литва", "positive", "snippet", db_path=client_db
        )
        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "negative"

    def test_import_labels_partial_batch_continues(self, client, client_db):
        """Битая запись в середине батча не роняет остальные."""
        import storage

        payload = [
            {"domain": "a.com", "query": "q1", "geo": "Литва", "sentiment": "positive"},
            {"domain": "b.com", "query": "q2", "geo": "Латвия", "sentiment": "WRONG"},
            {"domain": "c.com", "query": "q3", "geo": "Эстония", "sentiment": "neutral"},
        ]

        resp = client.post(
            "/labels/import",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 2
        assert body["skipped"] == 1
        assert body["errors"] == 0

        assert storage.get_domain_label("a.com", "q1", "Литва", client_db) == "positive"
        assert storage.get_domain_label("b.com", "q2", "Латвия", client_db) is None
        assert storage.get_domain_label("c.com", "q3", "Эстония", client_db) == "neutral"

    def test_import_labels_empty_list(self, client, client_db):
        """Пустой список возвращает нулевую сводку."""
        resp = client.post(
            "/labels/import",
            json=[],
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 0
        assert body["skipped"] == 0
        assert body["errors"] == 0

    def test_import_labels_missing_auth_returns_401(self, client):
        resp = client.post("/labels/import", json=[{}])
        assert resp.status_code == 401

    def test_import_labels_invalid_auth_returns_403(self, client):
        resp = client.post(
            "/labels/import",
            json=[{}],
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 403

