/**
 * apps_script.gs — Google Apps Script UI для SERPlux.
 *
 * Спецификация: docs/ui-spec.md §4 (вариант «одна таблица на клиента»)
 * Версия: 1.0
 *
 * Установка (ВЛАДЕЛЕЦ таблицы):
 *   1. Откройте таблицу → Расширения → Apps Script
 *   2. Вставьте этот код, сохраните (Ctrl+S)
 *   3. Закройте и заново откройте таблицу — появится меню "SERPlux"
 *   4. SERPlux → Настройки → [+] Инициализировать настройки
 *   5. SERPlux → Настройки → Установить URL сервера
 *   6. SERPlux → Настройки → Установить секрет
 *
 * Установка (РАЗРАБОТЧИК — таблица расшарена с правами редактора):
 *   1. Откройте таблицу → Расширения → Apps Script
 *   2. Вставьте этот код, сохраните (Ctrl+S)
 *   3. Запустите функцию setupTriggers() через Run (ОДИН РАЗ)
 *      — Google попросит разрешения — дайте их под СВОИМ аккаунтом
 *   4. Закройте и заново откройте таблицу — появится меню "SERPlux"
 *
 *   НЕ запускайте onOpen() вручную через кнопку Run — это не работает.
 *
 * Безопасность:
 *   - WEBHOOK_URL и WEBHOOK_SECRET хранятся ТОЛЬКО в Script Properties
 *   - Script Properties ОБЩИЕ для всех пользователей таблицы (установи один раз)
 *   - API-ключи провайдеров НЕ передаются через UI — они задаются в .env на сервере
 */

// ─── Константы ────────────────────────────────────────────────────────────────

var SETTINGS_SHEET_NAME = "Настройки";
var LOG_SHEET_NAME = "Лог";
var DEFAULT_DEPTH = 10;
var DEFAULT_LABEL_MODE = "domains";
var DEFAULT_DATE = "today";
var DEFAULT_REPORT_DATE = "latest";

/**
 * Шаблон листа «Настройки»: [ключ, значение по умолчанию, подсказка].
 * Порядок определяет номер строки (1-indexed).
 */
var SETTINGS_TEMPLATE = [
  ["client_id",            "",         "ID клиента (например: client01). Выбрать из dropdown или обновить SERPlux → Настройки → [>] Обновить список клиентов"],
  ["depth",                "10",       "Глубина сбора: 10, 20, 50 или 100"],
  ["with_labels",          "true",     "Включить разметку: true или false"],
  ["label_mode",           "auto",     "Режим разметки: auto, deep или domains/snippets/full"],
  ["date",                 "today",    "Дата сбора: today или YYYY-MM-DD"],
  ["force_relabel",        "false",    "Принудительная переразметка: true или false"],
  ["force_rebuild_report", "false",    "Перестроить отчёт: true или false"],
  ["report_date",          "latest",   "Дата отчёта: latest или YYYY-MM-DD"],
  ["provider_chain",       "opencode-zen", "Цепочка провайдеров LLM (через запятую)"],
  ["status",               "idle",     "Статус последнего прогона (обновляется автоматически)"]
];

// ─── Модуль 1: Меню (§4.3) ───────────────────────────────────────────────────

/**
 * Simple Trigger — вызывается при открытии таблицы.
 * Строит меню «SERPlux» по структуре §4.3.
 * НЕ запускать вручную через кнопку Run в редакторе Apps Script.
 */
function onOpen() {
  try {
    var ui = SpreadsheetApp.getUi();

    var clientsMenu = ui.createMenu("[К] Клиенты")
      .addItem("Показать список клиентов", "showClients")
      .addItem("Добавить клиента...", "addClient")
      .addItem("Обновить гео из Topvisor...", "updateClientGeos")
      .addSeparator()
      .addItem("Обновить список клиентов", "refreshClientList");

    var settingsMenu = ui.createMenu("⚙ Настройки")
      .addItem("Установить секрет", "setSecret")
      .addItem("Установить URL сервера", "setWebhookUrl")
      .addItem("[+] Инициализировать настройки", "initSettingsSheet")
      .addItem("[⟳] Пересоздать лист Настройки", "deleteAndRecreateSettingsSheet")
      .addItem("[!] Установить триггеры (1 раз)", "setupTriggers")
      .addSeparator()
      .addItem("Показать текущий профиль", "showProfile")
      .addItem("Управление провайдерами...", "manageProviders");

    ui.createMenu("SERPlux")
      .addItem("▶ Запустить сбор", "runCollection")
      .addItem("⟳ Проверить статус", "checkStatus")
      .addItem("[>] Построить отчёт за дату...", "buildReportForDate")
      .addSeparator()
      .addItem("Разметить собранные данные", "labelOnly")
      .addItem("Разметить за дату...", "labelOnlyForDate")
      .addSeparator()
      .addSubMenu(clientsMenu)
      .addSeparator()
      .addSubMenu(settingsMenu)
      .addToUi();
  } catch (e) {
    SpreadsheetApp.getUi().alert(
      "SERPlux: ошибка инициализации меню",
      e.message,
      SpreadsheetApp.getUi().ButtonSet.OK
    );
  }
}

// ─── Installable Trigger ──────────────────────────────────────────────────────

/**
 * Создаёт Installable Trigger для onOpen.
 * Запускать ОДИН РАЗ под своим аккаунтом (и владелец, и разработчики).
 * После этого меню SERPlux появляется при каждом открытии таблицы.
 */
function setupTriggers() {
  var ui = SpreadsheetApp.getUi();

  // Удаляем существующие onOpen-триггеры (чтобы не дублировать)
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "onOpen") {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  // Создаём Installable Trigger
  ScriptApp.newTrigger("onOpen")
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onOpen()
    .create();

  var email = Session.getActiveUser().getEmail();
  ui.alert(
    "Триггер установлен",
    "Installable Trigger создан для аккаунта:\n" + email + "\n\n" +
    "Теперь меню SERPlux будет появляться при каждом открытии таблицы.",
    ui.ButtonSet.OK
  );
}

// ─── Модуль 1: Лист «Настройки» (§4.2) ───────────────────────────────────────

/**
 * Получает список client_id из webhook GET /clients.
 * Используется для Data Validation dropdown в поле client_id.
 * 
 * @return {array} список строк с client_id, или пустой массив при ошибке
 */
function _getClientIdList() {
  var secret = _getSecret();
  if (!secret) {
    return [];
  }
  
  var result = _get("/clients", secret);
  if (!result.ok || !result.data || !Array.isArray(result.data)) {
    return [];
  }
  
  // Извлекаем client_id из каждого клиента в списке
  var clientIds = result.data.map(function(client) {
    return client.client_id || "";
  }).filter(function(id) { return id !== ""; });
  
  return clientIds.length > 0 ? clientIds : [];
}

/**
 * Устанавливает Data Validation для поля client_id (строка 1, колонка B).
 * Dropdown заполняется из GET /clients API.
 */
