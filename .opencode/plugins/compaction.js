
// .opencode/plugins/compaction.js
// Memory-management для SERPlux: инжектит persistent-context в compaction
// prompt через единственный документированный хук experimental.session.compacting,
// чтобы агент не терял phase/stack/anti-goals после сжатия контекста.
// Контракт: @opencode-ai/plugin Hooks["experimental.session.compacting"]
//   (input: { sessionID }, output: { context: string[]; prompt?: string })
// output.context.push(...) добавляет контекст к дефолтному compaction prompt;
// output.prompt (если задан) полностью заменяет его.
// session.compact в SDK отсутствует; session.compacted — post-event с payload
// { sessionID } без текста summary, flush summary-text через plugin hooks
// SDK не поддерживается.

// Persistent project context — добавляется в compaction prompt.
// Держим актуальным: phase/stack/контракты/anti-goals переживают компакцию.
const PERSISTENT_CONTEXT = `
# SERPlux — Persistent Context (injected on compaction)

## Phase
Core ✅, Docker ✅, Deploy ✅. Приоритет: мультиклиентность + мультипровайдерность.
Web UI ⏸ приостановлено (ADR 2026-07-02: единственный UI = Google Sheets).

## Stack (не менять без явного указания)
- Python 3.11+, requests (Topvisor API), gspread (Sheets), FastAPI (webhook)
- DeepSeek через opencode.ai/zen (разметка тональности, OPENCODE_API_KEY)
- SQLite (кэш, история, профили), Docker + docker-compose
- Интерфейс: Google Sheets (Apps Script меню + лист «Настройки»)

## Контракты модулей (СТРОГО соблюдать)
topvisor.py → run_check / poll_status / get_snapshot
collector.py → collect(config) → list[Row]
labeler.py → label(rows, mode) → rows c label (сначала кэш, потом LLM)
storage.py → save / get_cached_label / get_history
exporter.py → export(rows) → Sheets с цветовой разметкой
reporter.py → матрица-отчёт в Sheets
webhook.py → FastAPI: /health, /status, /run
config.py → читает настройки из листа «Настройки»
Row = {date, searcher, query, geo, region_index, position, url, domain, snippet, label}

## Anti-goals (не предлагать)
- Парсить Google/Яндекс напрямую (источник только Topvisor)
- SPA-фреймворки (React/Vue/Angular) — только Jinja2 + Tailwind + Vanilla JS
- «Расширенный» LLM-режим — только дешёвый DeepSeek через Zen
- Хардкодить секреты — только через .env

## Workflow rules
- Секреты только в .env (Topvisor, Google SA, OPENCODE_API_KEY, WEBHOOK_SECRET)
- После значимого изменения: docs/progress.md (статус), docs/decisions.md (ADR)
- Код на английском, общение/комментарии на русском, коммиты на английском
- Логирование через logging, не print

## Текущие агенты
build (Kimi K2.7 Code), plan (GLM-5.2), collector-dev, reviewer (GLM-5.2),
ui-dev (⏸ paused), infra-dev (Qwen 3.7 Plus).

## Память (memory-management)
- Persistent context инжектится в compaction prompt плагином compaction.js
  (хук experimental.session.compacting) — phase/stack/anti-goals переживают компакцию.
- Flush текста compaction summary на диск SDK-хуками не поддерживается
  (session.compacted event carries only sessionID). Куратор ADR — вручную в docs/decisions.md.
- /dream — финальный flush сессии. Восстановить контекст: docs/decisions.md, docs/progress.md, docs/contracts.md.
`.trim()

export default async ({ directory }) => {
  return {
    // Единственный документированный compaction-хук: experimental.session.compacting.
    // Fires до генерации LLM continuation summary. output.context.push(...)
    // добавляет persistent context к дефолтному compaction prompt — агент
    // сохраняет phase/stack/anti-goals после компакции.
    "experimental.session.compacting": async (input, output) => {
      try {
        output.context.push(PERSISTENT_CONTEXT)
      } catch (e) {
        console.error("[compaction.js] inject persistent context failed:", e?.message || e)
      }
    },
  }
}
