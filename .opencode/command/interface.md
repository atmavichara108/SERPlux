
---
description: Google Sheets UI (Apps Script меню, лист Настройки). Web UI ⏸ ADR. Запускает ui-dev.
agent: ui-dev
---

# Реализация UI SERPlux в Google Sheets

## Контекст
!`cat docs/progress.md`
!`cat docs/techdebt.md`

## Задача
Реализуй UI SERPlux в Google Sheets по приоритетам:

### 1. Меню Apps Script
- Пункты: «Запустить сбор», «Настройки», «Статус», «История»
- Обработчики onClick для каждого пункта

### 2. Лист «Настройки»
- Параметры: client_id, depth, with_labels, label_mode, date
- Валидация ввода (выпадающие списки, проверки)
- Параметры читаются при запуске прогона

### 3. Запуск прогона из меню
- Чтение параметров из листа «Настройки»
- Вызов POST /run webhook (UrlFetchApp)
- Запись ID/статуса прогона в лист

### 4. Статус и история
- Лист «Статус»: текущий прогон (started_at, status, message)
- Лист «История»: таблица прогонов (дата, клиент, поисковик, статус)

## Требования
- Google Apps Script (apps_script.gs)
- Google Sheets (листы, валидация данных)
- Связь с backend через webhook (POST /run, GET /status)
- НЕ трогай core-модули
- Обнови docs/progress.md после завершения
