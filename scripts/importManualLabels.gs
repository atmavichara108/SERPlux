/**
 * Импорт ручной разметки Михаила из Лист1 в domain_labels.
 * Структура Лист1 (блочная раскладка):
 * - Строка-заголовок блока: субъекты в колонках (Juri Sudheimer, Erik Sudheimer, ...)
 * - Под каждым субъектом пара колонок [позиция | URL]
 * - Строки-разделители гео: "Lithuania", "Germany", "United Kingdom", ...
 * - Метка = цвет фона ячейки с номером позиции
 *
 * Маппинг цветов (RGB hex, без #):
 * - 6aa84f, 93c47d → positive
 * - 990000 → negative
 * - f1c232, ffd966 → neutral
 * - иные → не угадываем, пишем в лист "Спорные"
 *
 * Результат:
 * - Лист "Эталон разметки" — проверка глазами
 * - Лист "Спорные" — неизвестные цвета (row, col, hex, url)
 * - POST /labels/import с массивом {domain, query, geo, sentiment, source:"manual_l1"}
 */

const WEBHOOK_URL = "http://127.0.0.1:8000/labels/import";
const WEBHOOK_SECRET = PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET");

// Маппинг цветов (нижний регистр, без #)
const COLOR_MAP = {
  "6aa84f": "positive",
  "93c47d": "positive",
  "990000": "negative",
  "f1c232": "neutral",
  "ffd966": "neutral",
};

// Гео-разделители (regexp)
const GEO_MARKERS = ["Lithuania", "Germany", "United Kingdom", "France", "Poland", "Netherlands", "Belgium", "Spain", "Italy"];

/**
 * Основная функция импорта
 */
function importManualLabels() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet1 = ss.getSheetByName("Лист1");
  
  if (!sheet1) {
    Logger.log("ERROR: Лист1 не найден");
    return;
  }

  const values = sheet1.getDataRange().getValues();
  const backgrounds = sheet1.getDataRange().getBackgrounds();
  const lastRow = values.length;
  const lastCol = values[0] ? values[0].length : 0;

  Logger.log(`Лист1: ${lastRow} строк × ${lastCol} колонок`);

  // Парсим блоки
  const blocks = parseBlocks(values, backgrounds, lastRow, lastCol);
  Logger.log(`Найдено блоков: ${blocks.length}`);

  // Собираем labels и спорные
  const labels = [];
  const disputes = [];

  for (const block of blocks) {
    for (const item of block.items) {
      if (item.sentiment) {
        labels.push({
          domain: item.domain,
          query: block.query,
          geo: block.geo,
          sentiment: item.sentiment,
          source: "manual_l1",
        });
      } else if (item.unknownColor) {
        disputes.push({
          row: item.row,
          col: item.col,
          hex: item.unknownColor,
          url: item.url,
          geo: block.geo,
          query: block.query,
        });
      }
    }
  }

  Logger.log(`Собрано labels: ${labels.length}, спорных: ${disputes.length}`);

  // Пишем "Эталон разметки"
  writeReferenceSheet(ss, labels);

  // Пишем "Спорные"
  if (disputes.length > 0) {
    writeDisputesSheet(ss, disputes);
  }

  // POST на бэкенд
  if (labels.length > 0) {
    postToBackend(labels);
  }

  Logger.log("Импорт завершён");
}

/**
 * Парсит блоки из Лист1
 * Возвращает массив {query, geo, items: [{domain, url, sentiment/unknownColor, row, col}]}
 */
function parseBlocks(values, backgrounds, lastRow, lastCol) {
  const blocks = [];
  let currentGeo = null;
  let blockStartRow = null;

  for (let row = 0; row < lastRow; row++) {
    const rowData = values[row];
    const rowBg = backgrounds[row];

    // Проверяем, разделитель ли это (гео)
    const geoMarker = findGeoMarker(rowData);
    if (geoMarker) {
      currentGeo = geoMarker;
      blockStartRow = row + 1;
      continue;
    }

    // Пропускаем пустые строки
    if (!rowData || !rowData.some(cell => cell && String(cell).trim())) {
      continue;
    }

    // Ищем заголовок блока (субъекты в колонках)
    // Заголовок = строка, где в соседних колонках "позиция" и "URL"
    const headerCols = findHeaderRow(rowData);
    if (headerCols && headerCols.length > 0) {
      // Это заголовок, парсим блок из этой строки
      const block = parseBlockFromHeader(
        values, backgrounds,
        row, headerCols,
        currentGeo || "Unknown", lastRow, lastCol
      );
      if (block) {
        blocks.push(block);
      }
    }
  }

  return blocks;
}

/**
 * Находит гео-маркер в строке
 */
function findGeoMarker(rowData) {
  for (const cell of rowData) {
    const text = String(cell).trim();
    for (const marker of GEO_MARKERS) {
      if (text === marker || text.toLowerCase() === marker.toLowerCase()) {
        return marker;
      }
    }
  }
  return null;
}

/**
 * Находит колонки заголовка (субъекты)
 * Ищет пары "позиция|URL" под субъектом
 */
