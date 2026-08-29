# SERPlux v1.0 — Rollback

Документ описывает только поддержанные репозиторием и deployment-скриптами
пути отката. Секреты и содержимое `.env` не входят в git rollback.

## Git

1. Перейти в корень проекта.
2. Проверить рабочее состояние: `git status --short`.
3. Найти нужный ранее подтверждённый commit: `git log --oneline`.
4. Переключить рабочую копию на выбранный commit штатной командой git.
5. Проверить `git rev-parse --short HEAD`.

Если откат выполняется через серверный `deploy.sh`, скрипт сам выполняет
`git pull origin main`, поэтому перед запуском на `main` должен быть выбран
нужный commit в удалённом репозитории.

## Compose deployment

Из каталога с `docker-compose.yml` повторно запустить:

```bash
docker compose build
docker compose up -d
curl -sf http://localhost:8000/health
```

Полный поддержанный путь также доступен через `./deploy.sh`: он выполняет
`git pull origin main`, сборку образа, `docker compose up -d`, миграцию
`migrate.py --db /app/data/serplux.db` и два health-check.

## Database backup

Перед deployment можно выполнить:

```bash
./backup_db.sh
```

Скрипт копирует `/app/data/serplux.db` в файл вида
`/app/data/serplux.db.bak.YYYY-MM-DD-HHMMSS`, проверяет SQLite и оставляет
последние 10 backup-файлов.

`deploy.sh` также создаёт timestamped backup через `docker compose exec` перед
сборкой образа, если база существует.

Восстановление из backup в автоматическом режиме репозиторием не поддержано;
не следует выдумывать команду restore. Для восстановления нужен отдельный
операционный план с подтверждением владельца данных.
