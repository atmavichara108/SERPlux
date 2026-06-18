
// Паттерны файлов с секретами (кроме .example)
const SECRET_FILE = /(\.env(?!\.example)|credentials\.json|service[-_]?account.*\.json|\.pem$|\.key$|\.netrc|secrets?\.(ya?ml|json|toml)|id_rsa|\.pgpass|\.bash_history|\.zsh_history)/i

// bash-команды, читающие/раскрывающие секреты
const SECRET_READ_CMD = /\b(cat|less|more|head|tail|nano|vim|vi|bat|xxd|od|strings)\b[^|;&]*(\.env(?!\.example)|credentials\.json|\.pem|\.key|secrets?\.)/i
const ENV_DUMP_CMD = /\b(env|printenv|export\s*$|set\s*$)\b/

// признаки утечки наружу (exfiltration)
const EXFIL_CMD = /\b(curl|wget|nc|netcat|scp|rsync)\b/i

// "похоже на реальный ключ" — для проверки коммитов
const LOOKS_LIKE_SECRET = /(sk-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_\-]{30,}|[A-Za-z0-9_\-]{32,}=*\s*$)/

export const EnvGuard = async ({ $, client }) => {
  const block = async (msg) => {
    await client.app.log({
      body: { service: "env-guard", level: "warn", message: msg },
    }).catch(() => {})
    throw new Error("EnvGuard: " + msg)
  }

  return {
    "tool.execute.before": async (input, output) => {
      const args = output.args || {}

      // 1. Блок чтения файлов с секретами любым read-инструментом
      if (input.tool === "read") {
        const fp = args.filePath || ""
        if (SECRET_FILE.test(fp)) {
          await block(`чтение файла с секретами запрещено: ${fp}. Используй .env.example`)
        }
      }

      // 2. Блок чтения секретов через bash + dump env + exfiltration
      if (input.tool === "bash") {
        const cmd = args.command || ""

        if (SECRET_READ_CMD.test(cmd)) {
          await block(`команда читает секретный файл: ${cmd}`)
        }
        if (ENV_DUMP_CMD.test(cmd)) {
          await block(`дамп переменных окружения запрещён: ${cmd}`)
        }
        // git add/commit секретных файлов
        if (SECRET_FILE.test(cmd) && /git\s+(add|commit)/.test(cmd)) {
          await block(`секретный файл не должен попадать в git: ${cmd}`)
        }
        // отправка содержимого секретов наружу
        if (EXFIL_CMD.test(cmd) && SECRET_FILE.test(cmd)) {
          await block(`попытка отправить секреты наружу: ${cmd}`)
        }
      }

      // 3. Блок записи реального ключа в код (вместо чтения из .env)
      if (input.tool === "write" || input.tool === "edit") {
        const content = args.content || args.newString || ""
        if (LOOKS_LIKE_SECRET.test(content)) {
          await block(`похоже, в код вписывается реальный ключ. Секреты только через os.environ / .env`)
        }
      }
    },

    // 4. Подстраховка: не дать утечь ключу в webfetch-параметры
    "tool.execute.before.webfetch": async (input, output) => {
      const url = output.args?.url || ""
      if (LOOKS_LIKE_SECRET.test(url)) {
        await block("в URL webfetch похоже на ключ — блокирую")
      }
    },
  }
}
