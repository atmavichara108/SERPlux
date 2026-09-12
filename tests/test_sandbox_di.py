"""
Тесты DI-инъекции песочницы (Фаза 1.2d):
  - topvisor.BASE_URL подменяется через TOPVISOR_API_BASE
  - провайдер LLM endpoint подменяется через LLM_API_BASE
  - дефолты (без env) остаются каноническими URL
"""

import importlib


class TestTopvisorBaseUrl:
    def test_default_is_canonical(self, monkeypatch):
        """Без TOPVISOR_API_BASE — канонический URL Topvisor."""
        monkeypatch.delenv("TOPVISOR_API_BASE", raising=False)
        import topvisor
        importlib.reload(topvisor)
        assert topvisor.BASE_URL == "https://api.topvisor.com/v2/json"

    def test_sandbox_override(self, monkeypatch):
        """TOPVISOR_API_BASE подменяет базу (rstrip('/') — без двойных слэшей)."""
        monkeypatch.setenv("TOPVISOR_API_BASE", "http://127.0.0.1:8011/v2/json/")
        import topvisor
        importlib.reload(topvisor)
        assert topvisor.BASE_URL == "http://127.0.0.1:8011/v2/json"

    def test_post_uses_base_url(self, monkeypatch):
        """_post строит URL от BASE_URL (подменяемый), а не от захардкоженного."""
        monkeypatch.setenv("TOPVISOR_API_BASE", "http://127.0.0.1:8011/v2/json")
        import topvisor
        importlib.reload(topvisor)
        assert topvisor.BASE_URL in "http://127.0.0.1:8011/v2/json/get/x"


class TestLLMEndpoint:
    def test_default_zen_endpoint(self, monkeypatch):
        """Без LLM_API_BASE — канонический OpenCode Zen endpoint."""
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        import config
        importlib.reload(config)
        assert config.PROVIDERS["opencode-zen"]["endpoint"] == \
            "https://opencode.ai/zen/v1/chat/completions"

    def test_sandbox_llm_endpoint(self, monkeypatch):
        """LLM_API_BASE подменяет endpoint провайдера (для mock_llm)."""
        monkeypatch.setenv("LLM_API_BASE", "http://127.0.0.1:8012/v1/chat/completions")
        import config
        importlib.reload(config)
        assert config.PROVIDERS["opencode-zen"]["endpoint"] == \
            "http://127.0.0.1:8012/v1/chat/completions"

    def teardown_method(self):
        """Гигиена: reload-тесты мутируют модуль config глобально.
        Возвращаем каноническое состояние после каждого теста, иначе
        последующие тесты (webhook /providers) видят sandbox-endpoint."""
        import config
        importlib.reload(config)
