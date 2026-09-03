
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

**Структура проекта: FLAT layout.** Все модули в корне репозитория. Каталога `src/` НЕТ и не будет. Не искать код в `src/`.

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

## Принципы и operationalные требования
- Сначала вертикальный срез (1 запрос, 1 гео, Google, без LLM), потом расширение.
- Идемпотентность: повторный запуск не должен ломать данные или дублировать.
- Частичный сбой = логируем и продолжаем, не падаем целиком.
- Логирование через стандартный logging, не print, в финальном коде.
- Каждый модуль с примером запуска в `__main__` для изоляции отладки.
- После значимого изменения: обнови `docs/progress.md` (статус) и
  `docs/decisions.md` (если принято архитектурное решение). Кратко, без воды.
- **Flush-протокол (память):** перед компакцией сессии ключевые решения и
  выводы дописывай в `docs/decisions.md`.
- Устойчивое развитие.

## Команды разработки

| Команда | Описание |
|---------|----------|
| `python -m pytest -v` | Запустить все 224 теста локально (требует `pip install -r requirements-dev.txt`) |
| `python -m pytest -k <pattern> -v` | Запустить тесты по паттерну (e.g., `test_collect` или `test_labeler`) |
| `python <module>.py` | Запустить модуль с примером в `__main__` для отладки |
| `docker compose up -d` | Поднять контейнер webhook (требует `.env` и `credentials.json`) |
| `./verify.sh` | Проверка после deploy: тесты, health, логи, БД, целостность данных (в контейнере) |
| `./backup_db.sh` | Бэкап SQLite БД (создаёт `serplux.db.bak.<timestamp>`) |
| `bash -n <script>.sh` | Синтаксис-проверка bash-скрипта |

## Gotchas и инварианты

- **pytest в контейнере:** требует requirements-dev.txt, иначе "pytest not found". Dockerfile копирует оба файла.
- **DB инициализация:** storage.py::_ensure_db() должна вызваться ДО первой записи. main.py делает это явно.
- **Google Sheets webhook:** требует `WEBHOOK_SECRET` в .env, иначе 401 на `/run`. Скрипт Apps Script сохраняет secret в Script Properties при `/install`.
- **Ключи в .env:** ВСЕ (Topvisor, Google service account, OPENCODE_API_KEY, WEBHOOK_SECRET) только через .env, никогда не хардкодь. .env в .gitignore.
- **Топвизор лимиты:** асинхронная сборка может быть медленной (поллинг) или упасть по лимиту тарифа. Смотри docs/topvisor-api.md.
- **Аккумулятивный отчёт:** reporter.py вставляет новые версии СВЕРХУ, старые сдвигаются вниз. Максимум 10 версий, старейшие обрезаются. Лист "Отчёт" никогда не очищается.
- **Кэш разметки:** labeler.py кэширует по (url, query, geo), где url — полный канонизированный URL из `storage.normalize_url()`. Один и тот же URL может быть виден в разных searcher'ах/гео — кэш переиспользуется.

## Агенты и команды

Обычная единственная входная команда — `/release vX.Y.Z: <описание>`. Она сама
нормализует запрос, задаёт вопросы и создаёт ровно один authoritative local spec
в `docs/specs/`; отдельные `/prompt` и `/spec` не обязательны. Это approved
exception к общей Vault policy: SERPlux execution workflow полностью локален и
не зависит от Vault. `/spec` только читает/перечисляет existing local specs,
`/prompt` — optional read-only diagnostic helper.

> Агенты определены в `.opencode/agents/*.md`, команды в `.opencode/command/*.md`.
> Auto-discovery по имени файла (без .md).

### Агенты

| Агент | Mode | Модель | Назначение | edit |
|-------|------|--------|-----------|------|
| **build** | primary | opencode-go/gpt-5.6-luna | Основная разработка | allow |
| **plan** | primary | opencode-go/gpt-5.6-luna | Планирование, анализ | deny |
| **collector-dev** | subagent | opencode-go/gpt-5.6-luna | Topvisor API + сбор данных | allow |
| **reviewer** | subagent | opencode-go/deepseek-v4-flash | PASS/FAIL верификация | deny |
| **verifier** | subagent | opencode-go/deepseek-v4-flash | Acceptance-only PASS/FAIL по DoD | deny |
| **ui-dev** | subagent | opencode-go/gpt-5.6-luna | Google Sheets UI (Apps Script) | allow |
| **infra-dev** | subagent | opencode-go/qwen3.7-plus | Docker, deploy, серверная инфра | allow |

### Команды-пайплайны

