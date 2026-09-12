---
description: Деплой SERPlux: serpctl release <tag> (гейт подтверждения) → Actions → автодеплой. Проверка статуса.
agent: infra-dev
---

# Деплой SERPlux через serpctl (immutable releases, Workstream D)

## Модель

Агент НЕ SSH-ится на сервер. Деплой автоматический: **пуш тега `v*` →
`.github/workflows/release.yml`** (тесты → buildx с тегами `vX.Y.Z`+`sha` →
GHCR → SSH → `scripts/release.sh`: preflight → backup → migration preflight
на копии → pull по digest → smoke `/version` → auto-rollback при провале →
deployment record).

## Задача

1. Убедись в готовности к релизу:
   - `python3 scripts/serpctl.py test` — зелёный;
   - `git status` — чистое дерево (чужой WIP — блокер, не stage его);
   - HEAD соотв. ожиданию (`git log --oneline -3`).
2. Подтверди с пользователем версию (semver `vX.Y.Z`, выше последнего тега
   `git tag -l`). Версию не выбирать молча.
3. Запусти release (RUN GATE: требует явного подтверждения пользователя):
   ```bash
   python3 scripts/serpctl.py release vX.Y.Z --dry-run   # показать шаги
   python3 scripts/serpctl.py release vX.Y.Z --yes       # тег+пуш → автодеплой
   ```
4. Следи за деплоем:
   - GitHub Actions → workflow **Release** (короткая ссылка из вывода `git push`);
   - `python3 scripts/serpctl.py deploy-status` — последний deployment record
     (пишется release.sh на сервере в docs/deployments.json);
   - `python3 scripts/serpctl.py health` — жив ли сервис.
5. Откат (если нужен): вручную на сервере по инструкции из
   `scripts/release.sh` (docker tag предыдущего digest) — либо новый тег.

## Границы
- Агент не выполняет `git push`, тег и деплой сам — только `serpctl release`
  с `--yes` после явного подтверждения пользователя.
- Тег `latest` и `git pull main` в проде запрещены (immutable releases).
- Ошибка SSH/GHCR-секрета → `BLOCKED`, не ретраить бесконечно.
- Полный runbook: `docs/serpctl.md` § «Release».