function _setupClientIdValidation(sheet) {
  var clientIds = _getClientIdList();
  
  if (clientIds.length === 0) {
    // Если список клиентов не получен — сохраняем свободный ввод с подсказкой
    sheet.getRange(1, 2).setDataValidation(
      SpreadsheetApp.newDataValidation()
        .setAllowInvalid(true)
        .setHelpText("ID клиента (например: client01). Загрузить из сервера: SERPlux → Настройки → [>] Обновить список клиентов")
        .build()
    );
  } else {
    // Устанавливаем dropdown из списка клиентов
    sheet.getRange(1, 2).setDataValidation(
      SpreadsheetApp.newDataValidation()
        .requireValueInList(clientIds, true)
        .setAllowInvalid(false)
        .setHelpText("Выберите клиента из списка")
        .build()
    );
  }
}

/**
 * Удаляет лист «Настройки» и пересоздаёт его с заполненной структурой.
 * Используется при повреждении листа или необходимости сброса настроек.
 */
function deleteAndRecreateSettingsSheet() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.alert(
    "Пересоздать лист «Настройки»?",
    "Это удалит текущий лист «Настройки» и создаст новый с предустановками.\n" +
    "Все пользовательские настройки будут потеряны.",
    ui.ButtonSet.OK_CANCEL
  );
  
  if (response !== ui.Button.OK) {
    return;
  }
  
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);
  
  if (sheet) {
    ss.deleteSheet(sheet);
  }
  
  // Пересоздаём лист с заполненной структурой
  initSettingsSheet();
}

/**
 * Создаёт или пересоздаёт лист «Настройки» с шаблоном ключей и Data Validation.
 * Формат: колонка A = ключ, B = значение, C = подсказка.
 */
function initSettingsSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(SETTINGS_SHEET_NAME);
  }

  // Очищаем содержимое (сохраняем форматирование если есть)
  sheet.clearContents();

  // Записываем шаблон
  var numRows = SETTINGS_TEMPLATE.length;
  sheet.getRange(1, 1, numRows, 3).setValues(SETTINGS_TEMPLATE);

  // Форматирование: ключи жирным, ширина колонок
  sheet.getRange(1, 1, numRows, 1).setFontWeight("bold");
  sheet.setColumnWidth(1, 200);
  sheet.setColumnWidth(2, 250);
  sheet.setColumnWidth(3, 400);

  // Data Validation для колонки B (значения)
  // client_id (строка 1): dropdown из GET /clients API
  _setupClientIdValidation(sheet);

  // depth (строка 2): список 10, 20, 50, 100
  sheet.getRange(2, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(["10", "20", "50", "100"], true)
      .setAllowInvalid(false)
      .setHelpText("Глубина сбора: 10, 20, 50 или 100")
      .build()
  );

  // with_labels (строка 3): true/false
  sheet.getRange(3, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(["true", "false"], true)
      .setAllowInvalid(false)
      .setHelpText("true или false")
      .build()
  );

  // label_mode (строка 4): auto, deep, domains, snippets или full
  sheet.getRange(4, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(["auto", "deep", "domains", "snippets", "full"], true)
      .setAllowInvalid(false)
      .setHelpText("Режим разметки: auto, deep, domains, snippets или full")
      .build()
  );

  // date (строка 5): свободный ввод с подсказкой
  sheet.getRange(5, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .setAllowInvalid(true)
      .setHelpText("today или дата в формате YYYY-MM-DD")
      .build()
  );

  // force_relabel (строка 6): true/false
  sheet.getRange(6, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(["true", "false"], true)
      .setAllowInvalid(false)
      .setHelpText("true или false")
      .build()
  );

  // force_rebuild_report (строка 7): true/false
  sheet.getRange(7, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(["true", "false"], true)
      .setAllowInvalid(false)
      .setHelpText("true или false")
      .build()
  );

  // report_date (строка 8): свободный ввод с подсказкой
  sheet.getRange(8, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .setAllowInvalid(true)
      .setHelpText("latest или дата в формате YYYY-MM-DD")
      .build()
  );

  // provider_chain (строка 9): свободный ввод с подсказкой
  // Dropdown будет установлен через refreshProviderChain()
  sheet.getRange(9, 2).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .setAllowInvalid(true)
      .setHelpText("Провайдер LLM или цепочка через запятую (напр. zen)")
      .build()
  );

  // Переключаемся на лист настроек
  ss.setActiveSheet(sheet);
  ss.toast("Лист «Настройки» инициализирован", "SERPlux", 5);
}

/**
 * Читает параметры из листа «Настройки» в объект.
 * Если лист отсутствует или ключ отсутствует — возвращает значения по умолчанию.
 *
 * @return {object} {clientId, depth, withLabels, labelMode, date,
 *                   forceRelabel, forceRebuildReport, reportDate,
 *                   providerChain, status}
 */
function _readSettings() {
  var defaults = {
    clientId: "",
    depth: DEFAULT_DEPTH,
    withLabels: true,
    labelMode: DEFAULT_LABEL_MODE,
    date: DEFAULT_DATE,
    forceRelabel: false,
    forceRebuildReport: false,
    reportDate: DEFAULT_REPORT_DATE,
    providerChain: "",
    status: "idle"
  };

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);
  if (!sheet) return defaults;

  var data = sheet.getDataRange().getValues();
  var settings = {};

  for (var i = 0; i < data.length; i++) {
    var key = String(data[i][0]).trim().toLowerCase();
    var val = data[i][1];

    switch (key) {
      case "client_id":
        settings.clientId = String(val).trim();
        break;
      case "depth":
        var parsed = parseInt(val, 10);
        settings.depth = (parsed > 0) ? parsed : DEFAULT_DEPTH;
        break;
      case "with_labels":
        settings.withLabels = String(val).trim().toLowerCase() !== "false";
        break;
      case "label_mode":
        var mode = String(val).trim().toLowerCase();
        settings.labelMode = (mode === "domains" || mode === "snippets" || mode === "full")
          ? mode : DEFAULT_LABEL_MODE;
        break;
      case "date":
        settings.date = String(val).trim() || DEFAULT_DATE;
        break;
      case "force_relabel":
        settings.forceRelabel = String(val).trim().toLowerCase() === "true";
        break;
      case "force_rebuild_report":
        settings.forceRebuildReport = String(val).trim().toLowerCase() === "true";
        break;
      case "report_date":
        settings.reportDate = String(val).trim() || DEFAULT_REPORT_DATE;
        break;
      case "provider_chain":
        settings.providerChain = String(val).trim();
        break;
      case "status":
        settings.status = String(val).trim();
        break;
    }
  }

  // Заполняем отсутствующие ключи значениями по умолчанию
  for (var k in defaults) {
    if (!(k in settings)) {
      settings[k] = defaults[k];
    }
  }

  return settings;
}

// ─── Модуль 2: Запуск и статус (§4.4) ────────────────────────────────────────

/**
 * «▶ Запустить сбор» — читает Настройки, валидирует, отправляет POST /run.
 *
 * Поток (§4.4):
 * 1. Читаем настройки
 * 2. Валидация: client_id задан? секрет задан?
 * 3. Диалог подтверждения
 * 4. POST /run с Bearer-авторизацией
 * 5. Обработка 202/409
 * 6. Обновление ячейки status
 */
