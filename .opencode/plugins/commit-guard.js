
// .opencode/plugins/commit-guard.js
// Неотвратимый гейт на уровне рантайма OpenCode: перехватывает `git commit`
// через tool.execute.before, запускает pytest, блокирует коммит если тесты падают.
// Дубль /commit verify-гейта, но работает даже при прямом `git commit` из bash —
// агент не может обойти его, вызвав bash напрямую.

const GIT_COMMIT_CMD = /\bgit\s+commit\b/

export const CommitGuard = async ({ $, client }) => {
  const block = async (msg) => {
    await client.app.log({
      body: { service: "commit-guard", level: "warn", message: msg },
    }).catch(() => {})
    throw new Error("CommitGuard: " + msg)
  }

  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return

      const args = output.args || {}
      const cmd = args.command || ""
      if (!GIT_COMMIT_CMD.test(cmd)) return

      // Запускаем тесты с захватом вывода; nothrow + quiet — не печатаем в TUI
      const result = await $`./venv/bin/python -m pytest -q --tb=short`.nothrow().quiet()
      const output = (result.stdout?.toString() || "") + (result.stderr?.toString() || "")

      if (result.exitCode !== 0) {
        // FAIL: полный вывод — в структурный лог, в TUI — краткое сообщение
        await client.app.log({
          body: { service: "commit-guard", level: "error", message: `Tests failed:\n${output}` },
        }).catch(() => {})
        throw new Error("CommitGuard: ❌ Tests FAILED, cannot commit")
      }

      // PASS: парсим количество тестов (пример: "64 passed")
      const match = output.match(/(\d+)\s+passed/)
      const passedCount = match ? match[1] : "?"
      await client.app.log({
        body: { service: "commit-guard", level: "info", message: `✅ ${passedCount} tests passed` },
      }).catch(() => {})
      // В TUI — только короткий итог, без висящих строк pytest
      console.log(`✅ ${passedCount} tests passed`)
    },
  }
}