function findHeaderRow(rowData) {
  const headerCols = [];
  for (let col = 0; col < rowData.length; col++) {
    const cell = String(rowData[col]).trim().toLowerCase();
    // Простая эвристика: если в ячейке есть имя (не пусто и не цифра и не URL)
    if (cell && !cell.match(/^\d+$/) && !cell.includes("http") && cell.length > 2) {
      headerCols.push(col);
    }
  }
  return headerCols.length > 0 ? headerCols : null;
}

/**
 * Парсит блок из заголовка
 */
function parseBlockFromHeader(values, backgrounds, headerRow, headerCols, geo, lastRow, lastCol) {
  if (!headerCols || headerCols.length === 0) {
    return null;
  }

  // query = первый субъект (нормализованный)
  const query = String(values[headerRow][headerCols[0]]).trim().toLowerCase();
  if (!query) {
    return null;
  }

  const items = [];

  // Пройдём по данным под заголовком, ищем позиции и URL
  for (let row = headerRow + 1; row < Math.min(headerRow + 50, lastRow); row++) {
    const rowData = values[row];
    const rowBg = backgrounds[row];

    // Если встретили новый гео-маркер или пустую строку, конец блока
    if (findGeoMarker(rowData) || !rowData.some(cell => cell && String(cell).trim())) {
      break;
    }

    // Ищем позиции под первым субъектом (headerCols[0])
    const posCol = headerCols[0];
    const urlCol = headerCols[0] + 1; // Предполагаем пару [позиция|URL]

    if (posCol >= rowData.length || urlCol >= rowData.length) {
      continue;
    }

    const position = String(rowData[posCol]).trim();
    const url = String(rowData[urlCol]).trim();

    // Если позиция — цифра и URL содержит точку, это валидная пара
    if (position.match(/^\d+$/) && url.includes(".")) {
      const bgColor = rowBg[posCol]; // Цвет ячейки с позицией
      const colorHex = bgColor.substring(1).toLowerCase(); // Убираем #, нижний регистр
      const sentiment = COLOR_MAP[colorHex];
      const unknownColor = sentiment ? null : colorHex;

      const domain = extractDomain(url);
      items.push({
        domain,
        url,
        sentiment: sentiment || null,
        unknownColor: unknownColor,
        row: row + 1, // 1-indexed для пользователя
        col: posCol + 1,
      });
    }
  }

  return items.length > 0 ? { query, geo, items } : null;
}

/**
 * Извлекает домен из URL (без www, lowercase)
 */
function extractDomain(url) {
  try {
    const parsed = new URL(url);
    let domain = parsed.hostname || url;
    domain = domain.replace(/^www\./, "");
    return domain.toLowerCase();
  } catch (e) {
    // Если URL невалиден, пытаемся парсить вручную
    const match = url.match(/(?:https?:\/\/)?(?:www\.)?([^\/]+)/);
    return match ? match[1].toLowerCase() : url.toLowerCase();
  }
}

/**
 * Пишет лист "Эталон разметки" с результатами
 */
function writeReferenceSheet(ss, labels) {
  let sheet = ss.getSheetByName("Эталон разметки");
  if (sheet) {
    ss.deleteSheet(sheet);
  }
  sheet = ss.insertSheet("Эталон разметки");

  // Заголовок
  sheet.appendRow(["domain", "query", "geo", "sentiment", "source"]);

  // Данные
  for (const label of labels) {
    sheet.appendRow([
      label.domain,
      label.query,
      label.geo,
      label.sentiment,
      label.source,
    ]);
  }

  Logger.log(`Лист "Эталон разметки" создан с ${labels.length} строками`);
}

/**
 * Пишет лист "Спорные" для ручного ревью
 */
function writeDisputesSheet(ss, disputes) {
  let sheet = ss.getSheetByName("Спорные");
  if (sheet) {
    ss.deleteSheet(sheet);
  }
  sheet = ss.insertSheet("Спорные");

  // Заголовок
  sheet.appendRow(["row", "col", "hex", "url", "geo", "query"]);

  // Данные
  for (const d of disputes) {
    sheet.appendRow([
      d.row,
      d.col,
      d.hex,
      d.url,
      d.geo,
      d.query,
    ]);
  }

  Logger.log(`Лист "Спорные" создан с ${disputes.length} строками`);
}

/**
 * Отправляет массив labels на бэкенд
 */
function postToBackend(labels) {
  const payload = JSON.stringify(labels);
  const options = {
    method: "post",
    contentType: "application/json",
    headers: {
      Authorization: `Bearer ${WEBHOOK_SECRET}`,
    },
    payload,
    muteHttpExceptions: true,
  };

  try {
    const response = UrlFetchApp.fetch(WEBHOOK_URL, options);
    const status = response.getResponseCode();
    const body = response.getContentText();

    Logger.log(`POST ${WEBHOOK_URL} → ${status}`);
    Logger.log(`Response: ${body}`);

    if (status === 200 || status === 201) {
      Logger.log("✅ Импорт успешен");
    } else {
      Logger.log(`⚠️ Ошибка: ${status}`);
    }
  } catch (e) {
    Logger.log(`❌ POST failed: ${e.toString()}`);
  }
}

/**
 * Меню в Google Sheets (инициализация)
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu("Импорт")
    .addItem("Импортировать разметку", "importManualLabels")
    .addToUi();
}
