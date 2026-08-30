---
description: Единый интерактивный release workflow SERPlux: intake, local spec, approval, build/review/verifier loop и ручной handoff.
agent: plan
---

# /release — единая входная команда SERPlux

Обычный feature/fix workflow запускается только через `/release`. Предпочтительный
формат:

```text
/release v1.0.2: <свободное описание задачи>
```

Plain description допускается, но версию нужно запросить и явно подтвердить. Не
выбирай версию, tag, branch или scope молча. Поддерживаются semver с префиксом
`v`; неоднозначность означает `BLOCKED`/вопрос, а не догадку.

`/release` не требует предварительных `/prompt` или `/spec` и не использует Vault.
Для SERPlux authoritative spec создаётся в `docs/specs/` этим workflow.

## Safety preflight

До любой реализации проверь и покажи evidence:

- repo path, branch, HEAD, status, последние commits, tags и target tag;
- pre-existing dirty changes и их ownership;
- доступность и однозначность version, tag, branch и scope;
- отсутствие пересечения approved scope с pre-existing changes.

Если есть pre-existing dirty/unowned changes, ambiguous version/tag/branch/scope,
занятый target tag или неверный repository — `BLOCKED`; чужие изменения нельзя
трогать, stage, включать в diff или коммитить. Ошибки provider/auth/network не
устраняй бесконечными retries.

## A. Intake / normalization / questions

Локальный prompt-engineer/task-compiler protocol выполняется внутри `plan`-agent;
глобальный runtime с такими именами не создаётся. Разбери `$ARGUMENTS`, нормализуй
goal, context, changes, constraints, anti-goals, candidate scope, DoD, risks,
rollback, migration и roster. Гипотезы помечай `[HYPOTHESIS]`, неизвестное —
`[UNKNOWN]`.

Задай необходимые уточняющие вопросы и остановись. Не формируй исполняемый план,
не вызывай `task` и не начинай build, пока ответы не устранят критические unknowns.
Roster должен содержать реальные named roles: `plan`, `build`, при необходимости
ровно одного domain agent (`collector-dev`, `ui-dev` или `infra-dev`), затем
отдельные `reviewer` и project `verifier`. Нет подходящей роли — `UNROUTABLE`, без
silent `general` fallback.

## B. Local spec / plan / approval

После intake создай ровно один deterministic spec для scope в
`docs/specs/<scope>-<version>.md` (lowercase kebab-case). При collision, второй
версии или неоднозначном имени — `BLOCKED`, не перезаписывай spec. Spec обязан
содержать: goal, context, requested changes, constraints/anti-goals, scope и
запрещённые области, DoD, risks, rollback, migration, roster/order, version,
approvals и evidence.

Покажи пользователю полный spec и implementation plan. Plan обязан включать файлы,
targeted tests, reviewer/verifier commands, scope boundary, release notes impact,
rollback path и ожидаемые evidence. Spec, brief и plan являются плановыми
артефактами, не evidence выполнения. Остановись со статусом
`AWAITING_PLAN_APPROVAL`; explicit approval плана обязателен до dispatch/build.

## C. Build / tests / review / verifier

Только после plan approval передай утверждённый scope named `build`-agent через
`task`; domain agent вызывается только если это есть в spec. Build loop:

1. build изменяет только approved scope;
2. targeted tests;
3. отдельный `reviewer` для quality, contracts, security и scope;
4. отдельный acceptance-only `verifier` по DoD.

`reviewer != verifier`. При FAIL допустимо не более 5 fix-итераций; каждая
повторяет tests → reviewer → verifier и фиксирует причины, правки и evidence.
После пятого FAIL — `BLOCKED`.

В обычном loop и после `VERDICT: PASS` не запускай `/dream`, memory flush, commit,
tag, push или deploy. Не выполняй их условно или автоматически. После
`VERDICT: PASS` ничего не меняй сам.

## D. Verifier report and manual handoff

Verifier PASS означает, что код прошёл acceptance gate по DoD, но не заменяет
пользовательскую приёмку и не является утверждением production readiness или
результатом серверной проверки. Покажи пользователю:

- результат, изменённые файлы и diff/status;
- targeted tests, reviewer result и verifier evidence;
- DoD/feature status и оставшиеся риски.

Затем остановись со статусом `READY_FOR_USER_INTEGRATION` или
`AWAITING_USER_REVIEW` и явно укажи, что flush отложен. Пользователь может
самостоятельно перейти к существующему `/commit`, затем выполнить доступный
push workflow и `/deploy`/серверную проверку. `/release` не требует для этого
дополнительной фразы или approval и не выполняет эти действия сам. Команды
`/commit`, `/deploy`, push workflow и pre-commit/commit-guard применяют свои
собственные текущие проверки.

Дополнительная правка возможна только если пользователь отдельно запускает
явно одобренную fix iteration; после неё снова пройти C и D. Pre-existing dirty
changes остаются исключёнными из scope и не должны stage, изменяться или
включаться в ручной commit.

## E. Separate manual actions

`/dream` или documented equivalent flush остаётся отдельным ручным действием
после решения пользователя и не является частью `/release`. `/release` не
запускает commit/tag/push/deploy автоматически и не блокирует ручной переход к
ним после verifier PASS. Если tag относится к отдельной release operation,
пользователь выполняет её через существующий проектный workflow без нового
gate со стороны `/release`.

Серверная проверка выполняется только после ручного commit/push/deploy и
фиксируется отдельно от verifier evidence. Не используй force tag,
delete/rewrite history или force push. При ошибке оставь рабочее дерево и
зафиксируй `BLOCKED`; не повторяй push бесконечно.

## Final states

Отчёт обязан содержать status, spec path, roster, diff/status, test output,
reviewer/verifier evidence и отдельно оставшиеся риски. После verifier PASS
итог — `READY_FOR_USER_INTEGRATION` или `AWAITING_USER_REVIEW`; flush, commit,
tag, push, deploy и server-check не объявляются выполненными и не включаются в
отчёт как выполненные действия.
