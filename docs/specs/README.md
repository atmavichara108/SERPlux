# SERPlux Local Execution Specs

## Status and ownership

Это approved exception к общей Vault policy. Для SERPlux authoritative execution
specs находятся только в `docs/specs/` этого репозитория. Их владелец — проектный
workflow SERPlux; старые Vault-файлы SERPlux сохранены только как historical,
non-authoritative artifacts.

## Protocol

Единственная обычная входная команда — `/release vX.Y.Z: <описание>`. В рамках
intake она задаёт вопросы, затем создаёт ровно один deterministic authoritative
spec в `docs/specs/`, показывает его вместе с планом и ждёт explicit plan approval.
Предварительные `/prompt` и `/spec` не требуются. `/prompt` — optional read-only
diagnostic helper; `/spec` только читает или перечисляет existing specs.

Selector `/spec` обязан однозначно разрешаться внутри `docs/specs/` и не может
содержать путь за пределами этого каталога. При отсутствии, неоднозначности или
несовместимости spec — `BLOCKED`; к Vault и случайным `docs/spec*` fallback нет.

Все implementation, task brief, plan/build/review/verifier и finalization gates
выполняются в репозитории SERPlux. Spec описывает instructions и gates, но не
доказывает выполнение.

## Naming and no duplicates

Имена — lowercase kebab-case, с версией или task id при необходимости:
`<scope>-<version-or-task>.md`. Один execution scope имеет одну authoritative
версию. Старые pointers, audits и runbooks могут ссылаться на spec, но не
дублируют instructions. Не создавайте specs в `docs/` вне этого каталога.

## Required gates

- preflight фиксирует repo, branch, status, HEAD, tags и ownership pre-existing changes;
- generated spec, plan и scope получают explicit approval до implementation;
- targeted tests, отдельные reviewer и acceptance-only verifier идут после build;
- FAIL допускает не более пяти fix-итераций, затем `BLOCKED`;
- после verifier PASS workflow останавливается в `READY_FOR_USER_INTEGRATION`/
  `AWAITING_USER_REVIEW` и передаёт дальнейшие действия пользователю;
- `/release` не запускает flush, commit, tag, push или deploy автоматически;
- `/dream`/equivalent flush остаётся отдельным ручным действием после решения
  пользователя и не входит в основной loop;
- пользователь может вручную выполнить `/commit` → push workflow → `/deploy` и
  server check; эти команды и pre-commit/commit-guard применяют свои проверки,
  без дополнительного gate от `/release`;
- pre-existing changes остаются под scope safety и не включаются в ручной commit.

`/release v1.0.2: ...` создаёт local canonical spec в рамках workflow. Наличие
spec или plan approval не объявляет feature или release выполненными. Verifier
PASS означает только прохождение acceptance gate по DoD; он не является
production readiness и не заменяет server check после ручного commit/push/deploy.
