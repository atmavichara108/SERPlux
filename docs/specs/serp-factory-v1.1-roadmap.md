# SERPlux Factory v1.1 Roadmap

> **AUTHORITATIVE LOCAL EXECUTION SPEC.** Единственный authoritative spec для
> согласованного v1.1 scope находится в `docs/specs/` репозитория SERPlux.
> Этот документ описывает будущую реализацию и не является evidence её
> выполнения.

## 1. Цель и статус

Цель v1.1 — превратить текущий однопроходный pipeline в управляемую фабрику:
проверять LLM-метки против ручного эталона, наблюдать и контролировать каждый
run, дать оператору безопасный Telegram-контур, перейти на immutable release и
выбрать server provider по проверяемым данным.

Этот spec является единым master scope для пяти app acceptance surfaces (A–E)
+ agent-layer workstreams F/G и permission baseline (Section 11). Отдельные
дочерние specs не создаются: все поверхности связаны одним run/release
контрактом и имеют общий порядок, rollback и gates.

`Dynamic Etalon Import v1.0.2` уже approved и не перезаписывается, не
переформулируется и не включается в этот scope как повторная работа. Новый
validator потребляет его записи `domain_labels` с `source=manual_l1` и сохраняет
совместимость с ключом `(domain, query)` без `geo` и `client_id`.

## 2. Неподвижные границы

### In scope

- Контракт validator-а ручного эталона и диагностика конфликтов.
- Персистентные run identity, state machine, stage progress, heartbeat, events
  и watchdog.
- Защищённый Telegram operator control plane.
- Tag/SHA-based deployment, migration preflight, version/health/smoke и
  rollback contract.
- Исследование и selection server provider по official sources.
- Необходимые ADR и targeted tests, перечисленные в этом spec.

### Out of scope

- Перезапись или повторная реализация `dynamic-etalon-v1.0.2.md`.
- Deep/page labeling, смена LLM-провайдера, geo dedup и новая модель
  client-isolation, если они не нужны для явно указанного контракта.
- Изменение Google Sheets geometry и автоматизация `onEdit`.
- Платные действия, production deploy, commit, tag или push в рамках этого
  authoring scope.
- Автоматизация дешёвого commit/push.

Дешёвый commit/push — отдельный **agent-layer concern** (см. Workstream F).
App spec может предусмотреть только интерфейсные предположения: release
identity доступен workflow-агенту, а commit/push выполняется только отдельным
согласованным workflow. Логика git automation, credential handling и approval
этого агента не смешивается с application implementation.

## 3. Shared contracts

### 3.1 Identity and audit

- Каждый run получает криптографически случайный, непереиспользуемый `run_id`.
- `run_id` проходит через API, storage, logs, events, Telegram и deployment
  evidence; отсутствие `run_id` в изменяющем операцию событии — ошибка.
- Время хранится в UTC ISO-8601; сортировка событий имеет `(occurred_at,
  sequence)`.
- Audit event содержит `event_id`, `run_id` или `null` для operator/config
  событий, actor, action, outcome, redacted metadata и timestamp. Секреты,
  bearer tokens и полный prompt/response в audit не попадают.

### 3.2 Compatibility

- Существующие `/run`, `/status`, `/health` не ломаются без отдельного
  approval; новые поля additive, deprecated behavior документируется.
- SQLite остаётся первым persistence boundary. Redis, Celery, Kafka, внешний
  queue и distributed lock не входят в v1.1.
- Existing `domain_labels` remains `(domain, query)`, with separate SQLite DB
  per client as documented in `CANON.md`.

## 4. Workstream A: Etalon validation

### Scope

Добавить deterministic validation layer вокруг текущего `labeler` pipeline.
Источник истины для ручной разметки — только импортированный/manual L1 etalon
из Dynamic Etalon Import v1.0.2 (`source=manual_l1`). Validator должен уметь
объяснить, почему URL получил `positive`, `negative` или `neutral`, особенно
для жёлтых/neutral результатов.

### Required flow

1. Normalize URL to `domain` and query to canonical lowercase subject key using
   the existing normalization rules.
2. **Pre-LLM lookup:** query `(domain, query)` in `domain_labels` before any LLM
   call. A `manual_l1` hit is a hard reference and has zero LLM cost.
