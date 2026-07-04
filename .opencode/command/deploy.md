---
description: Подготовить чек-лист деплоя SERPlux на сервер. Запускает infra-dev.
agent: infra-dev
---

# Подготовка деплоя SERPlux

## Контекст
!`cat docs/progress.md`
!`cat docs/techdebt.md`
!`cat docs/deploy.md`

## Модель
Агенты работают на ЛОКАЛЬНОЙ машине. Деплой на сервер выполняется пользователем вручную через SSH.
infra-dev НЕ выполняет docker-команды на сервере.

## Задача
1. Проверить локальные файлы на консистентность:
   - Dockerfile: все .py файлы копируются (включая migrate.py)
   - docker-compose.yml: переменные, volumes, ports
   - .env.example: все переменные проброшены
2. Сформировать чек-лист команд для сервера (git pull, build, up, migrate, verify)
3. Проверить, нужна ли миграция БД (старая схема results → новая)
4. Выдать готовый чек-лист пользователю для копипаста в SSH-терминал

## Чек-лист должен включать
- Предпосылки (группа docker, .env, credentials, regions_map)
- Обновление кода (git pull)
- Сборка и запуск (docker compose build, up -d)
- Миграция БД (docker compose exec serplux python migrate.py --db /app/data/serplux.db)
- Проверки (health, status с авторизацией)
- Внешний доступ (nginx/домен — отметить если неизвестно)

Обнови docs/progress.md после завершения.