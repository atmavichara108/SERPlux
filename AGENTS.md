
# AGENTS.md — правила проекта SERPlux

## Что это
Сбор поисковой выдачи (Google/Яндекс) через topvisor Snapshots API,
разметка URL по тональности, выгрузка в Google Sheets с версионированием.

## Стек (не менять без явного указания)
- Python 3.11+
- requests (topvisor API), gspread (Sheets), FastAPI (webhook)
- google-generativeai (Gemini Flash для разметки)
- SQLite для кэша и истории
- Никаких тяжёлых фреймворков. Если хочешь добавить зависимость — спроси.

## Архитектура: модули и контракты (СТРОГО соблюдать)
Каждый модуль работает по контракту. Не лезь в чужой модуль.

- topvisor.py  -> run_check(config) запускает проверку+снимок, poll_status(),
                  get_snapshot() -> list[Row]
- collector.py -> collect(config) -> list[Row] по всем связкам, с обработкой сбоев
- labeler.py   -> label(rows, mode) -> rows c полем label; сначала кэш, потом LLM
- storage.py   -> save(rows), get_cached_label(url), get_history()
- exporter.py  -> export(rows) пишет в Sheets с цветовой разметкой
- webhook.py   -> FastAPI endpoint, триггерится кнопкой из Sheets
- config.py    -> читает настройки из листа "Настройки" Google Sheet

Row = dict: {date, searcher, query, geo, position, url, domain, label}

## Секреты
- ВСЕ ключи (topvisor API, Google service account, Gemini) только в .env
- .env в .gitignore. Никогда не коммить ключи. Никогда не печатай ключи в логи.
- В репо лежит .env.example с пустыми плейсхолдерами.

## Принципы
- Сначала вертикальный срез (1 запрос, 1 гео, Google, без LLM), потом расширение.
- Идемпотентность: повторный запуск не должен ломать данные или дублировать.
- Частичный сбой = логируем и продолжаем, не падаем целиком.
- Логирование через стандартный logging, не print, в финальном коде.
- Каждый модуль с примером запуска в __main__ для изоляции при отладке.
- После значимого изменения: обнови docs/progress.md (статус) и
  docs/decisions.md (если принято архитектурное решение). Кратко, без воды.

## Чего НЕ делать
- Не парсить Google/Яндекс напрямую. Источник только topvisor.
- Не строить отдельный веб-фронт. Интерфейс = Google Sheets.
- Не реализовывать "расширенный" LLM-режим на старте. Только дешёвый Gemini Flash.