3. If no manual hit exists, run the existing snippet LLM path under its normal
   fallback behavior, recording source/provenance as runtime output, not as a
   new manual etalon.
4. **Post-label validator:** compare produced sentiment, provenance, confidence,
   URL/query key and expected manual value. Emit a structured validation result;
   do not silently rewrite the label.
5. Produce a conflict report with at least: `run_id`, URL/domain, query, geo,
   searcher, position, observed label, manual label (if any), source,
   confidence, conflict type, and recommended action.

### Neutral/yellow diagnosis

`neutral` is not automatically a valid manual conclusion. The report MUST
distinguish:

- `manual_neutral`: manual L1 explicitly says neutral;
- `unmatched_neutral`: no manual key and automatic fallback/LLM produced
  neutral;
- `manual_conflict`: automatic output differs from manual L1;
- `invalid_or_unknown`: missing key, unsupported value, malformed URL or
  unparseable provider result.

The validator must preserve the original snippet, URL and context needed for
human diagnosis, but must not fetch page content in this workstream.

### Precedence rules

1. Valid `manual_l1` wins over snippet/page/LLM output for the effective report
   label, and is never overwritten by automation.
2. A manual conflict is reported even when manual L1 wins; hiding the conflict
   is forbidden.
3. Missing manual L1 never becomes a synthetic manual entry.
4. Automatic `neutral` caused by empty snippet/provider failure is
   `confidence=uncertain` and remains diagnostically distinct from manual
   neutral.
5. `force_relabel` may bypass an automatic cache only; it cannot bypass a
   valid manual L1 precedence rule.
6. Conflicting manual imports for the same normalized key are a blocking import
   error requiring explicit operator resolution; last-write-wins is forbidden.

### DoD

- Every label attempt has a deterministic pre-lookup and post-validation result.
- A manual L1 hit causes no LLM invocation and controls the effective label.
- Yellow/neutral output is classified into the diagnosis categories above.
- Conflict report is stable, exportable and linked to `run_id`.
- Invalid keys/values are rejected or reported without corrupting etalon data.
- Tests cover cache hit/no LLM, missing etalon, manual-vs-LLM conflict, empty
  snippet, provider failure, duplicate manual conflict and idempotent rerun.
- Documentation explicitly links this layer to
  `dynamic-etalon-v1.0.2.md`; that file is unchanged.

### Risks and rollback

- Risk: a bad imported manual value suppresses a correct automatic result.
  Mitigation: immutable audit/conflict report, preview and explicit manual
  correction workflow; never silently downgrade provenance.
- Risk: neutral fallback is mistaken for customer-approved neutral. Mitigation:
  separate status and confidence fields.
- Rollback: disable validator enforcement while retaining read-only reports;
  restore the client DB from a pre-change backup only if data was mutated.
  Never delete or bulk-rewrite `manual_l1` without a verified backup.

## 5. Workstream B: Run observability/control plane

### State machine

The canonical states are:

`queued -> starting -> collecting -> labeling -> validating -> exporting ->
reporting -> succeeded`

Terminal/error states are `cancelled`, `failed`, and `stalled`. Only these
transitions are legal:

- `starting` may enter `cancelled` before work begins;
- each active stage may enter its next stage, `cancelled`, `failed`, or
  `stalled`;
- terminal states are immutable except an operator-created retry, which creates
  a new `run_id` and records `retry_of`;
- watchdog may move an active state to `stalled`, never directly to
  `succeeded`.

The state transition function must reject illegal transitions atomically and
write an event for both accepted and rejected attempts.

### Required data

SQLite-first implementation adds a normalized run record (one row per run),
stage progress records and append-oriented event records. Minimum fields:

- run: `run_id`, client/config fingerprint, requested_at, started_at,
  finished_at, state, current_stage, retry_of, cancellation_requested,
  error_code/message, version identity;
- stage: `run_id`, stage name, state, started/finished timestamps, completed,
  total, percent, counters and redacted detail;
- heartbeat: last monotonic/UTC update, owner/process identity and liveness
  evidence;
- event: sequence, transition/action, actor, outcome and redacted payload.

