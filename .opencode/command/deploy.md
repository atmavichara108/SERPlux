
---
description: Развернуть SERPlux на сервере: настройка, деплой, проверка. Запускает infra-dev.
agent: infra-dev
---

# Деплой SERPlux на сервер

## Контекст
!`cat docs/progress.md`
!`cat docs/techdebt.md`

## Текущее состояние
- Dockerfile и docker-compose.yml готовы
- Приложение задеплоено на сервер с собственным доменом
- Безопасность: тесты пройдены, критических дыр нет

## Задача
### 1. Проверка текущего деплоя
```
!`docker compose ps`
!`docker compose logs --tail=50 serplux`
!`curl -s http://localhost:8000/health`
```

### 2. Обновление (если нужно)
- Backup SQLite БД
- `docker compose pull` или `docker compose build`
- `docker compose up -d`
- Проверка health-check

### 3. Reverse proxy и SSL
- Настрой nginx/caddy перед контейнером
- SSL через certbot (Let's Encrypt)
- Проверь что /health доступен только локально

### 4. Мониторинг
- Health-check работает
- Логи ротируются
- Алерты при падении (опционально)

### 5. Проверка
- GET /health → 200
- POST /run с валидным токеном → 202
- GET /status → статус
- Веб-интерфейс загружается (если реализован)

Обнови docs/progress.md после завершения.
