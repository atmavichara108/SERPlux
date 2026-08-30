---
description: Read-only просмотр и перечисление authoritative local specs SERPlux.
agent: plan
---

# /spec — local spec reader

`/spec` только читает или перечисляет существующие specs. Generated specs создаёт
агент внутри `/release` в `docs/specs/`; эта команда сама не редактирует, не
создаёт, не dispatch-ит и не коммитит.

Selector: `$ARGUMENTS`.

1. Прочитай локальные `AGENTS.md` и `README.md`.
2. Разрешай selector только внутри `docs/specs/` текущего repo. Разрешены имя
   файла или `docs/specs/<spec>.md`; `..`, Vault path и другие project paths
   запрещены.
3. При пустом selector перечисли `docs/specs/*.md`, не выбирая spec молча.
4. При отсутствии/неоднозначности selector или local context выдай `BLOCKED` с
   причиной. Не обращайся к Vault и не используй `docs/spec*` вне каталога.
5. Покажи absolute canonical path, scope, constraints, DoD и gates. Spec —
   инструкция, не evidence; plan approval, review и verifier остаются частью
   `/release`, а commit, tag, push, deploy и flush выполняются только вручную
   через существующие project workflows после соответствующего handoff.