No unbounded payloads or secrets are stored in SQLite. Event retention and
indexing must be specified before migration.

### Watchdog and control operations

- Heartbeat is emitted at a bounded interval during every active stage.
- Watchdog detects stale heartbeat using a documented threshold, writes one
  idempotent `stalled` event and never races a newer heartbeat into a false
  stall.
- `cancel` is cooperative, idempotent and visible in status; it must not claim
  success when work could not be stopped.
- `retry` creates a new run and preserves the failed run/event history.
- A process restart reconstructs current status from SQLite without in-memory
  singleton state.

### API and web status window

Existing status remains available. New API surface must specify authentication,
pagination and redaction for run list/detail, events and control actions.

The **web status window is a new explicit requirement**, not an assumed
extension of Google Sheets. It requires a separate ADR and explicit approval
before implementation. The ADR must decide exposure/authentication, browser
session/CSRF model, polling or streaming, data redaction, operator roles and
whether it is enabled in production. No web UI code may be implemented before
that ADR is approved.

### DoD

- Unique run IDs, legal state transitions, stage progress and heartbeats are
  persisted and survive restart.
- Event log is append-oriented, ordered, redacted and queryable by run.
- Watchdog deterministically marks stale runs `stalled` exactly once.
- Status returns current stage/progress and terminal outcome; cancel/retry are
  idempotent and audited.
- SQLite-first boundary is enforced; no unapproved queue/cache dependency.
- Web status window ADR is approved before any implementation of that surface.
- Tests cover transition matrix, restart reconstruction, watchdog race,
  cancellation, retry linkage, pagination and secret redaction.

### Risks and rollback

- Risk: migration or watchdog marks a healthy long-running Topvisor job stale.
  Mitigation: heartbeat at stage boundaries and during external polling, a
  conservative threshold, and evidence-based dry run.
- Risk: event growth harms SQLite. Mitigation: retention/index policy and
  bounded payloads before production enablement.
- Rollback: disable watchdog/control actions and fall back to read-only status;
  preserve event rows. Restore DB only after backup and integrity verification.

## 6. Workstream C: Telegram control plane

### Scope and commands

Telegram is an operator adapter over the run control plane, not a second job
engine. It must maintain one live status message per active run, editing it on
stage/progress changes with rate limiting and a final immutable summary.

Read-only commands:

- `/status [run_id]` — current state, stage, progress, last heartbeat and safe
  error summary;
- `/runs` — bounded recent run list;
- `/help` — allowed commands and safety semantics.

Mutating commands:

- `/run` — create a new run from approved settings;
- `/cancel <run_id>` — request cooperative cancellation;
- `/retry <run_id>` — create a new linked run after terminal failure/stall;
- `/settings` — read settings by default; any mutation must use an explicit
  subcommand, validation and confirmation.

Exact arguments, defaults and response schemas are implementation contract
artifacts and must be frozen before build. Telegram cannot bypass API auth,
state transitions or manual L1 precedence.

### Security and audit

- Allowlist bot, chat and operator identity; reject unknown chat/user IDs.
- Secret/token is environment-backed and never printed, persisted in messages or
  audit payloads.
- Mutating actions require explicit confirmation (button or exact confirmation
  token bound to actor, run and short expiry). `/cancel` and `/retry` are never
  implicit from a status command.
- Replay, duplicate delivery and stale confirmation are idempotently rejected.
- Every command, authorization decision, confirmation and resulting state/event
  is audited with redaction.
- Telegram API failures cannot change run state or report false success.

### DoD

- Live status message is correct after stage transitions, restart and terminal
  completion, with bounded edit frequency.
- Read-only commands expose no mutation and redact secrets/error internals.
- Run/cancel/retry/settings enforce auth, confirmation, idempotency and audit.
- Unknown users/chats, replayed confirmations and Telegram outages are tested.
- Bot adapter calls the shared control-plane service rather than duplicating
  storage/state logic.

### Risks and rollback

- Risk: Telegram message edits lag or fail. Mitigation: status API remains
  authoritative and final delivery is retried without changing run state.
- Risk: leaked credentials or sensitive URLs. Mitigation: allowlist,
  redaction, short messages and security review.
