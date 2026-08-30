# Execution Spec: безопасное разрешение конфликта тега SERPlux v1

> **AUTHORITATIVE LOCAL SPEC.** Этот файл в `serp/docs/specs/` — единственный
> источник instructions для scope. Vault successor является архивом.

## Цель и ограничения

Устранить ambiguity вокруг `v1.0.0` без переписывания публичной истории и
force-move существующего тега. Перед execution прочитать локальные `AGENTS.md`,
`README.md`, проверить repo `/home/rudra/Projects/serp`, branch, HEAD, status,
коммиты `bdc9a54`/`3f80fcc`, local/remote tag state и ownership изменений.

Не менять app code, tests, pre-existing docs или `CHANGELOG.md` при конфликте с
release policy. Не удалять и не передвигать `v1.0.0` по умолчанию. При mismatch,
неопределённости remote state, dirty/unowned state или недоступности данных —
`BLOCKED` без автоматического исправления.

## Execution gates

1. Сохранить evidence исходного repo/branch/HEAD/status и local/remote tag state.
2. Проверить границы документального закрытия; application code и tests должны
   остаться неизменными.
3. Сохранить legacy `v1.0.0`; новый tag возможен только на approved
   documentation commit.
4. Commit, annotated tag и push выполняются только после отдельных approvals;
   target SHA перепроверяется непосредственно перед tag.
5. Финально проверить refs, tag object/message/target, status и log.

Force tag/delete, force push, rewrite history и silent fallback запрещены.
Наличие spec не является выполнением процедуры.
