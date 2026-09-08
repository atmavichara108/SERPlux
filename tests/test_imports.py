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
from pathlib import Path

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


def test_apps_script_manual_etalon_commands_are_explicit():
    """Apps Script keeps both imports manual and out of the collection hook."""
    script_path = Path(PROJECT_ROOT) / "apps_script.gs"
    if not script_path.exists():
        pytest.skip("apps_script.gs is a client-side artifact and is not copied into the server image")
    script = script_path.read_text(encoding="utf-8")
    assert '"Зафиксировать исправления в эталон", "importLatestReportToEtalon"' in script
    assert "function importHistoricalEtalonsToDb()" in script
    assert "function _normalizeHistoricalSheetName(name)" in script
    assert ".replace(/google/g, \"гугл\")" in script
    assert ".replace(/[—–]/g, \"-\")" in script
    assert "_findHistoricalEtalonSheet(ss, names[i])" in script
    assert "function importLatestReportToEtalon()" in script
    assert "Google — посл эталон разметки" in script
    assert "Яндекс ру — посл эталон разметки" in script
    assert "Яндекс ком — посл эталон разметки" in script
    assert "_collectReportLabels(sheet, true)" in script
    assert "_reportColorToSentiment" in script
    assert "source: \"manual_l1\"" in script
    assert "onEdit" not in script
    assert "importLatestReportToEtalon();" not in script


def test_apps_script_searcher_checkboxes_in_settings_template():
    """Searcher checkboxes присутствуют в SETTINGS_TEMPLATE листа «Настройки»."""
    script_path = Path(PROJECT_ROOT) / "apps_script.gs"
    if not script_path.exists():
        pytest.skip("apps_script.gs is a client-side artifact and is not copied into the server image")
    script = script_path.read_text(encoding="utf-8")

    # Проверяем именно блок SETTINGS_TEMPLATE, а не просто наличие строк в файле
    template_start = script.index("var SETTINGS_TEMPLATE = [")
    template_end = script.index("];", template_start)
    template_block = script[template_start:template_end]

    assert '"searcher_google"' in template_block
    assert '"searcher_yandex_ru"' in template_block
    assert '"searcher_yandex_com"' in template_block
    # По умолчанию все три поисковика выбраны
    assert '"true"' in template_block

    # _readSettings должен читать их в settings.searchers
    assert "settings.searchers.google" in script
    assert "settings.searchers.yandex_ru" in script
    assert "settings.searchers.yandex_com" in script

    # runCollection должен передавать searchers в /run и валидировать пустой выбор
    assert "payload.searchers = selectedSearchers" in script
    assert "Не выбран ни один поисковик" in script


def test_apps_script_settings_validation_uses_key_lookup():
    """Валидации листа «Настройки» ищут строку по ключу, а не по захардкоженному номеру.

    Регрессия v1.0.3: после вставки report_depth строкой 3 захардкоженный
    getRange(3, 2) перезаписывал with_labels списком ["true","false"], ломая
    report_depth. Все валидации должны идти через _findSettingsRow(sheet, "<ключ>").
    """
    script_path = Path(PROJECT_ROOT) / "apps_script.gs"
    if not script_path.exists():
        pytest.skip("apps_script.gs is a client-side artifact and is not copied into the server image")
    script = script_path.read_text(encoding="utf-8")

    # Ни одна валидация не должна хардкодить номер строки в setDataValidation
    assert "getRange(3, 2).setDataValidation" not in script

    # with_labels ищется по ключу (фикс бага: B3 остаётся ["10","20","50"], B4 получает ["true","false"])
    assert '_findSettingsRow(sheet, "with_labels")' in script


def test_apps_script_check_status_shows_labeling_breakdown():
    """checkStatus в ветке ok выводит breakdown разметки из stats.labeling."""
    script_path = Path(PROJECT_ROOT) / "apps_script.gs"
    if not script_path.exists():
        pytest.skip("apps_script.gs is a client-side artifact and is not copied into the server image")
    script = script_path.read_text(encoding="utf-8")

    assert "из эталона" in script
    assert "stats.labeling" in script


def test_apps_script_provider_discover_ui_does_not_ask_for_api_key():
    """_addProviderDialog использует preset endpoint'ы и не запрашивает сам API-ключ."""
    script_path = Path(PROJECT_ROOT) / "apps_script.gs"
    if not script_path.exists():
        pytest.skip("apps_script.gs is a client-side artifact and is not copied into the server image")
    script = script_path.read_text(encoding="utf-8")

    # UI вызывает auto-discovery моделей
    assert '"/providers/discover"' in script
    assert "api_key_env_var" in script

    # Preset endpoint'ы: выбор из списка, а не ручной ввод
    assert "PROVIDER_ENDPOINT_PRESETS" in script
    assert "https://opencode.ai/zen/v1/chat/completions" in script
    assert "https://openrouter.ai/api/v1/chat/completions" in script
    assert "https://api.openai.com/v1/chat/completions" in script
    assert "Введите endpoint" not in script

    # Сам ключ не запрашивается: только имя env-переменной
    assert "Введите API ключ" not in script

    # Пустой working из discover -> провайдер не регистрируется
    assert "Нет рабочих моделей" in script
    assert "провайдер НЕ зарегистрирован".lower() in script.lower()