function runCollection() {
  var ui = SpreadsheetApp.getUi();

  // 1. Читаем настройки
  var settings = _readSettings();

  // 2. Валидация: URL сервера
  var webhookUrl = _getWebhookUrl();
  if (!webhookUrl) {
    ui.alert(
      "Ошибка",
      "URL сервера не задан.\nЗапустите SERPlux → Настройки → Установить URL сервера.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Валидация: секрет
  var secret = _getSecret();
  if (!secret) {
    ui.alert(
      "Ошибка",
      "Секрет не задан.\nЗапустите SERPlux → Настройки → Установить секрет.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Валидация: client_id
  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  // 3. Диалог подтверждения
  var labelText = settings.withLabels
    ? "вкл (" + settings.labelMode + ")"
    : "выкл";

  var confirmMsg = "Запустить сбор для клиента «" + settings.clientId + "»?\n\n" +
    "Глубина: " + settings.depth + "\n" +
    "Разметка: " + labelText + "\n" +
    "Дата: " + settings.date + "\n" +
    "Переразметка: " + (settings.forceRelabel ? "да" : "нет") + "\n" +
    "Перестроить отчёт: " + (settings.forceRebuildReport ? "да" : "нет") + "\n" +
    "Провайдер: " + (settings.providerChain || "по умолчанию");

  var confirm = ui.alert(
    "Подтверждение запуска",
    confirmMsg,
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  // 4. POST /run — формируем тело по фактическому контракту webhook.py
  var payload = {
    client_id: settings.clientId,
    depth: settings.depth,
    with_labels: settings.withLabels,
    label_mode: settings.labelMode,
    force_relabel: settings.forceRelabel,
    date: settings.date,
    force_rebuild_report: settings.forceRebuildReport
  };
  if (settings.providerChain) {
    payload.provider_chain = settings.providerChain;
  }

  _updateStatusCell("starting");

  var result = _post("/run", payload, secret);

  // 5. Обработка ответа
  if (result.ok && result.code === 202) {
    var startedAt = (result.data && result.data.started_at) || "—";
    SpreadsheetApp.getActiveSpreadsheet().toast(
      "Прогон запущен. Начало: " + startedAt,
      "SERPlux",
      10
    );
    ui.alert(
      "SERPlux запущен",
      "Прогон запущен.\n" +
      "Начало: " + startedAt + "\n\n" +
      "Проверьте статус через меню SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
    _updateStatusCell("running");
    _appendLog(settings.clientId, "running", "Прогон запущен", "");
  } else if (result.code === 409) {
    var detail = (result.data && result.data.detail)
      ? result.data.detail
      : "Прогон уже выполняется, подождите завершения";
    ui.alert("SERPlux", detail, ui.ButtonSet.OK);
    _updateStatusCell("running");
  } else {
    var errorMsg = _friendlyError(result);
    ui.alert("Ошибка запуска", errorMsg, ui.ButtonSet.OK);
    _updateStatusCell("error");
    _appendLog(settings.clientId, "error", errorMsg, "");
  }
}

/**
 * «⟳ Проверить статус» — GET /status, диалог по маппингу состояний (§4.4).
 *
 * Маппинг:
 *   idle     → «Прогонов не было»
 *   starting → «Прогон выполняется... Начало: [started_at]»
 *   running  → «Прогон выполняется... Начало: [started_at]»
 *   ok       → «Прогон завершён успешно. Начало: [started_at], Конец: [finished_at]»
 *   error    → «Ошибка прогона: [message]»
 */
function checkStatus() {
  var ui = SpreadsheetApp.getUi();

  // Проверка секрета
  var secret = _getSecret();
  if (!secret) {
    ui.alert(
      "Ошибка",
      "Секрет не задан.\nЗапустите SERPlux → Настройки → Установить секрет.",
      ui.ButtonSet.OK
    );
    return;
  }

  var result = _get("/status", secret);

  if (!result.ok) {
    var errorMsg = _friendlyError(result);
    ui.alert("Ошибка", errorMsg, ui.ButtonSet.OK);
    return;
  }

  var d = result.data || {};
  var statusText = d.status || "unknown";
  var startedAt = d.started_at || "—";
  var finishedAt = d.finished_at || "—";
  var message = d.message || "";

  // Defensive: stats может отсутствовать
  var stats = d.stats || {};
  var providerUsed = stats.provider_used || "—";

  // Маппинг состояний по §4.4
  var dialogTitle = "";
  var dialogMsg = "";

  switch (statusText) {
    case "idle":
      dialogTitle = "Статус SERPlux";
      dialogMsg = "Прогонов не было.";
      break;

    case "starting":
    case "running":
      dialogTitle = "⟳ Прогон выполняется";
      dialogMsg = "Прогон выполняется...\nНачало: " + startedAt;
      break;

    case "ok":
      dialogTitle = "▶ Прогон завершён";
      dialogMsg = "Прогон завершён успешно.\n" +
        "Начало: " + startedAt + "\n" +
        "Конец: " + finishedAt;
      // Добавляем статистику если есть
      if (stats.collected !== undefined) {
        dialogMsg += "\n\nСобрано: " + (stats.collected || "—") +
          "\nСохранено: " + (stats.saved_new || "—") +
          "\nРазмечено: " + (stats.labeled || "—") +
          "\nВыгружено: " + (stats.exported || "—") +
          "\nПровайдер: " + providerUsed;
      }
      break;

    case "error":
      dialogTitle = "Ошибка прогона";
      dialogMsg = "Ошибка прогона: " + (message || "неизвестная ошибка");
      break;

    default:
      dialogTitle = "Статус SERPlux";
      dialogMsg = "Статус: " + statusText;
      if (startedAt !== "—") dialogMsg += "\nНачало: " + startedAt;
      if (message) dialogMsg += "\nСообщение: " + message;
  }

  ui.alert(dialogTitle, dialogMsg, ui.ButtonSet.OK);

  // Обновляем ячейку статуса и лог
  _updateStatusCell(statusText);
  _appendLog(
    d.client_id || _readSettings().clientId || "—",
    statusText,
    message || dialogMsg.split("\n")[0],
    providerUsed
  );
}

/**
 * «[>] Построить отчёт за дату...» — GET /clients/{id}/dates, выбор даты, POST /run с report_only.
 */
function buildReportForDate() {
  var ui = SpreadsheetApp.getUi();

  // Проверка секрета
  var secret = _getSecret();
  if (!secret) {
    ui.alert(
      "Ошибка",
      "Секрет не задан.\nЗапустите SERPlux → Настройки → Установить секрет.",
      ui.ButtonSet.OK
    );
    return;
  }

  var settings = _readSettings();

  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  // GET /clients/{id}/dates — список доступных дат
  var datesResult = _get("/clients/" + encodeURIComponent(settings.clientId) + "/dates", secret);
  if (!datesResult.ok) {
    ui.alert("Ошибка", _friendlyError(datesResult), ui.ButtonSet.OK);
    return;
  }

  var dates = (datesResult.data && datesResult.data.dates) || [];
  if (!Array.isArray(dates) || dates.length === 0) {
    ui.alert(
      "Нет данных",
      "Для клиента «" + settings.clientId + "» нет собранных данных.\n" +
      "Сначала запустите сбор через SERPlux → Запустить сбор.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Формируем список дат для выбора
  var datesList = dates.join(", ");
  var response = ui.prompt(
    "Построить отчёт за дату",
    "Доступные даты:\n" + datesList + "\n\n" +
    "Введите дату (YYYY-MM-DD) или оставьте пустым для последней доступной.",
    ui.ButtonSet.OK_CANCEL
  );

  if (response.getSelectedButton() !== ui.Button.OK) return;

  var reportDate = response.getResponseText().trim() || "latest";

  // Валидация: если введена дата, проверяем что она есть в списке
  if (reportDate !== "latest" && dates.indexOf(reportDate) === -1) {
    ui.alert(
      "Ошибка",
      "Дата «" + reportDate + "» не найдена в списке доступных.\n\n" +
      "Доступные даты:\n" + datesList,
      ui.ButtonSet.OK
    );
    return;
  }

  // Подтверждение
  var confirm = ui.alert(
    "Подтверждение",
    "Построить отчёт для клиента «" + settings.clientId + "» за дату: " + reportDate + "?",
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  // POST /run с report_only
  var payload = {
    client_id: settings.clientId,
    report_only: true,
    report_date: reportDate,
    force_rebuild_report: settings.forceRebuildReport
  };

  _updateStatusCell("starting");

  var result = _post("/run", payload, secret);

  if (result.ok && result.code === 202) {
    var startedAt = (result.data && result.data.started_at) || "—";
    ui.alert(
      "Запрос отправлен",
      "Запрос на построение отчёта отправлен.\n" +
      "Дата: " + reportDate + "\n" +
      "Начало: " + startedAt + "\n\n" +
      "Проверьте статус через SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
    _updateStatusCell("running");
    _appendLog(settings.clientId, "report_requested", "Отчёт за " + reportDate, "");
  } else if (result.code === 409) {
    var detail = (result.data && result.data.detail)
      ? result.data.detail
      : "Прогон уже выполняется, подождите завершения";
    ui.alert("SERPlux", detail, ui.ButtonSet.OK);
  } else {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    _updateStatusCell("error");
  }
}

// ─── Модуль 3: Клиенты и провайдеры (§4.4) ───────────────────────────────────

/**
 * «Показать список клиентов» — GET /clients, диалог со списком.
 */
function showClients() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var result = _get("/clients", secret);
  if (!result.ok) {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    return;
  }

  var clients = result.data || [];

  if (!Array.isArray(clients) || clients.length === 0) {
    ui.alert(
      "Клиенты",
      "Нет зарегистрированных клиентов.\n\n" +
      "Добавьте клиента через SERPlux → Клиенты → Добавить клиента...",
      ui.ButtonSet.OK
    );
    return;
  }

  var text = "Зарегистрированные клиенты (" + clients.length + "):\n\n";
  for (var i = 0; i < clients.length; i++) {
    var c = clients[i];
    text += (i + 1) + ". " + (c.client_id || "—") + " — " + (c.client_name || "—");
    if (c.project_id) text += "\n   project_id: " + c.project_id;
    if (c.sheet_id)   text += ", sheet_id: " + c.sheet_id;
    text += "\n\n";
  }

  ui.alert("Клиенты", text, ui.ButtonSet.OK);
}

/**
 * «Добавить клиента...» — многошаговый prompt, POST /clients.
 *
 * Шаги:
 * 1. client_id (латиница, без пробелов)
 * 2. client_name (отображаемое имя)
 * 3. project_id в Topvisor (опционально)
 * 4. Google Sheet ID (опционально)
 * 5. Если project_id задан — GET /topvisor/regions → мультивыбор гео
 * 6. Выбор сорсеров (google/yandex_ru/yandex_com)
 */
function addClient() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  // Шаг 1: client_id
  var r1 = ui.prompt(
    "Добавить клиента (шаг 1/6)",
    "Введите ID клиента (латиница, без пробелов).\nНапример: sudheimer, client2",
    ui.ButtonSet.OK_CANCEL
  );
  if (r1.getSelectedButton() !== ui.Button.OK) return;
  var clientId = r1.getResponseText().trim().toLowerCase();

  if (!clientId) {
    ui.alert("Ошибка", "ID клиента не может быть пустым.", ui.ButtonSet.OK);
    return;
  }
  if (!/^[a-z0-9_-]+$/.test(clientId)) {
    ui.alert(
      "Ошибка",
      "ID клиента должен содержать только латинские буквы, цифры, дефис и подчёркивание.\nПолучено: «" + clientId + "»",
      ui.ButtonSet.OK
    );
    return;
  }

  // Шаг 2: client_name
  var r2 = ui.prompt(
    "Добавить клиента (шаг 2/6)",
    "Введите отображаемое имя клиента.\nНапример: Sudheimer Group",
    ui.ButtonSet.OK_CANCEL
  );
  if (r2.getSelectedButton() !== ui.Button.OK) return;
  var clientName = r2.getResponseText().trim();

  if (!clientName) {
    ui.alert("Ошибка", "Имя клиента не может быть пустым.", ui.ButtonSet.OK);
    return;
  }

  // Шаг 3: project_id (опционально)
  var r3 = ui.prompt(
    "Добавить клиента (шаг 3/6)",
    "Введите project_id в Topvisor (число).\nОставьте пустым, если неизвестен.",
    ui.ButtonSet.OK_CANCEL
  );
  if (r3.getSelectedButton() !== ui.Button.OK) return;
  var projectIdStr = r3.getResponseText().trim();
  var projectId = projectIdStr ? parseInt(projectIdStr, 10) : null;
  if (projectIdStr && isNaN(projectId)) {
    ui.alert("Ошибка", "project_id должен быть числом. Получено: «" + projectIdStr + "»", ui.ButtonSet.OK);
    return;
  }

  // Шаг 4: sheet_id (опционально)
  var r4 = ui.prompt(
    "Добавить клиента (шаг 4/6)",
    "Введите Google Sheet ID таблицы клиента.\nОставьте пустым, если неизвестен.",
    ui.ButtonSet.OK_CANCEL
  );
  if (r4.getSelectedButton() !== ui.Button.OK) return;
  var sheetId = r4.getResponseText().trim() || null;

  var payload = {
    client_id: clientId,
    client_name: clientName
  };
  if (projectId !== null) payload.project_id = projectId;
  if (sheetId !== null)   payload.sheet_id = sheetId;

  // Шаг 5: гео из Topvisor (если project_id задан)
  var selectedGeos = null;
  if (projectId !== null) {
    var regionsResult = _get("/topvisor/regions?project_id=" + projectId, secret);
    if (regionsResult.ok) {
      var regions = (regionsResult.data && regionsResult.data.regions) || [];
      if (Array.isArray(regions) && regions.length > 0) {
        // Формируем список регионов
        var regionsList = "";
        for (var i = 0; i < regions.length; i++) {
          regionsList += (i + 1) + ". " + regions[i].name + " (index: " + regions[i].index + ")\n";
        }

        var geoResponse = ui.prompt(
          "Добавить клиента (шаг 5/6) — Выбор гео",
          "Доступные регионы из Topvisor:\n" + regionsList + "\n" +
          "Введите номера регионов через запятую (например: 1,3,5).\n" +
          "Оставьте пустым для выбора всех регионов.",
          ui.ButtonSet.OK_CANCEL
        );
        if (geoResponse.getSelectedButton() !== ui.Button.OK) return;

        var geoStr = geoResponse.getResponseText().trim();
        if (!geoStr) {
          // Выбрать все
          selectedGeos = [];
          for (var i = 0; i < regions.length; i++) {
            selectedGeos.push(regions[i].name);
          }
        } else {
          // Парсим номера
          var selectedNums = geoStr.split(",").map(function(s) { return parseInt(s.trim(), 10); });
          selectedGeos = [];
          for (var i = 0; i < selectedNums.length; i++) {
            var idx = selectedNums[i] - 1;
            if (idx >= 0 && idx < regions.length) {
              selectedGeos.push(regions[idx].name);
            }
          }
          if (selectedGeos.length === 0) {
            ui.alert("Ошибка", "Не удалось распознать выбранные регионы.", ui.ButtonSet.OK);
            return;
          }
        }
      }
    } else if (regionsResult.code === 502) {
      // Defensive: Topvisor недоступен — fallback на ручной ввод
      ui.alert(
        "Topvisor недоступен",
        "Не удалось получить гео из Topvisor.\nВведите гео вручную (через запятую).\n\n" +
        "Пример: Литва, Германия, Великобритания",
        ui.ButtonSet.OK
      );
      var manualGeoResponse = ui.prompt(
        "Добавить клиента (шаг 5/6) — Ручной ввод гео",
        "Введите гео через запятую.\nОставьте пустым, если не знаете.",
        ui.ButtonSet.OK_CANCEL
      );
      if (manualGeoResponse.getSelectedButton() !== ui.Button.OK) return;
      var manualGeoStr = manualGeoResponse.getResponseText().trim();
      if (manualGeoStr) {
        selectedGeos = manualGeoStr.split(",").map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; });
      }
    } else {
      // Другая ошибка — пропускаем гео
      ui.alert(
        "Предупреждение",
        "Не удалось получить регионы из Topvisor.\nГео не будут заданы. Вы сможете обновить их позже через «Обновить гео из Topvisor».",
        ui.ButtonSet.OK
      );
    }
  }

  if (selectedGeos && selectedGeos.length > 0) {
    payload.geos = selectedGeos;
  }

  // Шаг 6: сорсеры
  var searcherResponse = ui.prompt(
    "Добавить клиента (шаг 6/6) — Выбор поисковиков",
    "Выберите поисковики (введите номера через запятую):\n" +
    "  1. google\n" +
    "  2. yandex_ru\n" +
    "  3. yandex_com\n\n" +
    "Оставьте пустым для выбора всех трёх.",
    ui.ButtonSet.OK_CANCEL
  );
  if (searcherResponse.getSelectedButton() !== ui.Button.OK) return;

  var searcherStr = searcherResponse.getResponseText().trim();
  var allSearchers = ["google", "yandex_ru", "yandex_com"];
  var selectedSearchers = null;

  if (!searcherStr) {
    selectedSearchers = allSearchers;
  } else {
    var searcherNums = searcherStr.split(",").map(function(s) { return parseInt(s.trim(), 10); });
    selectedSearchers = [];
    for (var i = 0; i < searcherNums.length; i++) {
      var idx = searcherNums[i] - 1;
      if (idx >= 0 && idx < allSearchers.length) {
        selectedSearchers.push(allSearchers[idx]);
      }
    }
    if (selectedSearchers.length === 0) {
      ui.alert("Ошибка", "Не выбрано ни одного поисковика.", ui.ButtonSet.OK);
      return;
    }
  }

  payload.searchers = selectedSearchers;

  // POST /clients
  var result = _post("/clients", payload, secret);

  if (result.ok && result.code === 201) {
    var summaryMsg = "Клиент «" + clientName + "» (" + clientId + ") успешно добавлен.\n\n";
    if (projectId !== null) summaryMsg += "Project ID: " + projectId + "\n";
    if (sheetId !== null)   summaryMsg += "Sheet ID: " + sheetId + "\n";
    if (selectedGeos && selectedGeos.length > 0) {
      summaryMsg += "Гео: " + selectedGeos.join(", ") + "\n";
    }
    if (selectedSearchers && selectedSearchers.length > 0) {
      summaryMsg += "Поисковики: " + selectedSearchers.join(", ") + "\n";
    }
    summaryMsg += "\nНе забудьте загрузить карту регионов на сервер:\n" +
      "regions_map_" + clientId + ".json";

    ui.alert("Клиент добавлен", summaryMsg, ui.ButtonSet.OK);
  } else if (result.code === 409) {
    var detail = (result.data && result.data.detail)
      ? result.data.detail
      : "Клиент с ID «" + clientId + "» уже существует.";
    ui.alert("Ошибка", detail, ui.ButtonSet.OK);
  } else {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
  }
}

/**
 * «Показать текущий профиль» — GET /clients/{client_id}, диалог ключ:значение.
 */
function showProfile() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var settings = _readSettings();
  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  var result = _get("/clients/" + encodeURIComponent(settings.clientId), secret);

  if (!result.ok) {
    if (result.code === 404) {
      ui.alert(
        "Профиль не найден",
        "Клиент «" + settings.clientId + "» не найден на сервере.\n" +
        "Добавьте клиента через SERPlux → Клиенты → Добавить клиента...",
        ui.ButtonSet.OK
      );
    } else {
      ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    }
    return;
  }

  var p = result.data || {};
  var text = "Профиль клиента:\n\n" +
    "ID: " + (p.client_id || "—") + "\n" +
    "Имя: " + (p.client_name || "—") + "\n" +
    "Project ID: " + (p.project_id || "—") + "\n" +
    "Sheet ID: " + (p.sheet_id || "—") + "\n" +
    "Создан: " + (p.created_at || "—") + "\n" +
    "Обновлён: " + (p.updated_at || "—");

  ui.alert("Профиль: " + settings.clientId, text, ui.ButtonSet.OK);
}

/**
 * «Управление провайдерами...» — GET /providers, диалог-таблица.
 *
 * Кнопки управления — заглушки: POST/PUT/DELETE /providers не реализованы
 * (ADR 2026-07-03: провайдеры в config.py, read-only).
 */
function manageProviders() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var result = _get("/providers", secret);

  if (!result.ok) {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    return;
  }

  var providers = result.data || [];

  var text = "Провайдеры LLM:\n\n";

  if (!Array.isArray(providers) || providers.length === 0) {
    text += "(нет зарегистрированных провайдеров)\n";
  } else {
    for (var i = 0; i < providers.length; i++) {
      var p = providers[i];
      var statusText = p.enabled ? "включён" : "выключен";
      text += (i + 1) + ". " + (p.id || "—") + "\n" +
        "   Модель: " + (p.default_model || "—") + "\n" +
        "   Статус: " + statusText + " | Приоритет: " + (p.priority || "—") + "\n";
      if (p.models && p.models.length > 0) {
        text += "   Доступные модели: " + p.models.join(", ") + "\n";
      }
      text += "\n";
    }
  }

  text += "─────────────────────────────\n" +
    "Управление провайдерами (добавление, включение/выключение,\n" +
    "приоритет) доступно через config.py на сервере.\n" +
    "API-эндпоинты POST/PUT/DELETE /providers не реализованы.";

  ui.alert("Управление провайдерами", text, ui.ButtonSet.OK);
}

/**
 * «Разметить собранные данные» — POST /run с label_only=true.
 * Размечает существующие данные без повторного сбора.
 */
function labelOnly() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var settings = _readSettings();
  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Подтверждение
  var confirmMsg = "Разметить собранные данные для клиента «" + settings.clientId + "»?\n\n" +
    "Режим: " + settings.labelMode + "\n" +
    "Переразметка: " + (settings.forceRelabel ? "да" : "нет") + "\n" +
    "Провайдер: " + (settings.providerChain || "по умолчанию");

  var confirm = ui.alert("Разметка данных", confirmMsg, ui.ButtonSet.YES_NO);
  if (confirm !== ui.Button.YES) return;

  var payload = {
    client_id: settings.clientId,
    label_only: true,
    label_mode: settings.labelMode,
    force_relabel: settings.forceRelabel
  };
  if (settings.providerChain) {
    payload.provider_chain = settings.providerChain;
  }

  _updateStatusCell("starting");
  var result = _post("/run", payload, secret);

  if (result.ok && result.code === 202) {
    var startedAt = (result.data && result.data.started_at) || "—";
    ui.alert(
      "Разметка запущена",
      "Запрос на разметку отправлен.\n" +
      "Начало: " + startedAt + "\n\n" +
      "Проверьте статус через SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
    _updateStatusCell("running");
    _appendLog(settings.clientId, "label_only", "Разметка (label_only)", "");
  } else if (result.code === 409) {
    var detail = (result.data && result.data.detail)
      ? result.data.detail
      : "Прогон уже выполняется, подождите завершения";
    ui.alert("SERPlux", detail, ui.ButtonSet.OK);
  } else {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    _updateStatusCell("error");
  }
}

/**
 * «Разметить за дату...» — GET /clients/{id}/dates, выбор даты, POST /run с label_only=true.
 */
function labelOnlyForDate() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var settings = _readSettings();
  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  // GET /clients/{id}/dates
  var datesResult = _get("/clients/" + encodeURIComponent(settings.clientId) + "/dates", secret);
  if (!datesResult.ok) {
    ui.alert("Ошибка", _friendlyError(datesResult), ui.ButtonSet.OK);
    return;
  }

  var dates = (datesResult.data && datesResult.data.dates) || [];
  if (!Array.isArray(dates) || dates.length === 0) {
    ui.alert(
      "Нет данных",
      "Для клиента «" + settings.clientId + "» нет собранных данных.\n" +
      "Сначала запустите сбор через SERPlux → Запустить сбор.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Выбор даты
  var datesList = dates.join(", ");
  var dateResponse = ui.prompt(
    "Разметить за дату",
    "Доступные даты:\n" + datesList + "\n\n" +
    "Введите дату (YYYY-MM-DD):",
    ui.ButtonSet.OK_CANCEL
  );
  if (dateResponse.getSelectedButton() !== ui.Button.OK) return;

  var targetDate = dateResponse.getResponseText().trim();
  if (!targetDate) {
    ui.alert("Ошибка", "Дата не может быть пустой.", ui.ButtonSet.OK);
    return;
  }
  if (dates.indexOf(targetDate) === -1) {
    ui.alert(
      "Ошибка",
      "Дата «" + targetDate + "» не найдена в списке доступных.\n\n" +
      "Доступные даты:\n" + datesList,
      ui.ButtonSet.OK
    );
    return;
  }

  // Выбор режима разметки
  var modeResponse = ui.prompt(
    "Режим разметки",
    "Введите режим разметки:\n" +
    "  domains — по справочнику доменов (без LLM)\n" +
    "  snippets — по сниппетам (с LLM)\n" +
    "  full — по полному тексту (v2, заглушка)\n\n" +
    "Текущий: " + settings.labelMode,
    ui.ButtonSet.OK_CANCEL
  );
  if (modeResponse.getSelectedButton() !== ui.Button.OK) return;

  var labelMode = modeResponse.getResponseText().trim() || settings.labelMode;
  if (labelMode !== "domains" && labelMode !== "snippets" && labelMode !== "full") {
    ui.alert("Ошибка", "Недопустимый режим: «" + labelMode + "».\nДопустимо: domains, snippets, full.", ui.ButtonSet.OK);
    return;
  }

  // force_relabel
  var forceResponse = ui.alert(
    "Переразметка",
    "Принудительная переразметка (игнорировать кэш)?",
    ui.ButtonSet.YES_NO
  );
  var forceRelabel = (forceResponse === ui.Button.YES);

  // Подтверждение
  var confirmMsg = "Разметить данные за " + targetDate + " для клиента «" + settings.clientId + "»?\n\n" +
    "Режим: " + labelMode + "\n" +
    "Переразметка: " + (forceRelabel ? "да" : "нет") + "\n" +
    "Провайдер: " + (settings.providerChain || "по умолчанию");

  var confirm = ui.alert("Подтверждение", confirmMsg, ui.ButtonSet.YES_NO);
  if (confirm !== ui.Button.YES) return;

  var payload = {
    client_id: settings.clientId,
    label_only: true,
    date: targetDate,
    label_mode: labelMode,
    force_relabel: forceRelabel
  };
  if (settings.providerChain) {
    payload.provider_chain = settings.providerChain;
  }

  _updateStatusCell("starting");
  var result = _post("/run", payload, secret);

  if (result.ok && result.code === 202) {
    var startedAt = (result.data && result.data.started_at) || "—";
    ui.alert(
      "Разметка запущена",
      "Запрос на разметку за " + targetDate + " отправлен.\n" +
      "Начало: " + startedAt + "\n\n" +
      "Проверьте статус через SERPlux → Проверить статус.",
      ui.ButtonSet.OK
    );
    _updateStatusCell("running");
    _appendLog(settings.clientId, "label_only", "Разметка за " + targetDate, "");
  } else if (result.code === 409) {
    var detail = (result.data && result.data.detail)
      ? result.data.detail
      : "Прогон уже выполняется, подождите завершения";
    ui.alert("SERPlux", detail, ui.ButtonSet.OK);
  } else {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    _updateStatusCell("error");
  }
}

/**
 * «Обновить список клиентов» — GET /clients → Data Validation dropdown на ячейке client_id.
 */
function refreshClientList() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var result = _get("/clients", secret);
  if (!result.ok) {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
    return;
  }

  var clients = result.data || [];
  if (!Array.isArray(clients) || clients.length === 0) {
    ui.alert(
      "Нет клиентов",
      "Нет зарегистрированных клиентов.\nДобавьте клиента через SERPlux → Клиенты → Добавить клиента...",
      ui.ButtonSet.OK
    );
    return;
  }

  // Извлекаем client_id
  var clientIds = [];
  for (var i = 0; i < clients.length; i++) {
    if (clients[i].client_id) {
      clientIds.push(clients[i].client_id);
    }
  }

  if (clientIds.length === 0) {
    ui.alert("Ошибка", "Не удалось извлечь список клиентов.", ui.ButtonSet.OK);
    return;
  }

  // Находим ячейку client_id на листе «Настройки»
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);
  if (!sheet) {
    ui.alert(
      "Ошибка",
      "Лист «Настройки» не найден.\nЗапустите SERPlux → Настройки → [+] Инициализировать настройки.",
      ui.ButtonSet.OK
    );
    return;
  }

  var data = sheet.getDataRange().getValues();
  var clientRow = -1;
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim().toLowerCase() === "client_id") {
      clientRow = i + 1;
      break;
    }
  }

  if (clientRow === -1) {
    ui.alert("Ошибка", "Ключ «client_id» не найден на листе «Настройки».", ui.ButtonSet.OK);
    return;
  }

  // Устанавливаем Data Validation dropdown
  var cell = sheet.getRange(clientRow, 2);
  cell.setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(clientIds, true)
      .setAllowInvalid(false)
      .setHelpText("Выберите клиента из списка")
      .build()
  );

  ui.alert(
    "Список клиентов обновлён",
    "Выпадающий список клиентов установлен на листе «Настройки».\n\n" +
    "Доступные клиенты:\n" + clientIds.join(", "),
    ui.ButtonSet.OK
  );

  // Автоматически обновляем список провайдеров
  refreshProviderChain();
}