- Rollback: disable bot webhook/poller and revoke bot token; runs continue via
  authenticated API/status. Keep audit evidence.

## 7. Workstream D: Immutable deployment and rollback

### Release identity

Replace the current `git pull origin main` plus `serplux:latest` behavior with a
release identified by an immutable Git tag and commit SHA, and an image tag or
digest tied to that same release. Deployment must fail closed if the requested
identity is missing, ambiguous or differs between repository, image and running
container.

The deployment record includes release tag, full commit SHA, image digest,
previous release identity, operator, timestamps and check results. Mutable
`latest` is not a production selector.

### Preflight and order

1. Capture repo/branch/status/HEAD, requested release identity and ownership of
   pre-existing changes; dirty or mismatched state is `BLOCKED`.
2. Verify tag and commit ancestry/signature policy as approved by the project.
3. Verify image reference/digest and configuration presence without printing
   secrets.
4. Create and verify a SQLite backup before migration or data-affecting change.
5. Run migration preflight on a copy: schema compatibility, idempotency,
   foreign-key/integrity checks, expected row counts and rollback rehearsal.
6. Stop before apply if backup, migration dry-run or release identity check
   fails.
7. Apply the release, run migration only according to the approved order, then
   health and smoke checks.
8. Verify `/version` returns the expected tag/SHA/image identity and status/run
   APIs remain authenticated and functional.

### Health, smoke and rollback contract

Health is liveness/readiness, not proof of application correctness. Smoke must
cover version identity, authenticated status, a safe isolated run/control path,
DB integrity and the no-secret response/log policy.

Rollback is a documented, operator-confirmed operation:

- select the previous immutable release identity;
- stop new runs and record the incident;
- restore the DB backup only when migration/data compatibility requires it;
- deploy the previous image/tag, verify `/version`, health and smoke;
- confirm old run/event data and manual L1 rows are intact;
- record result; never use force-push, tag movement or unreviewed destructive
  SQL as rollback.

If migration is not backward-compatible, deployment must be blocked until a
restore rehearsal and compatibility strategy are approved. A failed health
check never counts as a successful deploy.

### DoD

- No production path pulls `main` or selects `latest`.
- Tag/SHA/image identity is checked before and after deployment and exposed by
  `/version` without secrets.
- Backup, migration preflight, integrity verification, health and smoke are
  deterministic and evidenced.
- Rollback to the prior immutable release is documented, tested on a copy and
  operator-confirmed.
- Tests cover mismatched identity, failed preflight, migration failure, health
  failure and successful/failed rollback paths.

### Risks and rollback

- Risk: schema migration succeeds but new code is incompatible with old data.
  Mitigation: copy-based preflight and compatibility gate.
- Risk: backup is present but unusable. Mitigation: restore rehearsal and
  integrity checks, not merely file existence.
- Rollback itself is the controlled procedure above; do not delete production
  data or move tags.

## 8. Workstream E: Server-provider research and selection

### Research rules

This is a decision artifact, not an invitation to invent infrastructure facts.
For every candidate, cite official provider sources for region, compute/storage,
network, security controls, backup/snapshot, support, terms and migration
mechanics. Prices may be stated only when captured from an official current
pricing page with currency, unit, region, date/time checked and assumptions.
Otherwise record `UNKNOWN`, never an estimate presented as fact.

Compare candidates using a fixed matrix:

- total cost of ownership and billing unit;
- CPU/RAM/disk/network performance relevant to SQLite, Docker, Topvisor and
  LLM-bound workloads;
- region/latency, availability, support and incident history available from
  official sources;
- security: IAM, firewall, encryption, backups, auditability and access model;
- migration: image portability, volume export/import, DNS/IP implications,
  restore time and exit constraints;
- operational fit: Docker support, monitoring, snapshot/backup automation and
  resource headroom.

Evidence must include retrieval date and URL. Unverified community claims may
  be listed only as questions, not selection evidence. No provider is selected
  until the matrix has no unresolved P0 security or migration blocker and the
  user explicitly approves the decision.

### DoD

- At least the agreed candidate set is compared from official sources.
- Every score has a cited source or is marked `UNKNOWN`.
- Costs include assumptions and are never based on unverified prices.
- Recommendation includes rejected alternatives, security/migration risks and
  a reversible migration plan.
