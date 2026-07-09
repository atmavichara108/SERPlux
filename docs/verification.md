# Верификация SERPlux

Граница между автоматизированной и ручной проверкой при разработке и деплое.

## Автоматизированные проверки

### GitHub Actions CI (на push и pull_request)

**Файл:** `.github/workflows/ci.yml`

**Что проверяет:**
- `pytest -v` — все unit/integration тесты (172 тестов в Этапе 0)
- Python 3.11 окружение
- зависимости из `requirements.txt` + `requirements-dev.txt`

**Результат:** ✓ или ✗ в GitHub UI (блокирует merge, если тесты упали)

**Не проверяет:** деплой, доступ к серверу, боевые данные

---

### verify.sh (на сервере после deploy.sh)

**Файл:** `verify.sh`

**Как запустить:**
```bash
cd /root/serp
./verify.sh [SERVICE=serplux]
```

**Важно:** `pytest` устанавливается в Docker-образ через `requirements-dev.txt`. Если verify.sh
падает с `No module named pytest`, нужно выполнить `docker compose build` (или `./deploy.sh`)
после пула, чтобы пересобрать образ с новыми зависимостями.

**Что проверяет (6 пунктов):**

1. **Тесты** — `pytest -q -p no:cacheprovider` внутри контейнера
   - `tests/` и `pyproject.toml` копируются в Docker-образ, чтобы pytest мог
     найти и запустить тесты на сервере
   - `no:cacheprovider` отключает кэш pytest, так как контейнер запущен
     под не-root пользователем без прав на запись в `/app`
   - ✓: все тесты passed
   - ✗: любой тест failed или не найден → exit 1

2. **Health endpoint** — `curl http://127.0.0.1:8000/health`
   - ✓: HTTP 200, JSON с `status`, `service`
   - ✗: curl failed, unreachable → exit 1

3. **Container status** — `docker compose ps $SERVICE`
   - Проверяется текстовый вывод (не требуется `jq` — его может не быть на сервере)
   - ✓: container state = running/healthy
   - ✗: container is down, crashed → exit 1

4. **Error logs** — `docker compose logs --tail 100 | grep -i error`
   - `grep` обёрнут в `|| true`, чтобы отсутствие совпадений не считалось ошибкой при `set -e`
   - ✓: нет ошибок/исключений
   - ⚠: найдены Error/Traceback (warning, не блокирует)

5. **Database schema** — PRAGMA table_info, проверка таблиц/колонок
   - Таблицы: `clients`, `positions`, `labels`, `domain_labels`
   - Колонки в `clients`: `id`, `client_id`, `queries`, `regions_map`, `searchers`, `project_id`
   - ✓: все присутствуют
   - ✗: отсутствуют → exit 1

6. **Data integrity** — осиротевшие записи (LEFT JOIN)
   - ✓: нет позиций/меток с `client_id`, которого нет в `clients`
   - ✗: найдены осиротевшие → exit 1

**Результат:** Итоговая сводка `N/6 passed`, exit 0 или exit 1

---

### backup_db.sh (ручной вызов перед миграциями)

**Файл:** `backup_db.sh`

**Как запустить:**
```bash
cd /root/serp
./backup_db.sh [SERVICE=serplux]
```

**Что делает:**
- Создаёт бэкап: `/app/data/serplux.db.bak.YYYY-MM-DD-HHMMSS`
- Проверяет целостность бэкапа (валидный SQLite)
- Ротирует старые бэкапы (хранит последние 10)
- Выводит список текущих бэкапов

**Рекомендуется запускать:**
- Перед критическими миграциями (переносом `client_id`, изменением схемы)
- В рамках ручного чек-листа перед `/deploy` на боевой сервер

---

## Ручные проверки

После `verify.sh` пройдена (exit 0), остаются ручные проверки:

### Функциональность (человеком на сервере)

1. **Подключение к Google Sheets**
   ```bash
   curl -H "Authorization: Bearer $WEBHOOK_SECRET" \
     http://127.0.0.1:8000/status
   ```
   Ожидаемо: `{"status":"idle","timestamp":"..."}`

2. **Запуск сбора** (из Apps Script UI или curl)
   ```bash
   curl -X POST http://127.0.0.1:8000/run \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $WEBHOOK_SECRET" \
     -d '{"client_id":"28938353", "date":"2026-07-09"}'
   ```
   Проверить: контейнер не падает, логи не показывают FATAL errors

