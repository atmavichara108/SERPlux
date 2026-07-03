---
description: Commit with conventional message. Tests are enforced by commit-guard plugin.
agent: build
model: opencode-go/deepseek-v4-flash
subtask: true
---
1. `git status` + `git diff --stat` — что коммитим.
2. Сформируй conventional commit message (рус.).
3. `git add` нужных файлов + `git commit`.

Тесты запускает commit-guard плагин автоматически (tool.execute.before).