- User approval of provider selection is recorded separately from app build
  approval.

### Risks and rollback

- Risk: pricing or limits change. Mitigation: date-stamped sources and recheck
  immediately before purchase/deploy.
- Risk: performance benchmark is synthetic. Mitigation: label benchmark
  assumptions and run a non-production acceptance test before migration.
- Rollback: retain the current provider and exportable backups until the new
  provider passes smoke, restore and observation window gates; migration is not
  irreversible by selection alone.

## 9. Workstream F: Экономичный агентский execution layer

### Scope

Определить и зафиксировать agent-layer execution model, минимизирующий стоимость
и латентность агентских операций без потери safety gates. Этот workstream не
меняет application code; он описывает контракт между агентной инфраструктурой и
pipeline-ом v1.1.

### Risk-based model routing

- **Cheap/free tier:** navigation, docs lookup, simple analysis, reviewer,
  verifier и другие read-only или deterministic задачи выполняются на
  дешёвых/free моделях по умолчанию.
- **Luna (strict/complex):** дорогая модель используется только для задач,
  требующих строгого reasoning, complex multi-step planning или domain-specific
  expertise, где cheap tier доказанно недостаточен.
- Маршрутизация определяется risk/complexity классификацией, а не hardcoded
  agent name. Один и тот же agent может использовать разные модели в зависимости
  от task class.

### Capability-routing и model-routing

Capability-routing (named capabilities, `UNROUTABLE`) ортогонален model-routing.
Capability определяет ЧТО делается и КАКОЙ agent/class; model-routing определяет
НА КАКОЙ модели. Если capability не найден — `UNROUTABLE`, без silent fallback.

### Named execution pipeline

Канонический pipeline: `plan → build → reviewer → verifier`.

- **plan:** декомпозиция, scope, route, risks. Read-only, cheap tier.
- **build:** implementation по подтверждённому scope. Может использовать Luna
  для complex steps.
- **reviewer:** contract/scope/security/safety check. Read-only, cheap tier.
- **verifier:** DoD acceptance, PASS/FAIL, no edits. Cheap tier.

Каждый stage имеет явный entry/exit criterion. Переход между stages требует
evidence прохождения предыдущего.

### Low-cost commit/push workflow

Commit/push automation — agent-layer concern, описанный здесь, а не в app spec.

- **Короткий preflight:** `git status`, `git diff --check`, targeted tests
  (только затронутые модули), conventional commit message.
- **Отдельный explicit commit/push gate:** commit и push требуют отдельного
  подтверждения пользователя, не implied spec approval или verifier PASS.
- **Без force/arbitrary git allow:** `--force`, `--force-with-lease`, arbitrary
  `git allow` не разрешены. Только fast-forward или explicit merge/rebase с
  подтверждением.
- Conventional commit format: `type(scope): description`.

### DoD

- Risk-based routing policy documented с классификацией task classes.
- Capability-routing и model-routing разделены; `UNROUTABLE` обрабатывается
  явно.
- Named pipeline `plan → build → reviewer → verifier` enforced с entry/exit
  criteria.
- Commit/push workflow имеет preflight, targeted tests, conventional commit и
  explicit gate.
- No force-push, no arbitrary git allow в любом agent workflow.
- Tests cover misrouting (cheap for complex task), UNROUTABLE handling,
  preflight failure, conventional commit validation.

### Risks and rollback

- Risk: cheap tier недостаточен для complex task, producing low-quality output.
  Mitigation: reviewer/verifier catch quality regression; escalation to Luna.
- Risk: commit/push automation bypasses safety. Mitigation: explicit gate,
  preflight, no force-push.
- Rollback: revert to manual commit/push; agent-layer routing policy can be
  disabled without affecting app code.

## 10. Workstream G: Read-once / context efficiency

### Scope

Минимизировать дублирующиеся чтения файлов и tool calls в рамках сессии и между
сессиями, сохраняя корректность и safety. Этот workstream описывает agent-layer
protocol, не application behavior.

### Session read-ledger

Каждая сессия ведёт read-ledger:

- **path:** абсолютный путь к прочитанному ресурсу.
- **hash_or_mtime:** hash содержимого или mtime на момент чтения, для
  определения изменения.
- **purpose:** зачем читался (scope, context, verification, etc.).
- **summary:** краткое извлечение, достаточно для последующих ссылок без
  повторного чтения.

Повторное чтение того же path разрешено только при:

- изменении `hash_or_mtime` (файл изменился);
- конфликте (другой agent/step изменил содержимое);
- acceptance gate (verifier/reviewer требует re-read для подтверждения).

### Handoff / context capsule

При handoff между agents/steps передаётся context capsule:

- summary прочитанных ресурсов (из read-ledger);
- текущий scope и route;
- blockers и open questions;
- evidence progress.

Capsule не содержит secrets, full prompts или полных содержимых файлов — только
summary и ссылки.

### Startup memory read

- Новая сессия читает memory (04-Memory/, project context) один раз при
  startup.
- Повторное чтение memory в той же сессии — только при изменении или явном
  конфликте.
- Memory read не включает secrets или full prompts.

### Replay-set acceptance

Replay-set — набор duplicate reads/tool calls, которые могут быть приняты или
отклонены:

- Duplicate read того же path без изменения `hash_or_mtime` — reject (используй
  summary из ledger).
- Duplicate tool call с тем же input — reject, используй cached result.
- Acceptance допускается при явном обосновании (conflict resolution, verification
  gate).

### DoD

- Read-ledger protocol documented с path/hash_or_mtime/purpose/summary.
- Re-read policy enforced: only on change, conflict or acceptance gate.
- Context capsule defined для handoff; no secrets/full prompts in capsule.
- Startup memory read происходит один раз per session.
- Replay-set acceptance logic tested: duplicate reads/tool calls rejected by
  default.
- Tests cover ledger hit/miss, hash change detection, capsule content
  validation, replay-set reject/accept.

### Risks and rollback

- Risk: stale summary в ledger приводит к incorrect decisions. Mitigation:
  hash_or_mtime check перед использованием summary.
- Risk: capsule слишком велик, negating efficiency. Mitigation: summary-only,
  no full contents.
- Rollback: disable read-ledger и replay-set; revert to unrestricted reads.
  No app code change required.

## 11. Agent-layer permission baseline

### Разрешены (safe read-only / non-destructive)

- `read`: чтение файлов в scope.
- `glob`: поиск файлов по паттерну.
- `grep`: поиск содержимого.
- `list`: чтение директорий.
- `external_directory`: доступ к разрешённым внешним директориям
  (workspace, vault, project roots).
- `git preflight`: `git status`, `git diff`, `git log`, `git diff --check`,
  `git branch`, `git tag` — read-only git операции.

### Gated (требуют явного подтверждения)

- `edit`: изменение файлов.
- `task`: dispatch subagent.
- `commit`: создание commit.
- `push`: push в remote.
- `destructive git`: `git reset --hard`, `git clean -fd`, `git checkout --`,
  branch deletion, tag deletion.
- `HITL`: human-in-the-loop confirmation для любого действия вне agreed scope.

### Принципы

- Read-only операции не требуют подтверждения в рамках agreed scope.
- Mutating операции всегда gated, даже внутри scope.
- Commit/push имеют отдельный explicit gate (Workstream F).
- Destructive git операции запрещены по умолчанию; требуют отдельного
  подтверждения и обоснования.
- Permission baseline применяется ко всем agents; agent-specific rules могут
  ужесточать, но не ослаблять.

## 12. Cross-workstream order

1. Определить agent-layer permission baseline (Section 11) и зафиксировать
   routing policy (Workstream F) до начала app workstreams.
2. Определить read-ledger protocol и context capsule (Workstream G) для
   эффективности всех последующих agent operations.
3. Preflight repo, branch, HEAD, tags, status and ownership; do not include
   pre-existing changes.
4. Freeze shared schemas and state/event contracts; produce ADR for web status
   window and any migration/rollback decision.
5. Implement etalon validator (A) and deterministic reports against the approved
   Dynamic Etalon Import contract.
6. Implement SQLite-first run identity/state/progress/events/heartbeat (B),
   then watchdog and API control operations.
7. Obtain web status ADR approval; only then implement the approved status
   window surface.