3. **Экспорт в Google Sheets**
   - Открыть лист «Собранные URL»
   - Проверить, что строки заполнены: date, query, position, url, domain, label, snippet
   - Проверить цветовую разметку (позитив/нейтраль/негатив)

4. **Матрица отчёта** (лист «Матрица отчёта»)
   - Проверить, что по осям отчёта: доля позитива, нейтрали, негатива по запросам
   - Проверить, что итоговая сводка не пуста

5. **Логи миграции** (если это первый запуск после deploy.sh)
   ```bash
   docker compose logs --tail 50 serplux | grep migrate
   ```
   Проверить: нет FATAL, все schema updates applied

### Боевые данные (перед/после миграции)

1. **Бэкап** — обязательно перед миграцией с переносом `client_id`
   ```bash
   ./backup_db.sh
   ```

2. **Списание мусорного клиента** — если migrate перенесла данные с `client_id='default'`
   - Проверить БД: `SELECT COUNT(*) FROM positions WHERE client_id='default'` → должно быть 0
   - Если не 0, значит перенос не завершился, не удалять default

3. **Откат** — если что-то сломалось после migrate
   ```bash
   docker compose exec serplux cp /app/data/serplux.db.bak.YYYY-MM-DD /app/data/serplux.db
   docker compose restart serplux
   ```

---

## Чек-лист деплоя (для пользователя)

### До deploy.sh
1. `git push origin main` выполнен (новый код на GitHub)
2. GitHub Actions CI пройдена (все тесты ✓ в GitHub)
3. `./backup_db.sh` выполнена (боевой бэкап сделан)

### Сам deploy.sh
```bash
cd /root/serp
git pull origin main
./deploy.sh
```

### После deploy.sh
1. `./verify.sh` — все 6 проверок ✓
2. curl к `/status` и `/run` — ответы 200
3. Ручное тестирование функциональности (см. выше)
4. Проверка боевых данных, логов

---

## Границы (что НЕ проверяет)

- **verify.sh НЕ проверяет:**
  - Корректность Topvisor API ключей (только health endpoint)
  - Доступность внешних сервисов (Google Sheets, DeepSeek API)
  - Боевых данных (достаточно только целостность — нет осиротевших)
  - Версии Python/зависимостей (только что контейнер запущен)

- **CI НЕ делает:**
  - Интеграционное тестирование с реальными API
  - Лоад-тестирование
  - Security-сканирование кода (планируется отдельно)

- **Ротация бэкапов НЕ автоматична:**
  - `backup_db.sh` ротирует только последние 10 при ручном вызове
  - Для автоматической очистки нужна cron-задача (на сервере)

---

## Примеры

### Сценарий 1: Обновление кода на боевом сервере

```bash
# На сервере
cd /root/serp

# Шаг 0: бэкап перед миграцией
./backup_db.sh

# Шаг 1: деплой (new code, build, up -d, migrate)
./deploy.sh

# Шаг 2: автоматическая верификация
./verify.sh

# Шаг 3: ручная проверка
# - curl /status
# - запустить сбор из Apps Script
# - проверить Google Sheets

# Готово
```

### Сценарий 2: Откат при ошибке в verify.sh

```bash
# verify.sh не прошла (exit 1)
./verify.sh

# Посмотреть детали
docker compose logs --tail 50 serplux

# Откатить код
git checkout <предыдущий коммит>
./deploy.sh

# Повторить verify.sh
./verify.sh
```

### Сценарий 3: Восстановление из бэкапа

```bash
# БД сломалась после миграции
docker compose exec serplux cp /app/data/serplux.db.bak.2026-07-09-120530 \
  /app/data/serplux.db
docker compose restart serplux
./verify.sh
```

---

## Отладка verify.sh

Если verify.sh обрывается без понятной ошибки, запустите с трассировкой:

```bash
bash -x ./verify.sh
```

Это покажет, на какой строке скрипт умер. Обычные причины:

- `set -e` + bash arithmetic: `((var++))` возвращает 1, когда `var=0`.
- Отсутствие `pytest` в образе (см. раздел "Фикс: pytest в образе").
- Отсутствие `jq` (устарело — текущая версия не использует jq).
- Зависание `curl` к health endpoint — проверить `docker compose ps` и порт.

## Будущие улучшения

- Cron для авторотации бэкапов (старше 30 дней удалять)
- Интеграция verify.sh в webhook `/verify` (можно вызвать из Apps Script)
- Slack-уведомления на ошибки verify.sh
- Загрузка артефактов (логи, бэкапы) в S3/облако при критических ошибках
