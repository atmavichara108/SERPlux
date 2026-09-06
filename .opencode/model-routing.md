---
type: Agent Instructions
title: SERPlux Model Routing Policy
description: Risk-based model selection for SERPlux agents. Разделяет capability-routing и model-routing.
timestamp: 2026-09-04
---

# SERPlux Model Routing Policy

## Принцип

**Capability-routing** определяет *кто* делает задачу (role/agent).
**Model-routing** определяет *какая модель* используется для роли.

Они ортогональны: смена модели не меняет capability, authority или acceptance contract.

## Risk-based model selection

### Luna (`opencode-go/gpt-5.6-luna`, ~$0.01/1K токенов)
**ТОЛЬКО** для strict/complex задач:
- Архитектурные решения и дизайн
- Миграции БД и schema changes
- Concurrency и асинхронность
- Deployment и rollback
- Сложные алгоритмы и edge cases

### DeepSeek Flash-Free (`opencode-go/deepseek-v4-flash`, $0)
**Для всего остального:**
- Navigation (grep, find, read files)
- Docs и контракты чтение
- Простой анализ и рефакторинг
- Review и verifier-style проверки
- Routine implementation (boilerplate, tests, config)

## Модели по ролям

| Role | Default Model | Luna (strict/complex only) |
|------|--------------|---------------------------|
| **plan** (primary) | `opencode-go/deepseek-v4-flash` | Архитектура, миграции, deployment |
| **build** (primary) | `opencode-go/deepseek-v4-flash` | Архитектура, миграции, concurrency |
| **collector-dev** (subagent) | `opencode-go/gpt-5.6-luna` | Специализированная роль — всегда Luna |
| **ui-dev** (subagent) | `opencode-go/gpt-5.6-luna` | Специализированная роль — всегда Luna |
| **infra-dev** (subagent) | `opencode-go/qwen3.7-plus` | Специализированная роль — всегда Qwen |
| **reviewer** (subagent) | `opencode-go/deepseek-v4-flash` | — |
| **verifier** (subagent) | `opencode-go/deepseek-v4-flash` | — |
| **commit** (command) | `opencode-go/deepseek-v4-flash` | — |
| **push** (command) | `opencode-go/deepseek-v4-flash` | — |

## Model profiles (Zen ↔ Go)

Переключение между провайдерами через `provider.variants` в `opencode.json`:

```json
"provider": {
  "opencode": {
    "models": {
      "gpt-5.6-luna": {
        "variants": {
          "free": { "model": "openrouter/glm-5.2:free" },
          "medium": { "model": "opencode-go/gpt-5.6-luna" },
          "premium": { "model": "openrouter/openrouter/auto" }
        }
      }
    }
  }
}
```

Переключение: `Ctrl+Shift+V` (variant_cycle keybind).

## Capability → Role mapping

| Capability | Role | Model Policy |
|-----------|------|-------------|
| `read-research` | `researcher` | DeepSeek Flash-Free |
| `quality-review` | `reviewer` | DeepSeek Flash-Free |
| `acceptance-verification` | `verifier` | DeepSeek Flash-Free |
| `meta-infrastructure` | `meta` | DeepSeek Flash-Free (default), Luna (strict) |
| `vault-coordination` | `librarian` | DeepSeek Flash-Free |
| `implementation` | `build` | DeepSeek Flash-Free (default), Luna (strict/complex) |
| `planning` | `plan` | DeepSeek Flash-Free (default), Luna (strict/complex) |
| `collector-dev` | `collector-dev` | Luna (always) |
| `ui-dev` | `ui-dev` | Luna (always) |
| `infra-dev` | `infra-dev` | Qwen3.7 Plus (always) |

## Missing capability → UNROUTABLE

Если capability не в registry — `UNROUTABLE`, без silent fallback на `general`.
