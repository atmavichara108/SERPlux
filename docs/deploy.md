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

## Процесс обновления

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
docker compose exec serplux python migrate.py --db /app/data/serplux.db

migrate.py:
- Шаг 0: бэкап serplux.db.bak.YYYY-MM-DD
- Перенос results → positions + labels
- Верификация COUNT(results) == COUNT(positions)
- DROP results только при успехе

## Откат

- git checkout <предыдущий коммит>
- docker compose build && docker compose up -d
- При потере БД: восстановить из бэкапа serplux.db.bak.YYYY-MM-DD

## Reverse proxy (nginx)

Контейнер слушает 127.0.0.1:8000. Для внешнего доступа — nginx/caddy:
- proxy_pass http://127.0.0.1:8000
- SSL через certbot --nginx
- /health можно закрыть или оставить открытым