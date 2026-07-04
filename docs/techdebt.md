# Реестр технологического долга SERPlux

Правила: дата выявления, суть, на что влияет и что делать.
Дописывать сверху. Когда исправлено — перенести в конец с пометкой ✔.

---

## Высокий приоритет (влияет на мультиклиентность и корректность)

### 2026-07-02 — Провайдер LLM захардкожен в labeler.py

**Проблема:** `ZEN_MODEL`, `ZEN_ENDPOINT` захардкожены в labeler.py. Нет фолбек-цепочки,
нет мониторинга стоимости/качества, нет API для управления провайдерами.
Невозможно добавить новый провайдер без правки кода.

**Где:** `labeler.py:14-15` (ZEN_MODEL, ZEN_ENDPOINT), `labeler.py:43-67` (_call_zen)

**Что делать:** Рефакторинг labeler.py:
- Абстракция провайдера (класс/функция с единым интерфейсом).
- Фолбек-цепочка по priority из БД.
- Запись статистики в provider_stats.
- API /providers для CRUD.
- Миграция текущего Zen в профиль провайдера.

---

### 2026-07-02 — project_id зашит в .env, не передаётся через API

**Проблема:** Webhook читает `project_id` из `TOPVISOR_PROJECT_ID` в `.env`. Тело `/run` не принимает `project_id` или `client_id`. Для каждого нового клиента нужно править `.env` и перезапускать контейнер. Невозможно запустить прогон для разных клиентов через один инстанс.

**Где:** `topvisor.py` → `_get_credentials()` / `TOPVISOR_PROJECT_ID`; `webhook.py` → `RunRequest`

**Что делать:** Вынести `project_id` / `client_id` в тело запроса `/run`. При мультиклиентности — читать из профиля клиента в БД.

---

### 2026-07-02 — date не принимается в теле /run

**Проблема:** Дата сбора всегда `today` (`main.py` → `config.get("date")` → fallback `datetime.today()`). Нельзя собрать выдачу за конкретную прошлую дату через API.

**Где:** `main.py:14` (`DEFAULT_CONFIG`), `webhook.py:35` (`RunRequest`), `collector.py`

**Что делать:** Добавить поле `date` в `RunRequest`. Потребует доработки `collector.py` (передача `date` в Topvisor). **Пометка:** проверить, принимает ли `run()` дату из config, и поддерживает ли Topvisor Snapshots API запрос за произвольную дату.

---

### 2026-07-02 — regions_map и project_id рассинхронизированы по источникам

**Проблема:** `regions_map` передаётся в теле запроса, `project_id` — из `.env`. Можно собрать данные проекта №1 с картой регионов от клиента №2. Нет единого источника конфигурации для прогона.

**Где:** `webhook.py:75-79`, `run.py`

**Что делать:** Свести все параметры клиента в один источник — профиль клиента (таблица `clients` в SQLite или JSON-файл). При передаче `client_id` в запросе — все остальные параметры подтягиваются из профиля.

---

## Средний приоритет (качество и UX)

### 2026-07-04 — /status не отдаёт stats (provider_used, collected, cost_estimate)

**Проблема:** UI (apps_script.gs → `checkStatus()`) ожидает `stats` в ответе GET /status
(collected, saved_new, labeled, exported, provider_used, cost_estimate, fallback_triggered).
Фактический `_last_run` в webhook.py содержит только `{started_at, finished_at, status, message, client_id}`.
UI работает через defensive-доступ (`stats?.provider_used ?? '—'`), но статистика не отображается.

**Где:** `webhook.py:37-43` (_last_run), `webhook.py:95-143` (_run_pipeline)

**Статус:** `finished_at` и `client_id` реализованы (2026-07-04). Остальные поля stats отложены.

**Что делать:** Расширить `_last_run` полем `stats: dict`. В `_run_pipeline()` собирать
статистику из результатов `save()`, `labeler.label()`, `exporter.export()` и записывать
в `_last_run["stats"]`. Записать `provider_used` из labeler (какой провайдер фактически
использовался). Требует рефакторинга `_run_pipeline()` для возврата статистики из `main.run()`.

---

### 2026-07-04 — CRUD /providers не реализован (только read-only GET)

**Проблема:** UI (apps_script.gs → `manageProviders()`) показывает список провайдеров
из GET /providers, но кнопки управления (добавить, вкл/выкл, приоритет) — заглушки.
POST/PUT/DELETE /providers не реализованы (ADR 2026-07-03: провайдеры в config.py).
При 2+ провайдерах потребуется управление через API, а не правка config.py.

**Где:** `webhook.py` (нет POST/PUT/DELETE /providers), `config.py` (PROVIDERS)

**Что делать:** При появлении второго провайдера:
1. Перенести PROVIDERS в SQLite (таблица `providers`)
2. Реализовать POST/PUT/DELETE /providers в webhook.py
3. Убрать заглушки в apps_script.gs → manageProviders()

---

### 2026-07-02 — date, force_rebuild_report, provider_chain не принимаются в /run

**Проблема:** docs/ui-spec.md §5.2 описывает целевой контракт `/run` с полями `date`,
`force_rebuild_report`, `provider_chain`. Фактический webhook.py принимает
`client_id`, `label_mode`, `force_relabel`, `report_only`, `report_date` (реализовано),
но НЕ принимает `date`, `force_rebuild_report`, `provider_chain`. apps_script.gs читает
эти ключи из листа «Настройки», но сервер их проигнорирует.

**Где:** `webhook.py:40-58` (RunRequest), `apps_script.gs` → `runCollection()` (payload)

