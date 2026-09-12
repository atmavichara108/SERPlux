"""
Тесты /version эндпоинта (Фаза 3a, Workstream D).

Эндпоинт отдаёт build-информацию (GIT_SHA/RELEASE_TAG/IMAGE_DIGEST/BUILT_AT) —
release.sh сверяет её с ожидаемым тегом перед завершением деплоя.
"""

import importlib

from fastapi.testclient import TestClient

import webhook


class TestVersionEndpoint:
    def _client(self) -> TestClient:
        importlib.reload(webhook)
        return TestClient(webhook.app)

    def test_version_empty_by_default(self, monkeypatch):
        """Без build-args (локальный запуск) — пустые строки, 200."""
        for var in ("GIT_SHA", "RELEASE_TAG", "IMAGE_DIGEST", "BUILT_AT"):
            monkeypatch.delenv(var, raising=False)
        client = self._client()
        resp = client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["git_sha"] == ""
        assert data["tag"] == ""

    def test_version_returns_build_args(self, monkeypatch):
        """Env-значения возвращаются как есть (release.sh сверяет tag)."""
        monkeypatch.setenv("GIT_SHA", "abc1234567890")
        monkeypatch.setenv("RELEASE_TAG", "v1.0.3")
        monkeypatch.setenv("IMAGE_DIGEST", "sha256:deadbeef")
        monkeypatch.setenv("BUILT_AT", "2026-09-12T00:00:00Z")
        client = self._client()
        resp = client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["git_sha"] == "abc1234567890"
        assert data["tag"] == "v1.0.3"
        assert data["image_digest"] == "sha256:deadbeef"
        assert data["built_at"] == "2026-09-12T00:00:00Z"

    def test_version_no_auth_required(self, monkeypatch):
        """Эндпоинт открыт (без Authorization) — значения не секретные."""
        client = self._client()
        resp = client.get("/version")
        assert resp.status_code == 200
