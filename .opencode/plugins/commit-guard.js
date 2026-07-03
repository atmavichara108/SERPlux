
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

      // Запускаем тесты; nothrow — сами проверяем exit code
      const result = await $`./venv/bin/python -m pytest -q`.nothrow()

      if (result.exitCode !== 0) {
        await block("Tests failed, cannot commit")
      }
      // PASS → пропускаем вызов дальше
    },
  }
}
