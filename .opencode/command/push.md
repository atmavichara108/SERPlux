---
description: Push workflow с explicit approval gate. Только после manual commit.
agent: build
model: opencode-go/deepseek-v4-flash
---

# /push — безопасный push в удалённый репозиторий

## Контекст
- `/commit` уже выполнен (conventional commit, tests пройдены через commit-guard)
- Push требует **явного approval** пользователя — не автоматизируется
- Никогда не используй force, не двигай tags, не rewrite history

## Workflow

1. **Preflight** (read-only):
   - `git status` — убеждаемся что HEAD чистый (commit уже сделан)
   - `git log --oneline -3` — проверяем что коммит есть
   - `git remote -v` — проверяем remote

2. **Проверка перед push**:
   - `git diff --check` — убеждаемся что нет незакоммиченных изменений
   - `git diff --stat origin/main` — показываем что пушится

3. **Явный approval gate**:
   ```
   ⚠️  Готово к push:
   - Коммит: <hash> <message>
   - Изменения: <N> файлов
   - Remote: origin/main

   Подтвердите push: "да, пушить" или "cancel"
   ```

4. **Push** (только после подтверждения):
   - `git push origin main`
   - `git push origin <tag>` (если есть новый tag)

5. **Post-push**:
   - `git log --oneline -3` — подтверждаем что push прошёл
   - Сообщить пользователю: commit hash, remote URL

## Safety
- Никогда не используй `--force`, `--force-with-lease`
- Никогда не удаляй remote tags
- Никогда не push в ветку без explicit approval
- Если push fails (non-fast-forward) — остановись, не retry автоматически