/**
 * Обновляет выпадающий список provider_chain на листе «Настройки».
 * GET /providers → Data Validation dropdown.
 */
function refreshProviderChain() {
  var secret = _getSecret();
  if (!secret) {
    // Тихо выходим если нет секрета (вызывается из refreshClientList)
    return;
  }

  var result = _get("/providers", secret);
  if (!result.ok) {
    // Тихо выходим при ошибке
    return;
  }

  var providers = result.data || [];
  if (!Array.isArray(providers) || providers.length === 0) {
    return;
  }

  // Извлекаем id провайдеров
  var providerIds = [];
  for (var i = 0; i < providers.length; i++) {
    if (providers[i].id && providers[i].enabled) {
      providerIds.push(providers[i].id);
    }
  }

  if (providerIds.length === 0) {
    return;
  }

  // Находим ячейку provider_chain на листе «Настройки»
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);
  if (!sheet) return;

  var data = sheet.getDataRange().getValues();
  var providerRow = -1;
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim().toLowerCase() === "provider_chain") {
      providerRow = i + 1;
      break;
    }
  }

  if (providerRow === -1) return;

  // Устанавливаем Data Validation dropdown
  var cell = sheet.getRange(providerRow, 2);
  cell.setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(providerIds, true)
      .setAllowInvalid(true)  // разрешаем ручной ввод для цепочек через запятую
      .setHelpText("Выберите провайдера или введите цепочку через запятую")
      .build()
  );
}

