# Architecture Design

This document describes the Telescreen system architecture, its boundaries and the major
decisions. Detailed designs live in the supporting `design-*.md` files listed at the end.

## Overview

Telescreen is "Sentry for sensitive data stored where it must not be". Collectors read
monitored sources transiently, detectors find credentials and personal data, the masking
pipeline turns raw hits into masked values and keyed fingerprints, and the central server
(Reporting Bureau) groups findings into Sentry-like issues, drives incident-response
workflows through its workflow engine, dispatches verification work to extensible Police
workers, and exposes everything through a REST API, an MCP server and a SolidJS UI. Raw sensitive values never leave the scan coroutine that observed them.

Status: v1 design, baseline for all plans under `impl-plans/active/`.

---

## Sections

### 1. Naming Map

The product name "Telescreen" (from Orwell's "1984") and the owner's terms "Reporting
Bureau" and "Police" are the only themed names. Every internal module, API path and UI
label uses a plain descriptive name (owner decision, 2026-10-08) so the code base stays easy
to read. Names are used consistently in Python packages, API paths, UI labels and docs.

| Concept | Name | Python package / location | API prefix | UI label |
|---------|------|---------------------------|-----------|----------|
| Product | Telescreen | `telescreen` | - | Telescreen |
| Central server | Reporting Bureau | `telescreen.api`, `telescreen.cli serve` | `/api` | - |
| Collectors (built-in and custom crawlers/agents) | Collectors | `telescreen.collectors` | `/api/collectors` | Collectors |
| Detection backends | Detectors | `telescreen.detection` | - | Detectors |
| Masking/redaction pipeline | Masking (`Masker`) | `telescreen.masking` | - | - |
| Notifications with outbound DLP gate | Notifications | `telescreen.notifications` | - | Notifications |
| Workflow engine | Workflow engine | `telescreen.workflow` | `/api/workflows`, `/api/runs` | Workflows, Runs |
| Custom workers | Police | `telescreen.police` (Bureau side), `telescreen_police_sdk` | `/api/police/v1` | Police |
| Audit log and search | Audit, Search | `telescreen.audit`, `telescreen.search` | `/api/audit`, `/api/search` | Audit, Search |
| AI chat and MCP | Assistant | `telescreen.assistant` | `/api/chat` | Assistant |
| Evidence upload | Evidence | `telescreen.evidence` | `/api/evidence` | Evidence |
| Issue tracking | Issues | `telescreen.issues` | `/api/issues` | Issues |
| Push ingestion | Ingest | `telescreen.issues.ingest` | `/api/ingest/v1` | - |

### 2. Requirement Traceability

| # | Requirement (issue body) | Design element | Detailed in |
|---|--------------------------|----------------|-------------|
| 1 | Collectors, central config, event and/or schedule triggers, no content copies | Collectors with `CollectorAdapter` protocol (slack, gdrive, gdocs, gsheets, github, custom, police); `collector_configs` table owned by the Bureau; transient scan pipeline | `design-collectors-and-ingestion.md` |
| 2 | Reporting Bureau hub, push API, masking everywhere | FastAPI Bureau, `/api/ingest/v1`, Masking, masking log formatter, non-echoing error handlers | `design-collectors-and-ingestion.md`, `design-masking.md` |
| 3 | MCP server and agent chat | Assistant tool set shared by `telescreen mcp` (stdio) and `/api/chat` | `design-api-and-ui.md` |
| 4 | Sentry-like issues | Grouping key, status machine, first/last seen, times_seen, history, auto-resolve, regression | `design-collectors-and-ingestion.md`, `design-data-model.md` |
| 5 | Outbound notifications audited for raw data | DLP gate; adapters accept only `CensoredMessage` | `design-masking.md` |
| 6 | AI agent workflows, durable, YAML/JSON | Workflow DSL v1 and DB-persisted engine | `design-workflow-engine.md` |
| 7 | Configurable incident response and escalation | `human_task` with SLA, escalation policies with tiers, recipients (member, team, email, slack) | `design-workflow-engine.md` |
| 8 | Evidence upload with reject, police re-check | `evidence_upload` and `police_verify` step types, Evidence scan-and-reject | `design-workflow-engine.md`, `design-api-and-ui.md` |
| 9 | Sentry-like UI, Meilisearch, index guard | SolidJS UI, `SearchBackend` (Meilisearch over httpx), rule-only IndexGuard | `design-api-and-ui.md`, `design-masking.md` |
| 10 | Switchable detectors behind one batched interface | `Detector.detect_batch`, rule/ml/managed_llm/local_llm, composite union/cascade | `design-detectors.md` |
| 11 | Audit data searchable | Append-only hash-chained `audit_records`, `audit` search index | `design-data-model.md`, `design-api-and-ui.md` |
| 12 | Custom police in private networks, subscription workers | Pull mode (register/claim/heartbeat/result) and collector kind `police` for centrally configured private crawlers | `design-police-contract.md` |
| 13 | Strict typing conventions | Typing boundary decisions in section 6 of this file | this file |
| 14 | Loosely coupled police, n8n-buildable, SDK | Versioned HTTP+JSON contract, push webhook mode, `telescreen_police_sdk`, n8n example | `design-police-contract.md` |

### 3. Runtime Processes

| Process | Command | Responsibilities |
|---------|---------|------------------|
| Bureau API | `telescreen serve` | REST API, collector webhooks, police contract endpoints, static UI (if `TELESCREEN_WEB_DIST` set) |
| Engine worker | `telescreen worker` | Claims engine tasks (workflow steps, timers, SLA escalation, collector scans, police push delivery), runs the cron scheduler, executes search indexing |
| MCP server | `telescreen mcp` | Assistant tools over stdio |
| Police (external) | SDK runner, n8n, or any HTTP client | Pull or push mode against `/api/police/v1` |

All Bureau processes share one PostgreSQL database. Meilisearch is optional at runtime:
when `TELESCREEN_MEILI_URL` is unset an in-process `InMemorySearchBackend` is used (dev and
tests only; it is not durable).

Data flow (scan):

```text
source event/schedule -> engine task collector_scan -> CollectorAdapter.fetch (transient text)
  -> ScanItem batch -> DetectorPipeline.detect_batch -> RawFinding spans
  -> Masker.seal (mask + snippet + HMAC fingerprint) -> MaskedFinding
  -> IssueService.record_scan (group, count, auto-resolve, regress) -> audit + index + workflow triggers
  (ScanItem text is dropped when the coroutine returns; never persisted, logged or indexed)
```

Deployment and examples (examples-compose plan):

- `examples/compose/docker-compose.yml`: `postgres:16`, `getmeili/meilisearch:v1.x` (pinned
  minor), `migrate` (one-shot `telescreen migrate`), `bureau` (`telescreen serve`, serves the
  built UI), `engine` (`telescreen worker`), `example-police` (the SDK pull-mode example).
  `examples/compose/Dockerfile` builds the wheel and the UI (bun) in stages.
  `examples/compose/.env.example` contains placeholders and `env:`/`file:` secret refs only.
- `examples/workflows/`: `github-credential-leak.yaml` (AI verification, first-line triage
  human task with SLA, dev team remediation with evidence upload, GitHub Enterprise admin
  verification of refs/forks/caches, police `verify_remediation`, send-back on failure,
  escalation), `slack-pii.yaml`, `gdrive-mynumber-sheet.yaml`, and
  `escalation-policies.json`. `tests/examples/` validates every example with the DSL loader.

### 4. Package Layout and Layering

Imports only point downward. A lower layer never imports a higher layer.

| Layer | Packages | May import |
|-------|----------|-----------|
| L0 | `telescreen.core` (config, json/yaml boundary, clock, errors, secret refs, ids, `vocabulary` enums shared by every plan: `RuleClass`, `Severity`, `DetectorKind`, `IssueStatus`, `CollectorKind`, `ErrorCode`) | stdlib, pydantic, pyyaml |
| L1 | `telescreen.detection` (types, rules, detectors), `telescreen.workflow.dsl` (parse, validate, expressions), `telescreen.ports` (cross-plan protocols and DTOs), `telescreen.contracts` (JSON Schemas + validator), `telescreen.db` (ORM models, session, migrations) | L0 |
| L2 | `telescreen.masking` | L0, L1 |
| L3 | `telescreen.api.common`, `telescreen.directory`, `telescreen.issues`, `telescreen.audit`, `telescreen.search`, `telescreen.notifications`, `telescreen.evidence`, `telescreen.police`, `telescreen.collectors`, `telescreen.workflow` (engine, executors), `telescreen.assistant` | L0-L2 and `telescreen.ports` protocols only (never another L3 implementation) |
| L4 | `telescreen.wiring`, `telescreen.api.app`, `telescreen.cli` | everything |

Rule: an L3 package that needs another L3 capability depends on a Protocol in
`telescreen.ports` (for example `AuditLog`, `IssueRecorder`, `NotificationSender`,
`PoliceGateway`, `RunSignaler`, `SearchIndexer`). Concrete implementations are bound only in
`telescreen.wiring` (L4). This keeps L3 plans parallel and testable with fakes.

Ports (`telescreen.ports`, frozen in wave 2; DTOs are frozen dataclasses, interfaces are
Protocols):

| Port | Purpose | Implemented by | Consumed by |
|------|---------|----------------|-------------|
| `AuditLog.append(entry: AuditEntry)` | Hash-chained append in the caller's session | audit-search (`telescreen.audit`) | all L3 |
| `SearchIndexer.index_issue/finding/run/audit(id)` | Post-commit indexing | audit-search (`telescreen.search`) | issues, workflow, audit-search |
| `IssueRecorder.record_scan(ResourceScan)`, `record_external(ExternalScan)` | Persist masked findings, grouping, auto-resolve; `record_external` re-scans with `Redactor` and re-keys fingerprints | issues-ingest | collectors-core, police-bureau, ingest router |
| `ResourceScanner.scan_resource(ResourceContent, scan_job_id)` | Transient raw-content scan path | issues-ingest | collectors-core, ingest router |
| `ResourceRescanner.rescan(issue_id)` | Rescan an issue resource | collectors-core | workflow-nodes (`detect`) |
| `CollectorAdapter` plus DTOs (`InboundWebhook`, `EventCheck`, `ResourceRef`, `ResourceContent`, `Segment`, `ChangePage`, `CollectorCursor`, `CollectorConfig`, `WatchState`, selector dataclasses), in `ports/collectors.py` | Per-source collector contract | collectors-slack, collectors-google, collectors-github | collectors-core |
| `IssueReader.get(issue_id)`, `open_resource_refs(collector_config_id)` | Masked issue snapshot; refs of open issues for removal reconciliation | issues-ingest | workflow, assistant, collectors-core |
| `IssueEventSink.on_issue_event(IssueEvent)` | Workflow triggers | workflow-engine | issues-ingest |
| `NotificationSender.send(NotificationRequest) -> NotificationReceipt` | In the caller's transaction: DLP gate, insert the `notifications` row (status `queued`, or `blocked`) and its audit record, then enqueue a `notification_delivery` task (idempotency key `notify:<notification_id>`). No network I/O | notifications | workflow (notify executor, SLA escalation) |
| `PoliceGateway.dispatch/cancel` | Police task dispatch | police-bureau | workflow-nodes, collectors-core (`police` kind) |
| `RunSignaler.signal(step_run_id, StepSignal)` | Resume waiting steps | workflow-engine | police-bureau, api human tasks |
| `EvidenceQuery.accepted_count(step_run_id)` | Evidence gate | evidence | workflow-engine |
| `TaskQueue` and `TaskHandler(kind, handle)` | Durable queue and pluggable task kinds | workflow-engine (queue) | collectors-core (`collector_scan`, `collector_watch_renew`), police-bureau (`police_push_delivery`), notifications (`notification_delivery`) |
| `ScheduleProvider.tick(now)` | Scheduler hook for cron work | collectors-core | workflow-engine scheduler |
| `StepExecutor`, `StepContext`, `StepOutcome`, `StepSignal` | Workflow node execution | workflow-engine, workflow-nodes | workflow-engine |
| `RecipientResolver.resolve(ref)` | Recipient ref to contacts | api-common (`telescreen.directory`) | notifications, workflow-engine |

Repositories live in the feature package that owns the tables: directory tables
(`members`, `teams`, `team_memberships`, `api_tokens`) in `telescreen.directory`; collectors
tables in `telescreen.collectors` (collectors-core); issues, findings, history and `counters` in
`telescreen.issues`; workflow tables in `telescreen.workflow`; `notifications` in
`telescreen.notifications`; `evidence` in `telescreen.evidence`; police tables in
`telescreen.police`; audit tables in `telescreen.audit`. db-schema owns only models,
session factory, migrations and the shared `ReplayNonceStore` (`telescreen/db/replay.py`)
used by collector webhooks and police callbacks.

`telescreen_police_sdk` is a separate top-level package in the same wheel. It imports only
stdlib and httpx and never `telescreen.*`. The Bureau imports the SDK only in
`tests/contract/`.

The UI lives in `src/web` (SolidJS + TypeScript + Vite + vitest) and talks to `/api` only.

### 5. Cross-Cutting Decisions

1. **Single tenant.** v1 serves one organization per deployment; there is no
   `organizations` table. Teams, members and recipients are global. Rationale: removes a
   tenant filter from every query and API without affecting any accepted requirement.
2. **External HTTP through httpx.** Slack, Google, GitHub, Meilisearch, local LLM, the
   `codex` managed LLM provider (OpenAI API) and police push delivery use `httpx.AsyncClient`
   directly and parse responses with
   `pydantic.TypeAdapter(...).validate_json(response.content)`. Vendor SDKs that speak in
   mappings (google-api-python-client, meilisearch client, slack_sdk, openai) are not used.
   The `claude` managed LLM provider (default, `TELESCREEN_MANAGED_LLM_PROVIDER`) uses the
   `anthropic` SDK; MCP uses the `mcp` SDK. Tests inject
   `httpx.MockTransport`; no test opens a network socket.
3. **Persistence portability.** Repositories use SQLAlchemy 2 async with portable column
   types. JSON documents are stored in `Text` columns as canonical JSON produced by
   `telescreen.core.json` (no JSONB). Offline tests run the real repositories against
   SQLite (`aiosqlite`, in-memory). PostgreSQL-only features are limited to: the
   append-only trigger on `audit_records` and `FOR UPDATE SKIP LOCKED` (rendered by
   SQLAlchemy `with_for_update(skip_locked=True)`, a no-op on SQLite which is single-writer).
   PostgreSQL integration tests are marked `integration`.
4. **Timestamps.** All timestamps are timezone-aware UTC. Columns use
   `DateTime(timezone=True)`; repositories normalize values read back with
   `telescreen.core.clock.ensure_utc` (SQLite returns naive values). Time is read only via
   the `Clock` protocol so tests use `FakeClock`.
5. **Identifiers.** Primary keys are UUID4 strings except `audit_records.seq` (integer
   chain position) and the issue display number (`issues.number`, shown as `TELE-<n>`,
   assigned from the `counters` table inside the inserting transaction).
6. **Authentication.** Bearer API tokens (random 32 bytes, base64url) stored as SHA-256
   hashes in `api_tokens` with scopes `read`, `write`, `admin`, `ingest`. Police use a
   separate enrollment token (secret ref) and per-worker tokens. SSO is out of scope for v1
   (see `design-docs/user-qa/qa-ui-authentication.md`).
7. **Secrets by reference.** Configuration and DB rows hold secret references
   (`env:NAME` or `file:/path`), never secret values. `telescreen.core.secrets` resolves
   them at use time and the resolved value is never logged or stored.
8. **Search indexing.** Indexing runs after the owning transaction commits, through
   `SearchIndexer` (implemented by `telescreen.search`). Failures are logged (masked) and repaired by
   `telescreen search reindex`. There is no outbox table in v1.

### 6. Strict Typing Boundary Decisions

The repo enforces `tools/typelint.py` (TL001-TL010), ruff ANN/PGH and ty with
`error-on-warning` (ty is pinned at 0.0.85 in `uv.lock`). None of these may be weakened.

| Topic | Decision |
|-------|----------|
| Arbitrary JSON | `telescreen.core.json` defines a closed, self-referencing frozen dataclass `JsonValue` (kind enum + scalar fields + `items: tuple[JsonValue, ...]` + `members: tuple[JsonMember, ...]`), plus `parse_json(text) -> JsonValue`, `parse_yaml(text) -> JsonValue` (PyYAML `safe_load`), `dump_json(value) -> str` (canonical: sorted keys, no whitespace) and typed accessors. No recursive `type` alias anywhere. |
| Decoding json/yaml natives | Converted inside `telescreen/core/json.py` only, using structural Protocols in the style of `TomlTable` in `tools/typelint.py`. If ty cannot narrow those Protocols, that single file gets a `TL005` per-file-ignore (justified: stdlib json/PyYAML return untyped natives). The SDK has the same boundary in `src/telescreen_police_sdk/_json.py` with the same rule. These are the only two pre-approved per-file-ignore candidates; each is added by the foundation plan only when the implementation proves it necessary. |
| Typed payloads | Inbound and outbound API bodies, LLM structured outputs, connector responses and contract envelopes are pydantic dataclasses or stdlib dataclasses parsed with `TypeAdapter.validate_json(bytes)`. Outbound mappings are produced by `dataclasses.asdict(...)` passed inline to the library call. |
| FastAPI | Request/response bodies are `pydantic.dataclasses.dataclass`; never `BaseModel`. |
| Settings | `TelescreenSettings` is a frozen stdlib dataclass built by `load_settings(env: EnvSource)` where `EnvSource` is a Protocol with `get(key) -> str | None` (satisfied by `os.environ`). No pydantic-settings. |
| ORM | `class Base(MappedAsDataclass, DeclarativeBase)`; table names via `__tablename__: ClassVar[str]`; no `TypeDecorator` subclasses (JSON is `Text`, timestamps normalized in repositories). |
| Interfaces | `typing.Protocol`; stateful services are dataclasses. |
| Exceptions | `TelescreenError(code: ErrorCode, detail: str)` where `detail` must be a static or already-masked string. Raw content is never formatted into exception messages. |

Any further per-file-ignore requires serial reconciliation through the foundation plan
owner and must be called out in review.

### 7. Data-Handling Guarantees (summary)

1. Raw detected values exist only in memory inside the scan or upload coroutine.
2. Persisted, logged, indexed, notified and returned data contains only masked values,
   masked snippets and HMAC-SHA256 fingerprints keyed by `TELESCREEN_FINGERPRINT_KEY`.
3. Monitored content is never stored. Evidence is stored only after a clean scan; stored
   images have their metadata stripped first.
4. Every log record, exception message and traceback passes the masking formatter.
5. Error responses never echo request input (custom FastAPI validation handler).
6. Every search document passes the rule-only IndexGuard; every outbound notification
   passes the DLP gate and is recorded and audited before delivery; every police
   result and pushed finding is re-scanned.
7. LLM egress. Text detectors send monitored content to an LLM only when explicitly enabled
   in `TELESCREEN_DETECTORS` (default `rule`). One exception, by owner decision
   (`design-docs/user-qa/qa-evidence-file-types.md`, `qa-llm-data-egress.md`): uploaded
   image evidence is always sent to the configured managed LLM provider (`claude` default,
   or `codex`) for scanning. That scan fails closed: if no scanner is configured, image
   uploads are rejected with 503. `ai_agent` steps and Assistant chat send only masked
   data. LLM outputs are spans, labels or verdicts, never values, and free-text outputs are
   redacted before use.

Full details and the leak-test matrix: `design-masking.md`.

### 8. Implementation Decomposition Constraints

Plans must keep disjoint write paths and follow this wave structure. A later wave may
replace stub files created by an earlier wave only where noted (explicit handoff).

| Wave | Plan (suggested id) | Owns (write paths) | Depends on |
|------|---------------------|--------------------|------------|
| 1 | foundation | `pyproject.toml`, `uv.lock`, `mise.toml`, `.gitignore`, `README.md`, `src/web/package.json`, `src/web/bun.lock`, `src/telescreen/__init__.py`, `src/telescreen/core/**`, `tests/conftest.py`, `tests/support/__init__.py`, `tests/core/**` | - |
| 2 | db-schema | `src/telescreen/db/**`, `tests/db/**`, `tests/support/db.py` | foundation |
| 2 | detection-rules | `src/telescreen/detection/{__init__,types,normalize,rule_detector,composite}.py`, `src/telescreen/detection/rules/**`, `tests/detection/rules/**` | foundation |
| 2 | ports | `src/telescreen/ports/**`, `tests/ports/**` | foundation |
| 2 | police-contract | `src/telescreen/contracts/**`, `tests/contract/test_schemas.py` | foundation |
| 2 | workflow-dsl | `src/telescreen/workflow/__init__.py`, `src/telescreen/workflow/dsl/**`, `tests/workflow/dsl/**` | foundation |
| 2 | web-shell | `src/web/{index.html,tsconfig.json,vite.config.ts,vitest.setup.ts}`, `src/web/src/{main.tsx,App.tsx,routes.tsx}`, `src/web/src/{api,components,styles}/**`, stub `src/web/src/pages/*/index.tsx` | foundation |
| 3 | masking | `src/telescreen/masking/**`, `tests/masking/**` | detection-rules, ports |
| 3 | detection-llm-ml | `src/telescreen/detection/{harness,managed_llm,local_llm,ml,factory}.py`, `tests/detection/llm/**` | detection-rules |
| 3 | api-common | `src/telescreen/directory/**`, `src/telescreen/api/__init__.py`, `src/telescreen/api/common/**`, `src/telescreen/api/routers/__init__.py`, `src/telescreen/api/routers/{health,directory}.py` (members, teams, API tokens), `tests/api/common/**` | db-schema, ports |
| 3 | police-sdk | `src/telescreen_police_sdk/**`, `tests/police_sdk/**`, `tests/contract/test_sdk_contract.py`, `examples/police/**` | police-contract |
| 3 | ui-issues / ui-workflows / ui-ops | `src/web/src/pages/{issues,search}/**` / `src/web/src/pages/{workflows,runs}/**` / `src/web/src/pages/{police,collectors,escalation,audit,chat,settings}/**` (replacing shell stubs) | web-shell |
| 4 | audit-search | `src/telescreen/audit/**`, `src/telescreen/search/**`, `src/telescreen/api/routers/{audit,search}.py`, `tests/audit/**`, `tests/search/**` | masking, api-common |
| 4 | issues-ingest | `src/telescreen/issues/**`, `src/telescreen/api/routers/{issues,ingest}.py`, `tests/issues/**` | masking, api-common |
| 4 | notifications | `src/telescreen/notifications/**`, `tests/notifications/**` | masking, detection-llm-ml |
| 4 | evidence | `src/telescreen/evidence/**` (including `image.py`: the `ImageEvidenceScanner` Protocol and its managed-LLM implementation `LlmImageEvidenceScanner` over `LlmClient`; and `strip.py`: the stdlib image metadata stripper), `src/telescreen/api/routers/evidence.py`, `tests/evidence/**` | masking, api-common, detection-llm-ml |
| 4 | police-bureau | `src/telescreen/police/**`, `src/telescreen/api/routers/police.py`, `tests/police/**`, `tests/contract/test_bureau_contract.py` | masking, api-common, police-contract |
| 4 | collectors-core | `src/telescreen/collectors/{__init__,service,registry,scheduler,rescanner,police,tasks}.py`, `src/telescreen/api/routers/collectors.py`, `tests/collectors/core/**` | masking, api-common |
| 4 | collectors-slack | `src/telescreen/collectors/slack/**`, `tests/collectors/slack/**` | ports, masking |
| 4 | collectors-google | `src/telescreen/collectors/google/**` (gdrive, gdocs, gsheets adapters and service-account JWT auth), `tests/collectors/google/**` | ports, masking |
| 4 | collectors-github | `src/telescreen/collectors/github/**`, `tests/collectors/github/**` | ports, masking |
| 4 | workflow-engine | `src/telescreen/workflow/{engine,queue,scheduler,escalation,triggers}/**`, `src/telescreen/workflow/executors/{__init__,flow,human}.py`, `src/telescreen/api/routers/{workflows,runs,human_tasks,escalation}.py`, `tests/workflow/engine/**` | workflow-dsl, masking, api-common |
| 4 | workflow-nodes | `src/telescreen/workflow/executors/{detect,ai_agent,notify,police_verify}.py`, `tests/workflow/executors/**` | workflow-dsl, masking, detection-llm-ml |
| 4 | assistant | `src/telescreen/assistant/**`, `src/telescreen/api/routers/chat.py`, `tests/assistant/**` | masking, api-common, detection-llm-ml |
| 5 | assembly | `src/telescreen/wiring.py`, `src/telescreen/api/app.py`, `src/telescreen/cli.py`, `src/telescreen/__main__.py`, `tests/e2e/**`, `tests/leak/**`, `tests/test_package.py` | all wave 4 plans |
| 5 | examples-compose | `examples/compose/**`, `examples/workflows/**`, `tests/examples/**` | workflow-dsl, assembly (CLI names only) |

Constraints:

- Only foundation edits root files and `src/web/package.json`/`bun.lock`. Other plans that
  need a dependency request it through serial reconciliation. Foundation therefore adds the
  full v1 dependency set up front (listed in `design-docs/specs/command.md`, Dependencies).
- `src/web/node_modules` and `src/web/dist` are gitignored and never part of a plan.
- The collectors plans never import each other. collectors-core holds the `CollectorRegistry` (kind to
  `CollectorAdapter`); the collectors router dispatches each `POST /api/collectors/{kind}/webhook` to the
  adapter registered for that kind; concrete adapters are registered only in
  `telescreen.wiring` (assembly). collectors-core tests use a fake adapter.
- Cross-wave-4 interaction happens only through `telescreen.ports`; wave 4 tests use fakes
  of other ports. End-to-end flows across packages are tested in `tests/e2e` and
  `tests/leak` by the assembly plan.
- Every Python file stays under 1000 lines; every plan file under 400 lines.

### 9. Verification Strategy

| Level | Tooling | Network |
|-------|---------|---------|
| Python unit/contract/leak | `uv run pytest` with pytest-asyncio, SQLite repositories, httpx MockTransport, fake LLM clients | none |
| Integration | `@pytest.mark.integration`, skipped unless `TELESCREEN_TEST_DATABASE_URL` / `TELESCREEN_TEST_MEILI_URL` | local services only |
| Static | `uv run ruff format --check .`, `uv run ruff check .`, `uv run python tools/typelint.py`, `uv run ty check` | none |
| Build | `uv build` (wheel contains `telescreen` and `telescreen_police_sdk`) | none |
| UI | `cd src/web && bun install && bun run typecheck && bun run test && bun run build` | `bun install` only |

`mise run ci` must pass. Test sample secrets are assembled at runtime from fragments so the
gitleaks pre-commit hook does not flag committed sources.

### 10. Supporting Documents

| Document | Content |
|----------|---------|
| `design-docs/specs/design-data-model.md` | Tables, columns, constraints, status machines |
| `design-docs/specs/design-masking.md` | Masking, fingerprints, logging, IndexGuard, DLP gate, leak tests |
| `design-docs/specs/design-detectors.md` | Detector protocol, rule catalog, LLM/ML harness, composite |
| `design-docs/specs/design-collectors-and-ingestion.md` | Collectors, triggers, push API, scan pipeline, issue lifecycle |
| `design-docs/specs/design-workflow-engine.md` | Workflow DSL v1, node types, engine durability, escalation |
| `design-docs/specs/design-police-contract.md` | Police contract v1, pull/push, signing, SDK, n8n |
| `design-docs/specs/design-api-and-ui.md` | REST API, auth, Assistant MCP/chat, evidence API, UI |
| `design-docs/specs/command.md` | CLI, environment variables, exit codes, dependencies |
| `design-docs/specs/notes.md` | Engine rationale vs Airflow/n8n/Kestra, riela mapping, tooling notes |

---