**Статус:** `report_only` и `report_date` реализованы (2026-07-04).
Остальные поля (`date`, `force_rebuild_report`, `provider_chain`) отложены.

**Что делать:** Расширить `RunRequest` полями `date`, `force_rebuild_report`,
`provider_chain`. Требует доработки collector.py (передача date), reporter.py
(force_rebuild_report), labeler.py (provider_chain).

---

### 2026-07-02 — Качество разметки DeepSeek низкое

**Проблема:** Используется бесплатная модель DeepSeek v4 Flash Free + промпт без few-shot примеров и контекста. Качество разметки тональности может быть нестабильным — neutral/positive/negative определяются без референсов.

**Где:** `labeler.py`

**Что делать:** Прокачка разметки:
- Few-shot примеры в промпте (по 2–3 эталона на класс).
- Референс-списки доменов для режима `domains`.
- Трёхэтапная модель достоверности (когда будет реализована: списки → сниппеты → заход).
- Память/обучение — пока неактуально (нет объёма).

---

### 2026-07-02 — _parse_label: мусорный ответ LLM → neutral, а не None

**Проблема:** Если LLM вернула неразборчивый ответ, парсер проставляет `neutral` вместо `None`. Создаёт ложные жёлтые метки — заказчик видит «нейтрально» там, где разметка на самом деле провалилась.

**Где:** `labeler.py` → `_parse_label()`

**Что делать:** Мусорный ответ (не positive/negative/neutral) → `None` (честнее). Разбирать вместе с прокачкой нейронки, чтобы не менять контракт дважды.

---

### 2026-07-02 — Логирование «Запущена проверка проектов: []» вводит в заблуждение

**Проблема:** Когда `run_check` фильтрует только те регионы, которых ещё нет в снимке, список переданных `region_indexes` может быть пустым — лог пишет `[]`. Звучит как ошибка, хотя проверка уже стартовала или уже идёт.

**Где:** `topvisor.py` → `run_check()`

**Что делать:** Улучшить формулировки:
- Если список непустой: `"Запущена проверка региона(ов): %s"`.
- Если список пустой: `"Проверка не требуется: все регионы актуальны"`.
- Различать «уже идёт» / «стартовала, id получим позже».

---

## Низкий приоритет (безопасность / чистота)

### 2026-07-04 — Поимённый COPY в Dockerfile хрупок

**Проблема:** Блок `COPY --chown=serplux:serplux` в Dockerfile перечисляет модули поимённо (main.py, topvisor.py, ..., migrate.py). При добавлении нового .py-модуля его нужно не забыть добавить в список — иначе модуль не попадёт в образ, ошибка обнаружится только в рантайме. migrate.py был забыт и добавлен отдельным фиксом.

**Где:** `Dockerfile:35-46` (блок COPY с перечислением файлов)

**Что делать:** Рассмотреть переход на `COPY --chown=serplux:serplux *.py ./` (копировать все .py-файлы). Учесть: .dockerignore должен исключать тесты (tests/), venv, __pycache__. Альтернатива — оставить поимённый список, но добавить CI-проверку (сравнение списка .py в корне со списком в Dockerfile).

---

### 2026-07-03 — Deprecation warning httpx/starlette в тестах

**Проблема:** `test_webhook.py` выдаёт `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`. Не влияет на функциональность, но засоряет вывод pytest и может стать загадкой при обновлении зависимостей.

**Где:** `tests/test_webhook.py` (через `fastapi.testclient`), `requirements.txt` / `requirements-dev.txt`

**Что делать:** Обновить `httpx` до `httpx2` (или обновить `starlette`/`fastapi` до версии, где warning убран). Проверить совместимость с `TestClient`.

---

### 2026-07-02 — Валидация тела /run происходит до проверки авторизации

**Проблема:** FastAPI валидирует тело запроса (через Pydantic `RunRequest`) до вызова хендлера, где проверяется `Authorization`. Пустое тело даёт 422 Unprocessable Entity со структурой ожидаемых полей до проверки токена — утечка информации о форме запроса.

**Где:** `webhook.py:35-39` (`RunRequest`), `webhook.py:111-146` (`trigger_run`)

**Что делать:** Поменять порядок: сначала `_verify_token`, потом парсинг тела. FastAPI dependency injection с `Depends` для авторизации — стандартный подход.

---

### 2026-07-02 — Мёртвая Cloud-DNS-зона в Hetzner

**Проблема:** Осталась неактивная DNS-зона в Hetzner от ранней попытки настройки (домен делегирован на konsoleH). Не мешает работе, но создаёт шум.

**Где:** Hetzner DNS console

**Что делать:** Удалить мёртвую зону вручную через Hetzner UI.

---

### 2026-07-02 — /health отдаёт имя сервиса ботам-сканерам

**Проблема:** `/health` возвращает `{"status": "ok", "service": "serplux-webhook"}` — имя сервиса видно любому сканеру интернета.

**Где:** `webhook.py:99-101`

**Что делать:** Убрать `service` или заменить на неинформативное `{"status": "ok"}`.

---

### 2026-07-02 — Gemini-код полностью выпилен? (проверка)

**Статус:** ✔ Выпилен. `grep -r 'generativeai\|gemini'` по `.py` не дал результатов. Единственное оставшееся упоминание — в `docs/decisions.md` (historical ADR, удалять не нужно). В `requirements.txt` и `requirements-dev.txt` зависимостей google-generativeai нет.

---

## Исправлено

(пусто)