/**
 * «Обновить гео из Topvisor...» — GET /topvisor/regions → PUT /clients/{id} с geos.
 */
function updateClientGeos() {
  var ui = SpreadsheetApp.getUi();
  var secret = _getSecret();
  if (!secret) {
    ui.alert("Ошибка", "Секрет не задан. Запустите SERPlux → Настройки → Установить секрет.", ui.ButtonSet.OK);
    return;
  }

  var settings = _readSettings();
  if (!settings.clientId) {
    ui.alert(
      "Ошибка",
      "Выберите клиента в листе Настройки.\nПоле client_id не заполнено.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Получаем профиль клиента
  var profileResult = _get("/clients/" + encodeURIComponent(settings.clientId), secret);
  if (!profileResult.ok) {
    ui.alert("Ошибка", _friendlyError(profileResult), ui.ButtonSet.OK);
    return;
  }

  var profile = profileResult.data || {};
  var projectId = profile.project_id;
  if (!projectId) {
    ui.alert(
      "Ошибка",
      "У клиента «" + settings.clientId + "» не задан project_id.\n" +
      "Обновите профиль клиента через SERPlux → Клиенты → Добавить клиента...",
      ui.ButtonSet.OK
    );
    return;
  }

  // GET /topvisor/regions
  var regionsResult = _get("/topvisor/regions?project_id=" + projectId, secret);
  if (!regionsResult.ok) {
    if (regionsResult.code === 502) {
      ui.alert(
        "Ошибка Topvisor",
        "Не удалось получить гео из Topvisor.\nПопробуйте позже или введите гео вручную.",
        ui.ButtonSet.OK
      );
    } else {
      ui.alert("Ошибка", _friendlyError(regionsResult), ui.ButtonSet.OK);
    }
    return;
  }

  var regions = (regionsResult.data && regionsResult.data.regions) || [];
  if (!Array.isArray(regions) || regions.length === 0) {
    ui.alert(
      "Нет регионов",
      "Для проекта " + projectId + " не найдено регионов в Topvisor.",
      ui.ButtonSet.OK
    );
    return;
  }

  // Формируем список регионов для мультивыбора
  var regionsList = "";
  for (var i = 0; i < regions.length; i++) {
    regionsList += (i + 1) + ". " + regions[i].name + " (index: " + regions[i].index + ")\n";
  }

  var response = ui.prompt(
    "Выбор гео",
    "Доступные регионы:\n" + regionsList + "\n" +
    "Введите номера регионов через запятую (например: 1,3,5):",
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() !== ui.Button.OK) return;

  var selectedStr = response.getResponseText().trim();
  if (!selectedStr) {
    ui.alert("Ошибка", "Не выбрано ни одного региона.", ui.ButtonSet.OK);
    return;
  }

  // Парсим номера
  var selectedNums = selectedStr.split(",").map(function(s) { return parseInt(s.trim(), 10); });
  var selectedGeos = [];
  for (var i = 0; i < selectedNums.length; i++) {
    var idx = selectedNums[i] - 1;
    if (idx >= 0 && idx < regions.length) {
      selectedGeos.push(regions[idx].name);
    }
  }

  if (selectedGeos.length === 0) {
    ui.alert("Ошибка", "Не удалось распознать выбранные регионы.", ui.ButtonSet.OK);
    return;
  }

  // Подтверждение
  var confirm = ui.alert(
    "Подтверждение",
    "Обновить гео для клиента «" + settings.clientId + "»?\n\n" +
    "Выбранные гео:\n" + selectedGeos.join(", "),
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  // PUT /clients/{id}
  var payload = { geos: selectedGeos };
  var result = _request("put", "/clients/" + encodeURIComponent(settings.clientId), payload, secret);

  if (result.ok) {
    ui.alert(
      "Гео обновлены",
      "Гео для клиента «" + settings.clientId + "» обновлены:\n" + selectedGeos.join(", "),
      ui.ButtonSet.OK
    );
  } else {
    ui.alert("Ошибка", _friendlyError(result), ui.ButtonSet.OK);
  }
}

// ─── Модуль 4: Секрет и индикация (§4.4, §4.5) ──────────────────────────────

/**
 * «Установить секрет» — prompt → сохранить WEBHOOK_SECRET в Script Properties.
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
    ui.alert("Ошибка", "Секрет не может быть пустым.", ui.ButtonSet.OK);
    return;
  }

  PropertiesService.getScriptProperties().setProperty("WEBHOOK_SECRET", secret);
  ui.alert("Готово", "Секрет сохранён в Script Properties.", ui.ButtonSet.OK);
}

/**
 * «Установить URL сервера» — prompt → сохранить WEBHOOK_URL в Script Properties.
 */
function setWebhookUrl() {
  var ui = SpreadsheetApp.getUi();
  var currentUrl = _getWebhookUrl();

  var response = ui.prompt(
    "Установить URL сервера",
    "Введите URL webhook-сервера:\n(например: https://serp.example.com)\n\n" +
    "Текущий: " + (currentUrl || "не задан"),
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() !== ui.Button.OK) return;

  var url = response.getResponseText().trim();
  if (!url) {
    ui.alert("Ошибка", "URL не может быть пустым.", ui.ButtonSet.OK);
    return;
  }

  // Убираем trailing slash
  url = url.replace(/\/$/, "");
  PropertiesService.getScriptProperties().setProperty("WEBHOOK_URL", url);
  ui.alert("Готово", "URL сервера сохранён:\n" + url, ui.ButtonSet.OK);
}

/**
 * Обновляет ячейку статуса на листе «Настройки» (строка «status», колонка B).
 * Цветовая заливка по §4.5:
 *   idle          → серый (#e2e3e5)
 *   starting/running → жёлтый (#fff3cd)
 *   ok            → зелёный (#d4edda)
 *   error         → красный (#f8d7da)
 *
 * @param {string} status — статус прогона
 */
function _updateStatusCell(status) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SETTINGS_SHEET_NAME);
  if (!sheet) return;

  var data = sheet.getDataRange().getValues();
  var statusRow = -1;

  // Ищем строку с ключом "status"
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim().toLowerCase() === "status") {
      statusRow = i + 1; // 1-indexed
      break;
    }
  }

  // Если строки нет — добавляем
  if (statusRow === -1) {
    statusRow = data.length + 1;
    sheet.getRange(statusRow, 1).setValue("status").setFontWeight("bold");
    sheet.getRange(statusRow, 3).setValue("Статус последнего прогона (обновляется автоматически)");
  }

  var cell = sheet.getRange(statusRow, 2); // колонка B
  cell.setValue(status);

  // Цветовая заливка по §4.5
  switch (status) {
    case "ok":
      cell.setBackground("#d4edda"); // зелёный
      break;
    case "error":
      cell.setBackground("#f8d7da"); // красный
      break;
    case "running":
    case "starting":
      cell.setBackground("#fff3cd"); // жёлтый
      break;
    default:
      cell.setBackground("#e2e3e5"); // серый (idle и прочие)
  }
}

