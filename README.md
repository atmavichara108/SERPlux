# SERPlux v1.0

Автоматический сбор поисковой выдачи Google и Яндекс по запросам и гео
через topvisor Snapshots API, разметка URL по тональности
(позитив / негатив / нейтрал) и выгрузка в Google Sheets с версионированием.

**Версия:** 1.0.0 (2026-07-11)  
**Тесты:** 262/262 passed
**Release notes:** [docs/release-1.0.md](docs/release-1.0.md)  
**Roadmap v2.0:** [docs/roadmap-2.0.md](docs/roadmap-2.0.md)

## Что делает

- Собирает ТОП выдачи (10/20/50/100) по связкам: запрос × гео × поисковая система
- Источник — topvisor (Google, Яндекс.ру, Яндекс.com)
- Размечает URL по тональности через DeepSeek (opencode.ai/zen) с кэшем domain_labels
- Пишет результат в Google Sheets: плоский лист «Данные» (кэш) + матрица-отчёт «Отчёт» (накопительный, 10 версий)
- Запуск в один клик из самой таблицы через меню Google Sheets
- **Мультиклиентность:** профиль клиента в БД, переключение через client_id
- **Эталон разметки:** парсер Лист1 → «Эталон разметки» → импорт в БД (source=manual_l1)

## Интерфейс

Управление и результат — внутри Google Sheets:

- Меню **SERPlux** (добавляется через Google Apps Script):
  - **Запустить сбор** — отправляет запрос на сервер, прогон идёт в фоне
  - **Проверить статус** — показывает статус последнего прогона
  - **Установить секрет** — сохраняет WEBHOOK_SECRET в Script Properties
- Лист **Данные**: Дата | ПС | Запрос | Гео | Позиция | URL | Домен | Сниппет | Метка
- Лист **Отчёт**: матрица позиций по субъектам × гео × дата, с цветовой разметкой

## Архитектура

```
topvisor.py   — сбор снимков через topvisor Snapshots API
collector.py  — оркестрация по всем связкам searcher × geo
labeler.py    — разметка тональности (DeepSeek/Zen, кэш в SQLite)
storage.py    — SQLite: хранение результатов и кэш меток
exporter.py   — выгрузка в лист "Данные" Google Sheets
reporter.py   — построение матрицы-отчёта в лист "Отчёт"
webhook.py    — FastAPI сервис, принимает запросы из Sheets
main.py       — точка входа, полный пайплайн
```

Подробнее — `AGENTS.md`, `docs/contracts.md`, `docs/decisions.md`. Единственная
обычная входная команда для feature/fix — `/release`; пользователь обычно даёт
одно свободное описание с версией, а workflow сам задаёт вопросы и создаёт
authoritative spec в `docs/specs/`. SERPlux workflow не зависит от Vault.

### Экспериментальный `/prompt`

`/prompt <сырой запрос>` — optional read-only diagnostic helper, обычно не нужен:
тот же слой нормализации работает внутри `/release`. Он выдаёт brief с Goal,
контекстом, ограничениями, DoD, рисками, roster и evidence.
Предположения помечаются `[HYPOTHESIS]`, неизвестное — `[UNKNOWN]`. Команда не
редактирует код, не вызывает dispatch и не объявляет задачу выполненной; brief не
является acceptance evidence. Это ограниченный design-only эксперимент, не
глобальный runtime `prompt-engineer`/`task-compiler`; при неясном маршруте или
spec результатом являются `UNROUTABLE`/`BLOCKED` без generic fallback.

`/spec [selector]` только читает/перечисляет existing local specs и не создаёт их.
`/release` сам создаёт один deterministic authoritative spec в `docs/specs/`; не
требуется предварительный `/spec` или `/prompt`.

### Release workflow OpenCode

Для следующей оплаченной feature/fix используйте:

```text
/release v1.0.2: реализовать <feature/fix>
```

Команда ведёт intake/questions → local spec + plan → explicit plan approval →
build → targeted tests → reviewer → verifier с максимум 5 fix-итерациями при FAIL
→ `READY_FOR_USER_INTEGRATION`/`AWAITING_USER_REVIEW`. Verifier PASS означает
acceptance gate по DoD, но не production readiness и не серверную проверку.

После handoff пользователь вручную запускает `/commit`, затем push workflow и
`/deploy`, после чего выполняет server check. `/release` не выполняет commit,
tag, push, deploy или `/dream`/flush автоматически и не требует дополнительной
фразы или approval для commit/push. Flush остаётся отдельным ручным действием
через `/dream` или documented equivalent.

## Установка и деплой

### Требования

- Docker + Docker Compose
- Google service account с доступом к таблице
- Аккаунт topvisor с проектом и Snapshots API
- Аккаунт opencode.ai (для OPENCODE_API_KEY)

