---
description: Read-only diagnostic helper для нормализации задачи SERPlux; обычно не вызывается пользователем.
agent: plan
---

# /prompt — optional diagnostic helper

Используй сырой запрос из `$ARGUMENTS` и только выведи диагностический brief.
Пользователь обычно не вызывает `/prompt`: тот же prompt-engineer/task-compiler
protocol автоматически выполняется внутри `/release`. Команда не редактирует
файлы, не создаёт spec, не вызывает `task`, не делает build/review/test и не
объявляет задачу выполненной.

Не обращайся к Vault и не создавай global `prompt-engineer`/`task-compiler` runtime.
При неясном маршруте укажи `UNROUTABLE`/`BLOCKED`; не выбирай `general` fallback.

Выведи разделы в этом порядке:

## Goal
## Product/context
## Requested change(s)
## Constraints / anti-goals
## Affected-area hypothesis
## Acceptance criteria / DoD
## Risks, rollback, data migration
## Agent roster and order
## Version/release target
## Open questions / required approvals
## Evidence to collect

Используй `[HYPOTHESIS]` и `[UNKNOWN]`. Brief — плановая диагностика, не
authoritative local spec и не acceptance evidence.
