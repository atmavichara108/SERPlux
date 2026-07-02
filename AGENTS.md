
# AGENTS.md — правила проекта SERPlux

## Что это
Сбор поисковой выдачи (Google/Яндекс) через Topvisor Snapshots API,
разметка URL по тональности, выгрузка в Google Sheets с версионированием.
**Интерфейс:** Google Sheets (Apps Script меню + лист «Настройки»).

## Стек (не менять без явного указания)
- Python 3.11+
- requests (Topvisor API), gspread (Sheets), FastAPI (webhook API)
- DeepSeek через opencode.ai/zen API (разметка тональности, OPENCODE_API_KEY)
- SQLite для кэша, истории и профилей
- Docker + docker-compose для деплоя
- Никаких тяжёлых фреймворков. Если хочешь добавить зависимость — спроси.

## Архитектура: модули и контракты (СТРОГО соблюдать)
Каждый модуль работает по контракту. Не лезь в чужой модуль.

- topvisor.py  → run_check(config) запускает проверку+снимок, poll_status(),
                  get_snapshot() → list[Row]
- collector.py → collect(config) → list[Row] по всем связкам, с обработкой сбоев
- labeler.py   → label(rows, mode) → rows c полем label; сначала кэш, потом LLM
- storage.py   → save(rows), get_cached_label(url), get_history()
- exporter.py  → export(rows) пишет в Sheets с цветовой разметкой
- reporter.py  → строит матрицу-отчёт в Google Sheets
- webhook.py   → FastAPI: API-эндпоинты для Apps Script
- config.py    → читает настройки из листа "Настройки" Google Sheet
- main.py      → точка входа пайплайна: collect → save → label → export

Row = dict: {date, searcher, query, geo, region_index, position, url, domain, snippet, label}

## Секреты
- ВСЕ ключи (Topvisor API, Google service account, OPENCODE_API_KEY, WEBHOOK_SECRET) только в .env
- .env в .gitignore. Никогда не коммить ключи. Никогда не печатай ключи в логи.
- В репо лежит .env.example с пустыми плейсхолдерами.

## Принципы
- Сначала вертикальный срез (1 запрос, 1 гео, Google, без LLM), потом расширение.
- Идемпотентность: повторный запуск не должен ломать данные или дублировать.
- Частичный сбой = логируем и продолжаем, не падаем целиком.
- Логирование через стандартный logging, не print, в финальном коде.
- Каждый модуль с примером запуска в __main__ для изоляции отладки.
- После значимого изменения: обнови docs/progress.md (статус) и
  docs/decisions.md (если принято архитектурное решение). Кратко, без воды.

## Агенты и команды

> Агенты определены в `.opencode/agents/*.md`, команды в `.opencode/command/*.md`.
> Auto-discovery по имени файла (без .md).

### Агенты

| Агент | Mode | Модель | Назначение | edit |
|-------|------|--------|-----------|------|
| **build** | primary | claude-sonnet-4-6 | Основная разработка | allow |
| **plan** | primary | claude-sonnet-4-6 | Планирование, анализ | deny |
| **collector-dev** | subagent | claude-sonnet-4-6 | Topvisor API + сбор данных | allow |
| **reviewer** | subagent | gpt-5.3-codex | PASS/FAIL верификация | deny |
| **ui-dev** | subagent | claude-sonnet-4-6 | Веб-интерфейс (приостановлено — требуется ADR) | allow |
| **infra-dev** | subagent | deepseek-v4-flash-free | Docker, deploy, серверная инфра | allow |

### Команды-пайплайны

| Команда | Агент | Что делает |
|---------|-------|-----------|
| `/interface` | ui-dev | Веб-интерфейс (приостановлено — требуется ADR, см. docs/ui-spec.md Q20) |
| `/container` | infra-dev | Создать/обновить Dockerfile + docker-compose |
| `/deploy` | infra-dev | Развернуть на сервере: проверка, обновление, proxy, SSL |

### Как вызывать

- **Через Tab** — переключение между primary-агентами (build, plan)
- **Через @** — вызов subagent'а вручную: `@ui-dev сделай дашборд`
- **Через команду** — `/interface` запустит ui-dev с готовым промптом
- **Автоматически** — build-агент может делегировать задачи subagent'ам через `task`

### Правила для subagent'ов

- **collector-dev**: ТОЛЬКО topvisor.py и collector.py. docs/contracts.md, docs/topvisor-api.md.
- **reviewer**: edit: deny. Проверяет контракты, утечки ключей, идемпотентность.
- **ui-dev**: НЕ трогать core-модули (.py кроме webhook.py). docs/ui-spec.md.
- **infra-dev**: НЕ трогать код приложения (.py). Dockerfile, docker-compose, сервер.

## Язык

- Все ответы пользователю — на русском.
- Рассуждения (thinking/reasoning) — тоже на русском.
- Код, имена переменных, технические идентификаторы, команды — как есть
  (английский), не переводить.
- Комментарии в коде — на русском.
- Сообщения коммитов — на английском (стандарт), кратко.

## Чего НЕ делать
- Не парсить Google/Яндекс напрямую. Источник только Topvisor.
- Не строить SPA-фреймворки (React, Vue, Angular). Только Jinja2 + Tailwind + Vanilla JS.
- Не реализовывать "расширенный" LLM-режим. Только дешёвый DeepSeek через Zen.
- Не хардкодить секреты, ключи, токены — только через .env.

## Документация проекта

| Файл | Назначение |
|------|-----------|
| docs/contracts.md | Контракты модулей (сигнатуры, типы) |
| docs/decisions.md | Реестр архитектурных решений (ADR) |
| docs/progress.md | Прогресс разработки (обновлять после каждой сессии) |
| docs/techdebt.md | Реестр технологического долга |
| docs/ui-spec.md | UI-спецификация: параметры, API, Sheets-меню, мультиклиентность |
| docs/topvisor-api.md | Механика Topvisor Snapshots API |
