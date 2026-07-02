/**
 * apps_script.gs — Google Apps Script для запуска SERPlux из Google Sheets.
 *
 * Установка:
 *   1. Откройте таблицу → Расширения → Apps Script
 *   2. Вставьте этот код, сохраните
 *   3. Запустите onOpen() вручную один раз — появится меню "SERPlux"
 *   4. Установите URL сервера: SERPlux → Настройки → Установить URL сервера
 *   5. Установите секрет: SERPlux → Настройки → Установить секрет
 *
 * Безопасность:
 *   - WEBHOOK_URL и WEBHOOK_SECRET НЕ хранятся в коде — только в Script Properties
 *   - Для установки запустите соответствующий пункт меню один раз вручную
 *
 * Версия: 0.2
 */

// ─── Значения по умолчанию ────────────────────────────────────────────────────

var DEFAULT_REGIONS_MAP = "regions_map.json";
var DEFAULT_DEPTH = 10;

// ─── Меню ─────────────────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("SERPlux")
    .addItem("▶ Запустить сбор (без разметки)", "runPipelineNoLabels")
    .addItem("▶ Запустить сбор + разметка", "runPipelineWithLabels")
    .addSeparator()
    .addItem("⟳ Проверить статус", "checkStatus")
    .addItem("📋 Открыть настройки", "openSettings")
    .addSeparator()
    .addItem("⚙ Установить секрет", "setSecret")
    .addItem("⚙ Установить URL сервера", "setWebhookUrl")
    .addToUi();
}

// ─── Основные функции ─────────────────────────────────────────────────────────

/** Запуск без разметки (with_labels=false). */
function runPipelineNoLabels() {
  _runPipeline(false);
}

/** Запуск с разметкой (with_labels=true). */
function runPipelineWithLabels() {
  _runPipeline(true);
}

/**
 * Запускает пайплайн: сбор → (опционально разметка) → выгрузка → отчёт.
 * Читает параметры из листа "Настройки" если он есть.
 *
 * @param {boolean} withLabels — включать ли разметку тональности
 */
