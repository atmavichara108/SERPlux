#!/usr/bin/env node
// scripts/check-plugins.mjs
// Минимальная проверка загрузки и export-контракта агентных плагинов OpenCode.
// Грузит каждый .opencode/plugins/*.{js,ts} как ESM, проверяет синтаксис
// (import бросает SyntaxError) и наличие экспортированной plugin-функции
// (named или default). Не запускает хуки, не вызывает OpenCode runtime.

import { readdir } from "node:fs/promises"
import { join, extname } from "node:path"
import { pathToFileURL } from "node:url"

const PLUGINS_DIR = new URL("../.opencode/plugins/", import.meta.url)
const EXT = new Set([".js", ".ts"])

let failures = 0

const files = (await readdir(PLUGINS_DIR)).filter((f) => EXT.has(extname(f)))

if (files.length === 0) {
  console.log("no plugin files found in .opencode/plugins/")
  process.exit(0)
}

for (const file of files) {
  const url = pathToFileURL(join(PLUGINS_DIR.pathname, file)).href

  // 1. Синтаксис/загрузка: dynamic import бросает SyntaxError до исполнения.
  let mod
  try {
    mod = await import(url)
  } catch (e) {
    console.log(`FAIL  ${file} — ${e.name}: ${e.message}`)
    failures++
    process.exitCode = 1
    continue
  }

  // 2. Export contract: хотя бы одна функция (named или default).
  const exports = Object.keys(mod)
  const namedFns = exports.filter((k) => k !== "default" && typeof mod[k] === "function")
  const hasDefault = typeof mod.default === "function"

  if (namedFns.length === 0 && !hasDefault) {
    console.log(`FAIL  ${file} — no plugin function exported (named or default)`)
    failures++
    process.exitCode = 1
    continue
  }

  const kind = hasDefault && namedFns.length > 0
    ? `default + named [${namedFns.join(", ")}]`
    : hasDefault
      ? "default"
      : `named [${namedFns.join(", ")}]`
  console.log(`OK    ${file} — ${kind}`)
}

if (failures > 0) {
  console.error(`\n${failures} plugin(s) failed check`)
} else {
  console.log(`\nall ${files.length} plugin(s) loaded and export OK`)
}