| Команда | Агент | Что делает |
|---------|-------|-----------|
| `/interface` | ui-dev | Google Sheets UI (Apps Script меню, лист Настройки). Web UI ⏸ ADR |
| `/container` | infra-dev | Создать/обновить Dockerfile + docker-compose |
| `/deploy` | infra-dev | Развернуть на сервере: проверка, обновление, proxy, SSL |
| `/prompt` | plan | Экспериментальная локальная нормализация сырого запроса в execution brief; без edit/dispatch |
| `/spec` | plan | Прочитать authoritative local spec из `docs/specs/`; без Vault fallback |
| `/release` | plan | Единственный обычный feature/fix workflow: intake/questions → generated local spec/plan approval → build → tests → reviewer → verifier/fix loop (максимум 5) → ручной handoff |

### Как вызывать

- **Через Tab** — переключение между primary-агентами (build, plan)
- **Через @** — вызов subagent'а вручную: `@ui-dev сделай дашборд`
- **Через команду** — `/interface` запустит ui-dev с готовым промптом
- **Через команду** — `/prompt <сырой запрос>` опционально выдаст read-only diagnostic brief; обычно его не вызывают
- **Через команду** — `/spec [selector]` прочитает/перечислит existing local specs; `/release` его заранее не требует
- **Через команду** — `/release v1.0.2: <свободное описание>` запустит весь интерактивный workflow
- **Автоматически** — build-агент может делегировать задачи subagent'ам через `task`

### Обязательный release workflow

`/release` выполняет intake/questions → generated local spec + plan → explicit
plan approval → build → targeted tests → отдельные reviewer и verifier → максимум
5 fix-итераций. После verifier PASS он показывает evidence и останавливается в
`READY_FOR_USER_INTEGRATION`/`AWAITING_USER_REVIEW`; flush отложен. Verifier PASS
означает acceptance gate по DoD, но не production readiness и не серверную
проверку.

`/release` не запускает автоматически `/dream`, flush, commit, tag, push или
deploy и не добавляет approval gate для commit/push. После handoff пользователь
сам запускает существующий `/commit`, доступный push workflow и `/deploy`, затем
проверяет сервер. Эти команды и pre-commit/commit-guard применяют свои текущие
проверки. Flush выполняется отдельно вручную через `/dream` или documented
equivalent после решения пользователя. Pre-existing dirty changes остаются под
scope safety и не должны затрагиваться или попадать в ручной commit.

### Экспериментальный `/prompt`

`/prompt` — optional read-only diagnostic helper; обычно пользователь даёт одно
описание прямо в `/release`. Он не создаёт global runtime и не запускает dispatch.
`/release` создаёт local spec сам; неясный scope или маршрут — `BLOCKED`/
`UNROUTABLE`, без `general` fallback.

Каноническая design note Vault: [`capability-routing-design-note`](file:///home/rudra/Projects/OpenCode-Vault/06-Audits/2026-08-25-capability-routing-design-note.md).

### Правило немедленного делегирования

Когда пользователь явно просит выполнить действие через другого агента
(«делегируй build», «передай ui-dev», «пусть infra-dev сделает», команда
вида `/interface`, `/container`, `/deploy` и т.п.) — plan-агент НЕ должен
размышлять, задавать лишние вопросы или повторно анализировать уже
известные файлы. plan-агент ДОЛЖЕН:

1. Подхватить инструкцию немедленно.
2. Сформировать чёткое ТЗ для целевого агента (что сделать, какие файлы
   затронуть, какие контракты соблюсти, как верифицировать).
3. Вызвать `task` с `subagent_type="<целевой агент>"` и подробным промптом.
4. Вернуть пользователю краткий отчёт: что делегировано, какой task_id.

Исключение: если ТЗ действительно неоднозначно (отсутствует целевой агент,
противоречивые требования, неизвестные файлы) — задать ОДИН конкретный
вопрос и продолжить после ответа. Не задавать вопросы ради вопросов.

### Правила для subagent'ов

- **collector-dev**: ТОЛЬКО topvisor.py и collector.py. Обязательно: docs/contracts.md, docs/topvisor-api.md.
- **reviewer**: edit: deny. Проверяет контракты, утечки ключей, идемпотентность.
- **ui-dev**: НЕ трогать core-модули (.py кроме webhook.py). docs/ui-spec.md, apps_script.gs.
- **infra-dev**: НЕ трогать код приложения (.py). Dockerfile, docker-compose, deploy.sh, verify.sh, сервер.

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
- Не расходовать впустую токены, когда можно обращаться к докам Волта за актуальной информацией, если не знаешь.

## Документация проекта

| Файл | Назначение |
|------|-----------|
| docs/contracts.md | Контракты модулей (сигнатуры, типы) |
| docs/decisions.md | Реестр архитектурных решений (ADR) |
| docs/progress.md | Прогресс разработки (обновлять после каждой сессии) |
| docs/techdebt.md | Реестр технологического долга |
| docs/ui-spec.md | UI-спецификация: параметры, API, Sheets-меню, мультиклиентность |
| docs/topvisor-api.md | Механика Topvisor Snapshots API |
