# Execution Spec: закрытие SERPlux v1.0

> **AUTHORITATIVE LOCAL SPEC.** Это approved SERPlux exception: единственный
> источник execution instructions находится в `serp/docs/specs/`. Vault-файл с
> тем же scope сохранён только как archived/non-authoritative artifact.

## Назначение и ограничения

Документ предназначен для отдельного execution-агента и не является evidence
выполнения, тестов, commit, tag или release readiness. Не менять `*.py`, tests,
Docker/prod-конфиги и pre-existing изменения. Не коммитить и не тегировать без
отдельных approvals.

## Порядок выполнения

1. В `/home/rudra/Projects/serp` прочитать `AGENTS.md`, `README.md`, затем
   проверить `git status --short`, `git log --oneline`, `HEAD` и ownership.
2. До changelog и rollback прочитать историю и фактические deployment/backup
   scripts; не выдумывать подтверждённые факты.
3. Обновить только согласованные docs: `docs/roadmap-2.0.md`, `CHANGELOG.md`,
   `docs/rollback.md`, `docs/acceptance-1.0.md`, `README.md`, `docs/progress.md`.
4. В acceptance разделить подтверждённое и непроверенное.
5. Перед завершением проверить diff/status, `git diff --check`, полный тестовый
   результат и tags; commit/tag/push не выполнять автоматически.

## Required document content

- `docs/roadmap-2.0.md`: сохранить текущий roadmap и разделы `ПРОДУКТ` и
  `АРХИТЕКТУРА`, включая deep labeling, удаление legacy `label_mode`, etalon
  automation, backup rotation, watchdog, notifications, LLM cost, qwen-plus
  quality, etalon coverage, geo dedup, global `domain_labels` scope,
  customer-specific `migrate.py`, test-count normalization и P0 shared-instance
  оговорку для global status/lock, daemon jobs и credentials.
- `CHANGELOG.md`: Keep a Changelog, заголовок `[1.0.0] - 2026-08-17`, только
  подтверждённые историей multiclient onboarding, cumulative reports, etalon
  `(domain,query)`, snippet-cache removal, geo dedup и retry 429/5xx.
- `docs/rollback.md`: описывать только фактические git/Compose/deploy/backup
  capabilities; restore mechanism не выдумывать.
- `docs/acceptance-1.0.md`: отдельно проверить 262 tests, real `label_only`,
  report, geo dedup и etalon, с явными статусами подтверждения.
- `README.md`: только необходимые docs changes, product structure и 262 tests;
  `*.py` не менять. `docs/progress.md` должен фиксировать v1.0 maintenance и
  перенос разработки на Overture.

## Acceptance

Документировать v2.0 tech debt в разделах `ПРОДУКТ` и `АРХИТЕКТУРА`, Keep a
Changelog с подтверждённой историей, фактический rollback, acceptance для 262
тестов, `label_only`, отчёта, geo dedup и эталона, а также maintenance/Overture
status. Наличие spec не засчитывает ни один критерий.

## Tag safety

Старый `v1.0.0` не перемещать и не удалять. Любой commit, annotated tag или push
возможны только после отдельных явных решений; force operations запрещены.
Если lifecycle closure отдельно потребует tag, сначала проверить `HEAD` против
`bdc9a54`; при несовпадении остановиться и запросить решение. Результат
`pytest -q`, `git diff --stat` и `git tag -l` сохранить в отчёте.
