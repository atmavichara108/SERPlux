# Dynamic Etalon Import v1.0.2

**Status:** implemented (Stage 1 + Stage 2; см. docs/progress.md 2026-08-30)
**Version:** v1.0.2
**Branch:** `main`
**Target tag:** `v1.0.2`

> **Примечание к реализации (2026-09-06):** код Stage 1
> (`importHistoricalEtalonsToDb`) и Stage 2 (`importLatestReportToEtalon`,
> меню «Зафиксировать исправления в эталон») в apps_script.gs, импорт через
> `POST /labels/import` с `source=manual_l1`. Production-импорт трёх
> исторических листов — ручная операция; evidence её выполнения в репо
> не зафиксирован (требуется прогон + сверка счётчиков imported/skipped).

## Goal

Безопасно пополнять эталон разметки из отчётов, вручную проверенных заказчиком.

## Context

Заказчик меняет цвет ячейки позиции на листе `Отчёт`. Цвет соответствует
`positive`, `negative` или `neutral`. Эталон хранится в `domain_labels` и в
текущем контракте индексируется по `domain + query`, без `geo`. У каждого
клиента отдельная SQLite-БД.

На первом этапе нужно импортировать три исторических листа:

- `Google — посл эталон разметки`;
- `Яндекс ру — посл эталон разметки`;
- `Яндекс ком — посл эталон разметки`.

## Requested Changes

### Stage 1: historical import

Добавить изолированную ручную функцию Apps Script, которая читает три листа,
использует геометрию `Отчёт`, извлекает URL, субъект/query и sentiment по цвету,
нормализует URL до `domain` на сервере и отправляет записи через существующий
`/labels/import` с `source=manual_l1`.

Листы обрабатываются независимо, битые строки пропускаются, итог содержит
числа обработанных, импортированных, пропущенных записей и ошибок. Функция не
добавляется в регулярный пользовательский flow.

### Stage 2: regular manual import

Добавить меню `SERPlux → Зафиксировать исправления в эталоне`.

Команда читает только последнюю версию листа `Отчёт`, извлекает все валидные
URL с распознанным цветом, показывает количество записей и требует явного
подтверждения. После подтверждения записи импортируются как `manual_l1` через
идемпотентный batch API. `onEdit` и автоматический hook при `Запустить сбор` не
используются.

Порядок работы пользователя: запустить сбор, проверить и исправить цвета,
зафиксировать исправления одной командой, затем скопировать отчёт в боевую
таблицу.

## Constraints and Anti-goals

- Google Sheets и Apps Script остаются единственным UI.
- Сохраняется контракт `domain + query`, без `geo`.
- `manual_l1` имеет высший приоритет.
- Каждый клиент использует отдельную БД.
- Не менять Topvisor, LLM, сбор и геометрию отчёта.
- Не использовать `onEdit`.
- Не импортировать без явного подтверждения пользователя.
- Не добавлять зависимости и не затрагивать чужие изменения.
- Не выполнять commit, tag, push, deploy или flush в рамках release workflow.

## Scope

- `apps_script.gs`
- `webhook.py` и `storage.py` только при необходимости исправить импортный вызов
- связанные тесты в `tests/`
- `docs/contracts.md`
- `docs/labeling_canon.md`
- `docs/CANON.md`
- `docs/decisions.md`
- `docs/progress.md`
- этот spec

Запрещённые области: `topvisor.py`, `collector.py`, `labeler.py`,
`exporter.py`, Docker/deploy/server, production `.env`.

## Definition of Done

- Три исторических листа импортируются одной ручной операцией.
- Повторный импорт идемпотентен.
- Последняя версия `Отчёт` корректно определяется.
- Query берётся из имени субъекта, не из geo.
- Sentiment определяется по цвету позиции.
- Белые, неизвестные и битые строки пропускаются.
- Регулярная команда доступна через меню и требует подтверждения.
- Нет `onEdit` и автоматического hook при новом сборе.
- Частичная ошибка не прерывает batch.
- `manual_l1` не перезаписывается автоматическими источниками.
- Документы закрепляют `domain + query`, без `geo`.
- Targeted tests, reviewer и verifier проходят.

## Risks

- Пользователь может подтвердить импорт до окончания проверки. Снижение риска:
  предупреждение и preview количества записей.
- В отчёте могут быть старые версии. Снижение риска: Stage 2 читает только
  последнюю версию.
- Неизвестные цвета могут быть ошибочно интерпретированы. Снижение риска:
  они попадают в `skipped`.
- Документы содержат исторические описания другой схемы. Снижение риска:
  обновить текущие нормативные документы и добавить ADR.

## Rollback

Перед production-импортом сделать backup клиентской SQLite-БД. При ошибочном
импорте отключить новую команду и восстановить БД из backup. Не удалять метки
вручную без backup.

## Migration

Схема БД не меняется. `client_id` в `domain_labels` не добавляется, поскольку
клиенты используют отдельные БД. Выполняется только документальное закрепление
фактического контракта `domain + query`, без `geo`.

## Roster and Order

1. `plan`: spec, architecture and scope.
2. `build`: implementation and targeted tests.
3. `reviewer`: read-only review of quality, contracts, security and scope.
4. `verifier`: acceptance-only verification against DoD.

Отдельный domain agent не требуется.

## Implementation Plan

1. Синхронизировать нормативную документацию с фактическим контрактом.
2. Вынести общий Apps Script parser report matrix.
3. Реализовать Stage 1 для трёх исторических листов.
4. Реализовать Stage 2 для последней версии `Отчёт` с preview и confirmation.
5. Добавить targeted tests для parsing, последней версии, цветов, invalid rows,
   идемпотентности и partial failures.
6. Запустить `python -m pytest -v`.
7. Передать результат `reviewer`, затем acceptance-only `verifier`.

## Commands and Evidence

Targeted commands:

```text
python -m pytest tests/test_webhook.py -v
python -m pytest tests/test_domain_labels.py -v
python -m pytest tests/test_imports.py -v
python -m pytest tests/test_main.py -v
python -m pytest -q
```

Reviewer проверяет scope, безопасность, приоритет `manual_l1`, идемпотентность
и отсутствие автоматического hook. Verifier запускает `python -m pytest -v` и
сверяет DoD.

## Release Notes Impact

Добавить запись о ручной команде фиксации исправлений и одноразовом импорте
исторических листов. Commit/tag/push выполняются пользователем отдельно.

## Approval and Preflight

- План и scope подтверждены пользователем.
- Repo: `/home/rudra/Projects/serp`.
- Branch: `main`.
- HEAD: `c8a9868d234af71d81e3953008eab2a123731f51`.
- `origin/main` совпадает с HEAD.
- Existing tags: `v1.0.0`, `v1.0.1`.
- Target tag `v1.0.2` свободен.
- Рабочее дерево не содержит чужих dirty changes, согласно подтверждению
  пользователя и preflight `git status --short --branch`.
