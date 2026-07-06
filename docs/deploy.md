# Деплой SERPlux на сервер

## Архитектура

Разработка на локальной машине → git push → SSH на сервер → git pull → docker compose build → up -d.
Агенты (plan, build, infra-dev) работают на локальной машине и НЕ выполняют команды на сервере.
Деплой выполняется пользователем вручную через SSH.

## Предпосылки (один раз)

- Пользователь на сервере в группе docker: `sudo usermod -aG docker $USER`, перезайти в SSH
- `.env` на сервере заполнен (не из git — в .gitignore)
- `credentials.json` на сервере (не из git)
- `regions_map*.json` на сервере
- nginx/caddy настроен как reverse proxy на 127.0.0.1:8000 (HTTPS)
- SSL-сертификат через certbot

## Автоматический деплой (рекомендуется)

Скрипт `deploy.sh` выполняет полный цикл обновления безопасно:

```bash
./deploy.sh
```

**Что делает скрипт:**
1. Проверяет наличие `docker-compose.yml` в текущем каталоге
2. `git pull origin main` — обновляет код из репозитория
3. **Бэкап БД** — создаёт `serplux.db.bak.YYYY-MM-DD-HHMMSS` перед миграцией
4. `docker compose build` — собирает новый образ
5. `docker compose up -d` — перезапускает контейнер
6. **Health-check** — поллит `http://127.0.0.1:8000/health` до 3 раз с паузой 5 сек
7. **Миграция БД** — `migrate.py` (идемпотентный, безопасен для повторного запуска)
8. Финальный health-check + вывод последних логов

**Безопасность:**
- `set -euo pipefail` — скрипт падает на любой ошибке, не продолжает вслепую
- Если health-check не пройден — миграция НЕ выполняется, выводятся логи для диагностики
- Бэкап создаётся ДО миграции — всегда можно откатиться
- Идемпотентен: повторный запуск без изменений безопасен
- НЕ трогает volume (никаких `down -v`)

**Параметризация:**
```bash
SERVICE=serplux ./deploy.sh  # можно указать другой сервис
```

**Откат при ошибке:**
Если health-check не прошёл:
```bash
docker compose logs --tail 50 serplux  # посмотреть логи
git checkout <предыдущий коммит>
docker compose build && docker compose up -d
```

Если миграция сломала БД:
```bash
docker compose exec serplux cp /app/data/serplux.db.bak.YYYY-MM-DD-HHMMSS /app/data/serplux.db
docker compose restart serplux
```

## Ручной деплой (альтернатива)

Если нужен полный контроль или отладка:

1. Локально: git commit, git push origin main
2. SSH на сервер
3. cd /root/serp
4. git pull origin main
5. diff .env.example .env — сверить переменные
6. docker compose build
7. docker compose up -d
8. docker compose ps — статус Up
9. docker compose logs --tail 30 — нет ошибок
10. curl http://localhost:8000/health — {"status":"ok","service":"serplux-webhook"}
11. source .env && curl -H "Authorization: Bearer $WEBHOOK_SECRET" http://localhost:8000/status — idle

## Миграция БД (если старая схема с таблицей results)

После up -d, до первого прогона:
```bash
docker compose exec serplux python migrate.py --db /app/data/serplux.db
```

migrate.py:
- Шаг 0: бэкап serplux.db.bak.YYYY-MM-DD
- Перенос results → positions + labels
- Верификация COUNT(results) == COUNT(positions)
- DROP results только при успехе

**Примечание:** `deploy.sh` автоматически запускает migrate.py после успешного health-check.

## Откат

- git checkout <предыдущий коммит>
- docker compose build && docker compose up -d
- При потере БД: восстановить из бэкапа serplux.db.bak.YYYY-MM-DD

## Reverse proxy (nginx)

Контейнер слушает 127.0.0.1:8000. Для внешнего доступа — nginx/caddy:
- proxy_pass http://127.0.0.1:8000
- SSL через certbot --nginx
- /health можно закрыть или оставить открытым