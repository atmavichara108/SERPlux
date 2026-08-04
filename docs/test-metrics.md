---
type: Test Metrics Contract
title: "SERPlux — нормализация тест-метрик (T-087)"
date: 2026-08-04
status: canonical
author: librarian (T-087/T-098, read-only probe + артефакт; правки docs — через general)
branch: fix/labeling-cache-and-quality
head: f7ccd3e
head_commit_ts: 2026-08-04
probe_ts: 2026-08-04T18:09:05Z
---

# Test Metrics Contract — SERPlux (T-087)

## 1. Назначение

Единый source-of-truth по тест-метрикам проекта. Существующие claims в
README/AGENTS/CANON/verification/release-1.0/progress/TASKS — **исторические**
и не актуализируются массовым рефакторингом (см. раздел 4 и `docs/techdebt.md`,
запись 2026-08-04). Любое расхождение разрешается против этого документа:
канон — executed/collected count на коммите HEAD, не текст старых docs.

## 2. Схема метрик (определения)

| Метрика | Определение | Как измерять |
|---|---|---|
| **test definitions** | Число `def test_*` в `tests/` (статика) | `rg -c "^\s*def test_" tests/` и сумма |
| **collected pytest cases** | Число, которое `pytest` собрал без ошибок collection | `./venv/bin/python -m pytest --collect-only -q` |
| **executed passed / failed / skipped** | Из прогона `pytest` (не collection) | `./venv/bin/python -m pytest -q` |
| **documented suite claim** | Число в doc/AGENTS/memo — носитель, не факт | grep по docs; к канону не относится |

Канон приоритетов: `executed > collected > test definitions > documented claim`.
`documented claim` **никогда** не источник истины.

## 3. Фактические значения

### 3.1 Коммит HEAD f7ccd3e (canonical, executed) — CANONICAL

- branch: `fix/labeling-cache-and-quality`
- head: `f7ccd3e` (`fix(storage): simplify geo normalization to strip+lowercase, remove GEO_DISPLAY mapping`)
- head_commit_ts: 2026-08-04
- окружение: `venv/` (Python 3.14.x, deps ok)
- **executed** = `./venv/bin/python -m pytest -q --tb=short` →
  **256 collected, 256 passed, 0 failed, 0 skipped, 0 errors, exit 0** (3.52s)
- **collected pytest cases = 256**, collection errors = 0
- **test definitions (rg) = 212**

### 3.2 Исторические замеры (справочно, не канон)

- `ee28637` (2026-08-03, pre-WIP clean HEAD): collected = 248, definitions (rg) = 204.
- `d21a770` (2026-08-02, review doc): executed 245 passed in 3.16s.
- WIP-запись «254/254 passed» (2026-08-04) — была UNVERIFIED до merge;
  после merge WIP в `f7ccd3e` superseded: executed = 256/256 (см. 3.1).

## 4. Documented suite claims — реестр устаревших (не правятся; sync → techdebt)

| Файл | Claim | Канон (HEAD f7ccd3e) | Статус |
|---|---|---|---|
| `README.md` | 224/224 | 256 executed | STALE |
| `AGENTS.md` (таблица команд) | 224 | 256 executed | STALE |
| `docs/CANON.md` | 224 passed | 256 executed | STALE |
| `docs/release-1.0.md` | 224 | 256 executed | STALE |
| `docs/user-guide.md` | 224 | 256 executed | STALE |
| `docs/verification.md` | 172 | 256 executed | STALE |
| `docs/progress.md` (история) | 224 | 256 executed | STALE (история) |
| `TASKS.md` | 95 / 111 | 256 executed | STALE |
| «grep test definitions = 94» (T-087 brief) | 94 | definitions = 212 (rg) | UNTRACEABLE → метод устарел, исключён |

«STALE» не означает баг для исторических записей (`progress.md`, `TASKS.md`,
`release-1.0.md`) — они фиксируют состояние на свою дату. Багом является
**текущее** утверждение `224`/`172`/`95`/`111` в живых docs
(README/AGENTS/CANON/verification/user-guide/TASKS).

## 5. Open

1. Синхронизация живых claims под канон — **записана техдолгом** в
   `docs/techdebt.md` (запись 2026-08-04), реализация — за пользователем
   при проектной работе в репо (не librarian, не build-агент по умолчанию).
2. ~~Зафиксировать executed на HEAD~~ — **ЗАКРЫТО** 2026-08-04: executed
   256/256 на HEAD `f7ccd3e` (раздел 3.1).
3. «grep test definitions = 94» — **ЗАКРЫТО**: источник/метод утерян,
   метрика переведена на `rg`-naming, канон = 212 (HEAD f7ccd3e).
4. Ресёрч-топик (diff `d21a770` → `ee28637` → `f7ccd3e`) — закрыт
   фактическим executed-прогоном на `f7ccd3e`.

## 6. Что НЕ делалось в T-087/T-098

- Правки application code (`*.py`, `*.gs`, prod-конфиги) — нет.
- Правки существующих doc claims (README/AGENTS/CANON/verification/user-guide/TASKS) — нет
  (sync записан техдолгом, см. раздел 5 п.1).
- Правки vault / VibeOS / 02-Methods — нет.