/**
 * Дозаписывает строку в лист «Лог» (§4.5).
 * Если лист не существует — создаёт его с заголовками.
 *
 * Колонки: Дата/время, Клиент, Статус, Сообщение, Провайдер
 *
 * @param {string} client — ID клиента
 * @param {string} status — статус прогона
 * @param {string} message — сообщение
 * @param {string} providerUsed — фактический провайдер LLM
 */
function _appendLog(client, status, message, providerUsed) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(LOG_SHEET_NAME);

    // Создаём лист «Лог» если отсутствует
    if (!sheet) {
      sheet = ss.insertSheet(LOG_SHEET_NAME);
      sheet.appendRow(["Дата/время", "Клиент", "Статус", "Сообщение", "Провайдер"]);
      sheet.getRange(1, 1, 1, 5).setFontWeight("bold");
      sheet.setFrozenRows(1);
    }

    var timestamp = Utilities.formatDate(
      new Date(),
      Session.getScriptTimeZone(),
      "yyyy-MM-dd HH:mm:ss"
    );

    sheet.appendRow([
      timestamp,
      client || "—",
      status || "—",
      (message || "").substring(0, 500), // ограничиваем длину
      providerUsed || "—"
    ]);
  } catch (e) {
    // Лог не критичен — не роняем основную функцию
    Logger.log("Ошибка записи в лог: " + e.message);
  }
}

