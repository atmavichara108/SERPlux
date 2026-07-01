/**
 * apps_script.gs — Google Apps Script для запуска SERPlux из Google Sheets.
 *
 * Установка:
 *   1. Откройте таблицу → Расширения → Apps Script
 *   2. Вставьте этот код, сохраните
 *   3. Запустите onOpen() вручную один раз — появится меню "SERPlux"
 *   4. Заполните константы WEBHOOK_URL и WEBHOOK_SECRET ниже
 *      (или храните их в PropertiesService — см. setSecrets())
 *
 * Безопасность:
 *   - WEBHOOK_SECRET НЕ хранится в коде — только в Script Properties
 *   - Для установки секрета запустите setSecrets() один раз вручную
 */

// ─── Настройки ────────────────────────────────────────────────────────────────

/** URL вашего сервера, например: https://serplux.example.com */
var WEBHOOK_URL = "https://YOUR_SERVER_URL";

/** Имя файла карты регионов (лежит рядом с main.py на сервере) */
var DEFAULT_REGIONS_MAP = "regions_map.json";

// ─── Меню ─────────────────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("SERPlux")
    .addItem("▶ Запустить сбор", "runPipeline")
    .addItem("⟳ Проверить статус", "checkStatus")
    .addSeparator()
    .addItem("⚙ Установить секрет", "setSecrets")
    .addToUi();
}

// ─── Основные функции ─────────────────────────────────────────────────────────

/**
 * Запускает полный пайплайн: сбор → разметка → выгрузка → отчёт.
 * Читает параметры из листа "Настройки" если он есть.
 */
function runPipeline() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var params = _readSettings();
  var payload = {
    regions_map: params.regionsMap || DEFAULT_REGIONS_MAP,
    with_labels: params.withLabels !== false,
    depth: params.depth || 10
  };

  var result = _post("/run", payload, secret);
  if (result.ok) {
    ui.alert(
      "SERPlux запущен",
      "Прогон принят в очередь.\nНачало: " + result.data.started_at +
      "\n\nПроверьте статус через меню SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
  } else {
    ui.alert("Ошибка запуска", "HTTP " + result.code + "\n" + result.body, ui.ButtonSet.OK);
  }
}

/**
 * Проверяет статус последнего прогона.
 */
function checkStatus() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var result = _get("/status", secret);
  if (result.ok) {
    var d = result.data;
    var msg = "Статус: " + d.status;
    if (d.started_at) msg += "\nЗапущен: " + d.started_at;
    if (d.message)    msg += "\nСообщение: " + d.message;
    ui.alert("Статус SERPlux", msg, ui.ButtonSet.OK);
  } else {
    ui.alert("Ошибка", "HTTP " + result.code + "\n" + result.body, ui.ButtonSet.OK);
  }
}

/**
 * Устанавливает WEBHOOK_SECRET в Script Properties (безопасное хранилище).
 * Запускать вручную один раз.
 */
function setSecrets() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.prompt(
    "Установить секрет",
    "Введите WEBHOOK_SECRET (токен из .env на сервере):",
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() !== ui.Button.OK) return;
  var secret = response.getResponseText().trim();
  if (!secret) {
    ui.alert("Секрет не может быть пустым");
    return;
  }
  PropertiesService.getScriptProperties().setProperty("WEBHOOK_SECRET", secret);
  ui.alert("Готово", "Секрет сохранён в Script Properties.", ui.ButtonSet.OK);
}

// ─── Вспомогательные функции ──────────────────────────────────────────────────

/**
 * Читает параметры из листа "Настройки" (если существует).
 * Ожидаемый формат: A1=ключ, B1=значение (по одному на строку).
 *
 * Поддерживаемые ключи:
 *   regions_map  — имя файла карты регионов
 *   with_labels  — true/false
 *   depth        — число
 */
function _readSettings() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Настройки");
  if (!sheet) return {};

  var data = sheet.getDataRange().getValues();
  var settings = {};
  for (var i = 0; i < data.length; i++) {
    var key = String(data[i][0]).trim().toLowerCase();
    var val = data[i][1];
    if (key === "regions_map")  settings.regionsMap  = String(val).trim();
    if (key === "with_labels")  settings.withLabels  = String(val).trim().toLowerCase() !== "false";
    if (key === "depth")        settings.depth       = parseInt(val, 10) || 10;
  }
  return settings;
}

/** Читает секрет из Script Properties. */
function _getSecret() {
  return PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET") || "";
}

/** POST-запрос к webhook. */
function _post(path, payload, secret) {
  return _request("post", path, payload, secret);
}

/** GET-запрос к webhook. */
function _get(path, secret) {
  return _request("get", path, null, secret);
}

function _request(method, path, payload, secret) {
  var url = WEBHOOK_URL.replace(/\/$/, "") + path;
  var options = {
    method: method,
    headers: {
      "Authorization": "Bearer " + secret,
      "Content-Type": "application/json"
    },
    muteHttpExceptions: true
  };
  if (payload) {
    options.payload = JSON.stringify(payload);
  }

  try {
    var response = UrlFetchApp.fetch(url, options);
    var code = response.getResponseCode();
    var body = response.getContentText();
    var data = {};
    try { data = JSON.parse(body); } catch (e) {}
    return { ok: code >= 200 && code < 300, code: code, body: body, data: data };
  } catch (e) {
    return { ok: false, code: 0, body: String(e), data: {} };
  }
}
