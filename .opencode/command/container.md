
---
description: Создать/обновить Dockerfile и docker-compose.yml для SERPlux. Запускает infra-dev.
agent: infra-dev
---

# Docker-контейнеризация SERPlux

## Контекст
!`cat docs/progress.md`

## Текущее состояние
- Dockerfile уже есть: multi-stage (builder → runtime), python:3.11-slim, non-root
- docker-compose.yml уже есть: serplux сервис, volume, credentials.json

## Задача
Проверь и улучши текущую конфигурацию:

### Dockerfile
- Multi-stage сборка — уже есть, проверь оптимальность
- Non-root пользователь — уже есть (serplux:1001)
- HEALTHCHECK — уже есть
- Убедись что templates/ и static/ копируются в образ (если UI реализован)
- requirements.txt — проверь что все зависимости учтены

### docker-compose.yml
- Volume для SQLite — уже есть
- credentials.json read-only — уже есть
- Ограничения ресурсов — уже есть (512MB, 1.0 CPU)
- Добавь reverse proxy (nginx/caddy) если нужно
- Добавь SSL через certbot если нужно

### Что проверить
- Все ли файлы приложения копируются в образ
- Правильно ли настроены пути для templates/ и static/
- Работает ли health-check
- Корректна ли ротация логов

Обнови docs/progress.md после завершения.