8. Implement Telegram as an adapter over the shared control plane (C), followed
   by security/audit tests.
9. Implement immutable deployment/version/smoke/rollback contract (D) only after
   migration preflight and backup restore rehearsal are accepted.
10. Conduct official-source provider research (E) and obtain separate selection
    approval; provider migration is a later execution decision unless explicitly
    included in a new approved scope.
11. Integrate risk-based model routing и capability-routing (F) в execution
    pipeline; validate UNROUTABLE handling и commit/push workflow.
12. Validate read-ledger, context capsule и replay-set acceptance (G) в
    multi-agent scenarios.
13. Run targeted tests, project reviewer, then project-local acceptance-only
    verifier. On FAIL, allow at most five fix iterations, then `BLOCKED`.
14. After verifier PASS stop at `READY_FOR_USER_INTEGRATION` /
    `AWAITING_USER_REVIEW`; user performs handoff, `/commit`, push workflow,
    `/deploy` and server check separately.

## 13. Required gates and evidence

### Approval

- This master scope is user-confirmed for spec authoring.
- Implementation requires explicit plan/scope approval after `/release` intake.
- Web status window requires its own ADR and approval.
- Provider choice and any destructive migration require separate explicit
  approval.
- Commit, tag, push and deploy are never implied by spec approval.
- Agent-layer permission baseline и routing policy (F) требуют отдельного
  approval до enforcement.

### Reviewer and verifier

Project reviewer checks contracts, precedence, security/redaction, idempotency,
scope and rollback claims. Project-local verifier checks only the DoD and
returns PASS/FAIL with evidence; it must not edit files. Neither role replaces
production server validation. Reviewer и verifier выполняются на cheap tier
(Workstream F).

### Minimum evidence set

- preflight repo/status/HEAD/tag evidence;
- schema/state transition and API contract tests;
- validator conflict fixtures including yellow/neutral cases;
- restart/watchdog/cancel/retry evidence;
- Telegram auth/confirmation/audit evidence;
- backup restore and migration dry-run evidence;
- version/health/smoke and immutable identity evidence;
- dated official-source provider matrix;
- agent-layer permission baseline enforcement evidence;
- risk-based routing classification и model routing decision log;
- read-ledger protocol validation: duplicate read rejection, hash change
  detection;
- commit/push workflow preflight evidence: `git diff --check`, targeted tests,
  conventional commit format, and final scope report.

## 14. Explicit non-goals and open decisions

### Non-goals

- Do not modify `dynamic-etalon-v1.0.2.md`.
- Do not put implementation instructions in Vault or another `docs/` path.
- Do not implement web status before ADR approval.
- Do not mix commit/push agent automation into app code or app acceptance.
- Do not claim provider prices, benchmarks or security capabilities without
  official evidence.
- Do not allow force-push, arbitrary git allow или bypass commit/push gate.
- Do not store secrets или full prompts в read-ledger или context capsule.

### Open decisions

- Decide during intake: exact API paths/schema versioning, event retention,
  watchdog thresholds, Telegram deployment mode, operator role model, release
  signature policy, migration compatibility window, provider candidate set and
  production observation window.
- Decide: model routing classification — какие task classes относятся к cheap
  tier vs Luna (Workstream F).
- Decide: read-ledger storage format, retention policy и hash algorithm
  (Workstream G).
- Decide: context capsule max size и serialization format (Workstream G).
- Decide: commit/push workflow tool selection и conventional commit scope
  conventions (Workstream F).
- Decide: agent-specific permission overrides — какие agents могут ужесточать
  baseline и через какой механизм.

## 15. Authoring preflight

- Repo: `/home/rudra/Projects/serp`.
- Branch at authoring preflight: `main`.
- HEAD at authoring preflight: `05bde07`.
- Existing local tags observed: `v1.0.0`, `v1.0.1`.
- Existing authoritative local specs, including
  `dynamic-etalon-v1.0.2.md`, are preserved.
- This authoring change does not commit, tag, push, deploy, modify application
  code/tests/configuration or run external provider selection.
- Workstreams F, G и agent-layer permission baseline are agent-layer concerns;
  they do not alter application code, tests, configuration or deployment.
