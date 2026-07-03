---
description: Commit only after verifier PASS. Gate, not free action.
agent: build
model: opencode-go/deepseek-v4-flash
subtask: true
---
1. `git status` + `git diff --stat` — что коммитим.
2. Invoke @verifier: проверь staged-изменения против DoD/тестов проекта.
3. If VERDICT: FAIL → покажи numbered fixes, примени ТОЛЬКО их, вернись к п.2.
4. If VERDICT: PASS → сформируй conventional commit message (рус.),
   `git add` + `git commit`.

HARD STOP after 3 verify-fix циклов. Если всё ещё FAIL — доложи блокеры, НЕ коммить.