// ─── HTTP-хелперы ─────────────────────────────────────────────────────────────

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
 * Все запросы используют Bearer-авторизацию.
 *
 * @param {string} method — "get" или "post"
 * @param {string} path — путь эндпоинта (/run, /status, /clients, /providers)
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
    try { data = JSON.parse(body); } catch (e) { /* не-JSON ответ */ }
    return { ok: code >= 200 && code < 300, code: code, body: body, data: data };
  } catch (e) {
    return { ok: false, code: 0, body: String(e), data: {} };
  }
}

/**
 * Формирует user-friendly сообщение об ошибке по HTTP-ответу.
 * Не показывает stack traces и сырые JSON-тела.
 *
 * @param {object} result — результат _request()
 * @return {string} понятное сообщение для диалога
 */
function _friendlyError(result) {
  if (result.code === 0) {
    return "Не удалось связаться с сервером.\nПроверьте URL и доступность сервера.\n\nДетали: " + result.body;
  }

  switch (result.code) {
    case 401:
      return "Неверный секрет или отсутствует авторизация.\nЗапустите SERPlux → Настройки → Установить секрет.";
    case 403:
      return "Доступ запрещён. Проверьте секрет.";
    case 404:
      return "Эндпоинт не найден. Проверьте URL сервера.";
    case 409:
      return (result.data && result.data.detail)
        ? result.data.detail
        : "Конфликт: операция уже выполняется.";
    case 422:
      return "Неверные параметры запроса.\nПроверьте настройки на листе «Настройки».";
    case 500:
    case 502:
    case 503:
      return "Ошибка сервера (HTTP " + result.code + ").\nПопробуйте позже или обратитесь к администратору.";
    default:
      // Для неизвестных кодов — показываем detail из ответа если есть
      if (result.data && result.data.detail) {
        return "Ошибка (HTTP " + result.code + "):\n" + result.data.detail;
      }
      return "Ошибка HTTP " + result.code + ".\nОбратитесь к администратору.";
  }
}

/** Читает WEBHOOK_SECRET из Script Properties. */
function _getSecret() {
  return PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET") || "";
}

/** Читает WEBHOOK_URL из Script Properties. */
function _getWebhookUrl() {
  return PropertiesService.getScriptProperties().getProperty("WEBHOOK_URL") || "";
}
