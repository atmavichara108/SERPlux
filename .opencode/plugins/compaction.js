
// .opencode/plugins/compaction.js
// Memory-management для SERPlux: при компакции сессии флашит ключевые
// выводы в docs/decisions.md и инжектит persistent-context в summary,
// чтобы агент не терял phase/stack/anti-goals после сжатия контекста.
// Адаптировано из dv-hub/.opencode/plugins/compaction.ts (.js формат).

import { appendFileSync } from "node:fs"
import { join } from "node:path"

const DECISIONS_REL = "docs/decisions.md"

// Persistent project context — добавляется в каждый compaction summary.
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
- Ключевые выводы сессии переживают компакцию на диске: docs/decisions.md.
- compaction.js автофлашит compaction summary в раздел «Compaction flush».
- Куратор ADR — вручную выше этого раздела. /dream — финальный flush сессии.
- Восстановить контекст: docs/decisions.md, docs/progress.md, docs/contracts.md.
`.trim()

const stamp = () =>
  new Date().toISOString().replace("T", " ").slice(0, 19)

export default async ({ directory }) => {
  const root = directory || process.cwd()
  const decisionsFile = join(root, DECISIONS_REL)

  return {
    // Fires when opencode compacts the session to free the context window.
    // 1) Flush: дописать compaction summary в docs/decisions.md — ключевые
    //    выводы переживают сжатие на диске, а не только в окне.
    // 2) Inject: добавить persistent context в summary — агент сохраняет
    //    phase/stack/anti-goals после компакции.
    "session.compact": async ({ summary }) => {
      const text = (summary || "").trim()

      // --- flush to disk ---
      try {
        const entry =
          `\n\n## Compaction flush — ${stamp()}\n\n` +
          `> Автосохранение ключевых выводов сессии перед сбросом контекстного окна.\n` +
          `> Это раздел автофлаша (плагин compaction.js). Куратор ADR — выше, вручную.\n\n` +
          `${text}\n`
        appendFileSync(decisionsFile, entry, "utf8")
      } catch (e) {
        // Сбой флаша не должен ломать компакцию.
        console.error("[compaction.js] flush to docs/decisions.md failed:", e?.message || e)
      }

      // --- inject persistent context into summary ---
      return {
        summary: `${summary}\n\n---\n\n${PERSISTENT_CONTEXT}`,
      }
    },
  }
}
