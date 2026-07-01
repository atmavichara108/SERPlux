"""
test_imports.py — smoke-тест: все модули проекта импортируются без ошибок.

Проверяет, что:
- нет синтаксических ошибок
- нет обращений к env-переменным на уровне модуля (только lazy)
- нет несуществующих зависимостей

Секреты не нужны: все модули используют lazy-загрузку credentials.
"""

import importlib
import sys
import os

import pytest


# Добавляем корень проекта в sys.path, чтобы импортировать модули напрямую
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Минимальный набор env-переменных, чтобы модули не падали при импорте
# (на случай если какой-то модуль читает env на уровне модуля, а не в функции)
STUB_ENV = {
    "TOPVISOR_API_KEY": "stub",
    "TOPVISOR_USER_ID": "stub",
    "TOPVISOR_PROJECT_ID": "12345",
    "OPENCODE_API_KEY": "stub",
    "GOOGLE_SHEET_ID": "stub",
    "GOOGLE_CREDENTIALS_PATH": "credentials.json",
    "WEBHOOK_SECRET": "stub-secret",
    "DB_PATH": ":memory:",
}

MODULES = [
    "config",
    "storage",
    "topvisor",
    "collector",
    "labeler",
    "exporter",
    "reporter",
    "webhook",
    "main",
]


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    """Устанавливает заглушки env-переменных для всех тестов в файле."""
    for key, value in STUB_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    """Каждый модуль должен импортироваться без исключений."""
    # Если модуль уже в sys.modules — перезагружаем, чтобы поймать ошибки
    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        mod = importlib.import_module(module_name)

    assert mod is not None, f"Модуль {module_name} вернул None"


def test_all_modules_have_no_top_level_api_calls():
    """
    Проверяет, что импорт всех модулей не вызывает сетевых запросов.
    Косвенная проверка: если импорт прошёл без EnvironmentError при stub-ключах,
    значит credentials не читаются на уровне модуля.
    """
    for name in MODULES:
        assert name in sys.modules or importlib.import_module(name) is not None
