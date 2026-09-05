# Provider UI, Searcher Checkboxes, Depth Fix v1.0.2

**Status:** approved for implementation  
**Version:** v1.0.2 (extension)  
**Branch:** `main`  
**Target tag:** `v1.0.2`

## Goal

1. Упростить добавление LLM-провайдера: встроенные endpoint'ы, безопасное добавление API key, auto-discovery бесплатных моделей.
2. Добавить checkbox выбора поисковиков на листе `Настройки` (по дефолту все выбраны, применяется к текущему прогону).
3. Исправить баг: `depth=50` не пробрасывается в Topvisor API.
4. Исправить env reload: после изменения `.env` и restart контейнера новый API key не подтягивается.

## Context

- OpenCode Zen — единственный встроенный провайдер.
- Бесплатные модели = literal `*-free` в имени.
- Пользователь не должен вводить endpoint вручную.
- API key не должен отображаться в UI.
- Глубина 50 критична для текущего запуска.

## Requested Changes

### 1. Провайдер UI (Apps Script)

**Текущее состояние:**
- `_addProviderDialog()` спрашивает: ID, endpoint, default_model, models, api_key_env_var, priority.
- Пользователь должен знать endpoint.

**Целевое состояние:**
- Пользователь вводит только:
  - ID провайдера (slug, например `openrouter`);
  - API key (через безопасное поле, не сохраняется в БД);
  - Выбирает из списка известных endpoint'ов или вводит свой.
- Система автоматически:
  - Делает тестовый запрос к `/models` endpoint (если поддерживается);
  - Фильтрует только `*-free` модели;
  - Тестирует каждую на отклик (timeout 10s);
  - Добавляет рабочие модели в dropdown.
- В UI показывает:
  - Список протестированных бесплатных моделей;
  - Статус (ok/error/timeout);
  - Возможность выбрать default_model.

**Встроенные endpoint'ы:**
```text
OpenCode Zen: https://opencode.ai/zen/v1/chat/completions
OpenRouter: https://openrouter.ai/api/v1
OpenAI: https://api.openai.com/v1
```

### 2. Checkbox поисковиков (Apps Script)

**Лист `Настройки`:**
```text
[ ] Поисковики:
  [x] Google
  [x] Яндекс ру
  [x] Яндекс ком
```

- По дефолту все выбраны.
- Выбор применяется только к текущему прогону.
- Не изменяет профиль клиента.
- `runCollection()` читает checkbox'ы и передаёт `searchers` в `/run`.

### 3. Fix depth=50 (backend)

**Проблема:**
- `RunRequest.depth` валидируется (10/20/50/100).
- `collector.py` получает `depth` из config.
- Но Topvisor API может игнорировать depth на уровне запроса.

**Расследование:**
- Проверить `topvisor.py::run_check()` — передаётся ли depth.
- Проверить Topvisor API docs — где задаётся глубина.
- Возможно, depth задаётся на уровне проекта, а не запроса.

**Fix:**
- Если depth не пробрасывается — добавить в запрос Topvisor.
- Если Topvisor игнорирует — логировать warning и использовать default.

### 4. Fix env reload (Docker)

**Проблема:**
- После изменения `.env` и `docker compose restart` старый API key продолжает работать.

**Причина:**
- `docker compose restart` не пересоздаёт контейнер, env остаётся старым.
- Нужно `docker compose down` + `up` или `docker compose up -d --force-recreate`.

**Fix:**
- Добавить в `deploy.sh` опцию `--force-recreate`.
- Или документировать в `docs/deploy.md` правильный порядок.

## Constraints

- Не менять локальные модели субагентов.
- API key не должен отображаться в UI.
- Endpoint должен быть встроенным или предлагаться на выбор.
- Тестировать только бесплатные модели.
- Checkbox поисковиков — только для текущего прогона.

## Anti-goals

- Не добавлять произвольные endpoint'ы без проверки.
- Не сохранять API key в БД.
- Не изменять профиль клиента при выборе поисковиков.
- Не трогать `collector-dev` без необходимости.

## Scope

- `apps_script.gs` — provider UI, checkbox поисковиков.
- `webhook.py` — auto-discovery endpoint, test models.
- `config.py` — встроенные endpoint'ы.
- `topvisor.py` — fix depth.
- `deploy.sh` — force-recreate опция.
- `docs/deploy.md` — инструкция env reload.
- Тесты: `test_webhook.py`, `test_collector.py`.

## DoD

- Пользователь может добавить провайдера без ввода endpoint.
- API key не отображается в UI.
- Бесплатные модели тестируются и добавляются в dropdown.
- Checkbox поисковиков работает и передаётся в `/run`.
- `depth=50` пробрасывается в Topvisor API.
- После `docker compose down && up` новый API key подтягивается.
- Тесты проходят.
- Reviewer и verifier PASS.

## Risks

1. **Topvisor API не поддерживает depth на уровне запроса.**  
   Снижение: логировать warning, использовать default.

2. **Auto-discovery моделей может расходовать лимиты.**  
   Снижение: тестировать только `*-free` модели, timeout 10s.

3. **Checkbox поисковиков может сломать backward compatibility.**  
   Снижение: по дефолту все выбраны, fallback на профиль клиента.

## Rollback

- Откатить Apps Script изменения.
- Вернуть старый `topvisor.py`.
- Использовать `docker compose restart` вместо `down && up`.

## Migration

- Схема БД не меняется.
- `.env` не меняется.
- Только UI и backend логика.

## Roster

1. `plan` — spec и architecture.
2. `build` — backend (webhook, config, topvisor, deploy.sh).
3. `ui-dev` — Apps Script (provider UI, checkbox).
4. `collector-dev` — расследование depth bug.
5. `reviewer` — security, contracts, scope.
6. `verifier` — acceptance-only.

## Implementation Plan

### Phase 1: Backend (build)

1. **`config.py`:**
   - Добавить `KNOWN_ENDPOINTS` dict.

2. **`webhook.py`:**
   - Добавить `POST /providers/test`.
   - Изменить `POST /providers/register`.

3. **`topvisor.py`:**
   - Расследовать `run_check()` — передаётся ли depth.
   - Fix depth проброс.

4. **`deploy.sh`:**
   - Добавить опцию `--force-recreate`.

5. **Тесты:**
   - `test_webhook.py`: тест `/providers/test`.
   - `test_collector.py`: тест depth проброса.

### Phase 2: UI (ui-dev)

1. **`apps_script.gs`:**
   - Изменить `_addProviderDialog()`.
   - Добавить checkbox поисковиков.
   - Изменить `runCollection()`.

2. **Тесты:**
   - `test_imports.py`: проверка наличия checkbox и provider UI.

### Phase 3: Review & Verifier

1. **Reviewer:** security, contracts, scope.
2. **Verifier:** `python -m pytest -v`, DoD.

### Phase 4: Deploy

1. Commit и push.
2. На сервере:
   ```bash
   cd /root/serp
   docker compose down
   docker compose up -d
   ./verify.sh
   ```

## Approval and Preflight

- План и scope подтверждены пользователем.
- Repo: `/home/rudra/Projects/serp`.
- Branch: `main`.
- Target tag `v1.0.2` свободен.
- Рабочее дерево не содержит чужих dirty changes.