### Тесты перед деплоем

```bash
pip install -r requirements-dev.txt
pytest
```

Все 262 теста должны пройти. Тесты не обращаются к внешним API и не пишут в боевую БД/таблицу.

### Шаги

```bash
# 1. Клонировать репозиторий
git clone <repo-url> && cd serp

# 2. Прогнать тесты
pip install -r requirements-dev.txt && pytest

# 3. Создать .env из шаблона и заполнить все переменные
cp .env.example .env

# 4. Положить credentials.json (Google service account) рядом с docker-compose.yml

# 5. Запустить контейнер
docker compose up -d

# 5. Проверить что сервис поднялся
curl http://localhost:8000/health
```

### Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| `TOPVISOR_API_KEY` | API-ключ topvisor |
| `TOPVISOR_USER_ID` | ID пользователя topvisor |
| `TOPVISOR_PROJECT_ID` | ID проекта topvisor |
| `OPENCODE_API_KEY` | Ключ opencode.ai для DeepSeek/Zen |
| `GOOGLE_SHEET_ID` | ID Google Sheets таблицы |
| `GOOGLE_CREDENTIALS_PATH` | Путь к credentials.json (в контейнере: `credentials.json`) |
| `WEBHOOK_SECRET` | Bearer-токен для защиты webhook |
| `DB_PATH` | Путь к SQLite БД (в контейнере: `/app/data/serplux.db`) |
| `REGIONS_MAP` | Файл карты регионов (по умолчанию: `regions_map.json`) |

### Подключение кнопки в Google Sheets

1. Откройте таблицу → **Расширения → Apps Script**
2. Вставьте содержимое `apps_script.gs`
3. Укажите `WEBHOOK_URL` в начале скрипта (URL вашего сервера)
4. Сохраните, запустите `onOpen()` вручную один раз
5. В таблице появится меню **SERPlux → Установить секрет** — введите `WEBHOOK_SECRET`

### Добавление нового клиента

Мультиклиентность реализована через профиль клиента в БД (таблица `clients`).
Каждый клиент имеет свой набор субъектов (queries), гео (regions_map), поисковых систем (searchers).
Переключение через `client_id` на листе «Настройки» или в теле запроса к `/run`.
CRUD клиентов: API `/clients` (GET/POST/PUT).

**Инструкция для заказчика:** [docs/onboarding-client.md](docs/onboarding-client.md)

## API webhook

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/health` | Health-check (без авторизации) |
| `GET` | `/status` | Статус последнего прогона |
| `POST` | `/run` | Запустить пайплайн (сбор/разметка/экспорт/отчёт) |
| `GET` | `/clients` | Список клиентов |
| `POST` | `/clients` | Создать клиента |
| `GET` | `/clients/{client_id}` | Профиль клиента |
| `PUT` | `/clients/{client_id}` | Обновить профиль клиента |
| `GET` | `/clients/{client_id}/dates` | Список дат с данными |
| `GET` | `/topvisor/regions` | Регионы Topvisor |
| `GET` | `/providers` | Список LLM-провайдеров (read-only) |
| `POST` | `/labels/import` | Импорт эталонной разметки |

Авторизация: `Authorization: Bearer <WEBHOOK_SECRET>` (кроме `/health`)

Тело `POST /run` (JSON, все поля опциональны):
```json
{
  "client_id": "default",
  "depth": 10,
  "with_labels": true,
  "label_mode": "auto",
  "date": "2026-07-11",
  "report_only": false,
  "label_only": false
}
```

## Источник данных

topvisor Snapshots API: сбор ТОПа выдачи. Сбор асинхронный:
запуск проверки → поллинг готовности → получение снимка.
Лимиты — по тарифу аккаунта topvisor.

## Разметка по тональности

DeepSeek v4 Flash Free через opencode.ai/zen — дешёвый и быстрый.
Кэш по тройке (полный URL + query + geo): повторно встреченные URL не гоняются через LLM.
URL канонизируется (lowercase scheme/host, убирается trailing slash/fragment) перед поиском в кэше.
Режимы: `auto` (справочник → LLM → neutral) и `deep` (заглушка для v2).

## Документация

- **CANON.md:** источник истины по геометрии Лист1 и контракту эталона
- **release-1.0.md:** release notes v1.0 (что вошло, известные ограничения)
- **roadmap-2.0.md:** roadmap v2.0 (7 кандидатов)
- **contracts.md:** контракты модулей (сигнатуры, типы)
- **decisions.md:** лог архитектурных решений (ADR)
- **user-guide.md:** руководство пользователя
- **verification.md:** автоматизированная и ручная проверка

## Лицензия

Proprietary. Все права защищены.