function _runPipeline(withLabels) {
  var ui = SpreadsheetApp.getUi();

  // Проверка URL сервера
  var webhookUrl = _getWebhookUrl();
  if (!webhookUrl) {
    ui.alert(
      "Ошибка",
      "URL сервера не задан.\nЗапустите SERPlux → Установить URL сервера.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Проверка секрета
  var secret = _getSecret();
  if (!secret) {
    ui.alert(
      "Ошибка",
      "Секрет не задан.\nЗапустите SERPlux → Установить секрет.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Чтение параметров из листа "Настройки"
  var params = _readSettings();
  var labelMode = params.labelMode || "snippets";
  var depth = params.depth || DEFAULT_DEPTH;
  var regionsMap = params.regionsMap || DEFAULT_REGIONS_MAP;

  // Формируем тело запроса по ФАКТИЧЕСКОМУ контракту webhook.py
  // (только regions_map, with_labels, depth — остальное webhook пока не принимает)
  var payload = {
    regions_map: regionsMap,
    with_labels: withLabels,
    depth: depth
  };

  // Отправляем запрос
  var result = _post("/run", payload, secret);

  if (result.ok) {
    var startedAt = result.data.started_at || "—";
    // Toast — быстрое уведомление внизу экрана
    SpreadsheetApp.getActiveSpreadsheet().toast(
      "Прогон запущен. Начало: " + startedAt,
      "SERPlux",
      10
    );
    // Диалог с деталями
    ui.alert(
      "SERPlux запущен",
      "Прогон принят в очередь.\n" +
      "Начало: " + startedAt + "\n" +
      "Глубина: " + depth + "\n" +
      "Разметка: " + (withLabels ? "вкл (" + labelMode + ")" : "выкл") + "\n\n" +
      "Проверьте статус через SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
  } else {
    var errorMsg = "HTTP " + result.code + "\n" + result.body;
    SpreadsheetApp.getActiveSpreadsheet().toast(
      "Ошибка запуска: HTTP " + result.code,
      "SERPlux",
      10
    );
    ui.alert("Ошибка запуска", errorMsg, ui.ButtonSet.OK);
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
    var statusText = d.status || "unknown";
    var msg = "Статус: " + statusText;

    // Цветовая индикация в зависимости от статуса
    var statusIcon = "";
    if (statusText === "ok") {
      statusIcon = "✅ ";
    } else if (statusText === "error") {
      statusIcon = "❌ ";
    } else if (statusText === "running" || statusText === "starting") {
      statusIcon = "⏳ ";
    }

    if (d.started_at) msg += "\nЗапущен: " + d.started_at;
    if (d.message)    msg += "\nСообщение: " + d.message;

    ui.alert(statusIcon + "Статус SERPlux", msg, ui.ButtonSet.OK);

    // Обновляем ячейку статуса на листе "Настройки" (если есть)
    _updateStatusCell(statusText);
  } else {
    ui.alert("Ошибка", "HTTP " + result.code + "\n" + result.body, ui.ButtonSet.OK);
  }
}

/**
 * Переключает на лист "Настройки".
 */
function openSettings() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Настройки");
  if (sheet) {
    ss.setActiveSheet(sheet);
    SpreadsheetApp.getActiveSpreadsheet().toast(
      "Открыт лист «Настройки»",
      "SERPlux",
      3
    );
  } else {
    SpreadsheetApp.getUi().alert(
      "Лист «Настройки» не найден.\nСоздайте лист с именем «Настройки» для управления параметрами прогона."
    );
  }
}

// ─── Настройка секретов и URL ─────────────────────────────────────────────────

/**
 * Устанавливает WEBHOOK_SECRET в Script Properties.
 */
function setSecret() {
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

/**
 * Устанавливает WEBHOOK_URL в Script Properties.
 */
function setWebhookUrl() {
  var ui = SpreadsheetApp.getUi();
  var currentUrl = _getWebhookUrl();
  var response = ui.prompt(
    "Установить URL сервера",
    "Введите URL webhook-сервера:\n(например: https://serp.example.com)\n\nТекущий: " + (currentUrl || "не задан"),
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() !== ui.Button.OK) return;
  var url = response.getResponseText().trim();
  if (!url) {
    ui.alert("URL не может быть пустым");
    return;
  }
  // Убираем trailing slash для единообразия
  url = url.replace(/\/$/, "");
  PropertiesService.getScriptProperties().setProperty("WEBHOOK_URL", url);
  ui.alert("Готово", "URL сервера сохранён: " + url, ui.ButtonSet.OK);
}

// ─── Вспомогательные функции ──────────────────────────────────────────────────

/**
 * Читает параметры из листа "Настройки" (если существует).
 * Ожидаемый формат: колонка A = ключ, колонка B = значение.
 *
 * Поддерживаемые ключи:
 *   regions_map   — имя файла карты регионов
 *   with_labels   — true/false (чтение, но в /run передаётся из кнопки)
 *   depth         — число (10/20/50/100)
 *   client_id     — ID клиента (чтение для будущего, в /run НЕ отправляется)
 *   label_mode    — режим разметки (чтение для будущего, в /run НЕ отправляется)
 *   date          — дата сбора (чтение для будущего, в /run НЕ отправляется)
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
    if (key === "regions_map")   settings.regionsMap  = String(val).trim();
    if (key === "with_labels")   settings.withLabels  = String(val).trim().toLowerCase() !== "false";
    if (key === "depth")         settings.depth       = parseInt(val, 10) || DEFAULT_DEPTH;
    // Читаем для будущего (когда webhook примет эти поля)
    if (key === "client_id")     settings.clientId    = String(val).trim();
    if (key === "label_mode")    settings.labelMode   = String(val).trim();
    if (key === "date")          settings.date        = String(val).trim();
  }
  return settings;
}

/** Читает секрет из Script Properties. */
function _getSecret() {
  return PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET") || "";
}

/** Читает URL сервера из Script Properties. */
function _getWebhookUrl() {
  return PropertiesService.getScriptProperties().getProperty("WEBHOOK_URL") || "";
}

/**
 * Обновляет ячейку статуса на листе "Настройки" (если есть строка "status").
 * Красит ячейку в цвет по статусу.
 */
function _updateStatusCell(status) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Настройки");
  if (!sheet) return;

  var data = sheet.getDataRange().getValues();
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim().toLowerCase() === "status") {
      var cell = sheet.getRange(i + 1, 2); // колонка B
      cell.setValue(status);

      // Цветовая заливка по статусу
      if (status === "ok") {
        cell.setBackground("#d4edda"); // зелёный
      } else if (status === "error") {
        cell.setBackground("#f8d7da"); // красный
      } else if (status === "running" || status === "starting") {
        cell.setBackground("#fff3cd"); // жёлтый
      } else {
        cell.setBackground("#e2e3e5"); // серый (idle)
      }
      return;
    }
  }
}

/** POST-запрос к webhook. */
function _post(path, payload, secret) {
  return _request("post", path, payload, secret);
}

/** GET-запрос к webhook. */
function _get(path, secret) {
  return _request("get", path, null, secret);
}

/**
 * Универсальный HTTP-запрос к webhook-серверу.
 *
 * @param {string} method — "get" или "post"
 * @param {string} path — путь эндпоинта (/run, /status, /health)
 * @param {object|null} payload — тело запроса (для POST)
 * @param {string} secret — Bearer-токен
 * @return {object} {ok: bool, code: int, body: string, data: object}
 */
function _request(method, path, payload, secret) {
  var baseUrl = _getWebhookUrl();
  if (!baseUrl) {
    return { ok: false, code: 0, body: "WEBHOOK_URL не задан в Script Properties", data: {} };
  }

  var url = baseUrl.replace(/\/$/, "") + path;
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
