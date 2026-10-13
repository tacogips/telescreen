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

Status: v1 design, implemented. It is the baseline for the v1 and v1.1 plans, which are
archived under `impl-plans/completed/`. v1.2 (notification templates, workflow
notification sets, roles and permissions, OIDC SSO, live delivery verification) is
designed in `design-v1-2-notifications-and-identity.md` and implemented; its plans are
archived under `impl-plans/completed/` as well. That document's v1.2 notes below
and in the supporting specs are additions; they replace only decision 6 (Authentication)
of section 5.

The v1.3 Sentry-parity additions are implemented (2026-10-13); their plans are archived
under `impl-plans/completed/`.

The v1.4 data inventory (asset register, lifecycle events, list-item and value diffs,
admin field schema, retention) is designed in `design-v1-4-data-inventory.md` (2026-10-13)
and is the baseline for the `v14-pNN` plans.

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
| Notification templates (v1.2) | Notification templates | `telescreen.notifications.templates` | `/api/notification-templates` | Settings > Notification templates |
| Users, roles, tokens (v1.2) | Users and roles | `telescreen.directory`, `telescreen.core.permissions` | `/api/users`, `/api/roles`, `/api/tokens` | Settings > Users and roles |
| OIDC sign-in (v1.2) | Identity | `telescreen.identity` | `/api/auth` | Login |
| Data inventory (v1.4) | Data inventory | `telescreen.inventory` | `/api/data` | Data, Settings > Field schema |

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
| 15 | Plain naming; only Telescreen, Reporting Bureau and Police themed | Section 1 naming map. The themed page names in the original UI acceptance criterion map to the Collectors page and the Assistant chat page | this file |
| 16 | Owner decisions: text and image evidence, Claude (default) or Codex managed LLM, bearer tokens for v1 | Evidence scan-and-reject with a fail-closed image scanner; `TELESCREEN_MANAGED_LLM_PROVIDER`; `api_tokens` | `design-api-and-ui.md`, `design-detectors.md`, `design-docs/user-qa/qa-*.md` (all resolved) |
| 17 | Reviewed draft is the baseline | Commit 2036af9 re-reviewed on 2026-10-08. Fixes were limited to stale names, JSON wire encoding, cross-package endpoint ownership, ports for issue history and the evidence gate, the SDK ASGI boundary, and the polling-only queue rationale. Second re-review of cfa6529 (2026-10-08) fixed only map-reduce integration gaps: sweep ports between collectors-core, workflow-mapreduce and police-bureau; a serialized dispatcher for the concurrency cap; crash-safe shard re-execution; watermark-based removal reconciliation (`issues.last_checked_at`); missing CLI, env, API and task-kind entries; and v1 `map` step scope | all files |
| 18 | Whole-estate inspection (for example all of Google Drive, also by Police) runs concurrently as map-reduce, not sequentially; Airflow considered | Durable map-reduce sweeps in the self-built engine: streaming partition, `map_items` dispatched concurrently with a global `max_concurrency`, quota backpressure, reduce with removal reconciliation; `map` DSL step; Police `partition`/`scan_shard` task types; built-in sweeps for Drive/Docs/Sheets, GitHub, Slack. Airflow rejected as core engine (`notes.md`) | `design-map-reduce-sweeps.md` |

v1.2 rows (`design-v1-2-notifications-and-identity.md`):

| # | Requirement | Design element | Section |
|---|-------------|----------------|---------|
| 19 | Editable notification templates | Versioned `notification_templates`, `system.<event>` built-ins, allow-listed `NotificationContext`, rendering inside `NotificationService` before the DLP gate | 3 |
| 20 | Workflow notification target/channel sets | DSL `telescreen.workflow/v2` `notification_sets`, `targets`, `slack_channel:`, per-target `notifications` rows | 4 |
| 21 | Live email and Slack verification | `tests/live` (Mailpit, Slack, Keycloak), `telescreen notify test` | 7 |
| 22 | Users, roles, permission management | `Permission` matrix, `require_permission`, route-walk test, last-admin lock, token intersection | 5 |
| 23 | OIDC SSO with Keycloak | `telescreen.identity` verifier, `/api/auth/session`, keyed `oidc_subject` identities, claim mapping, UI PKCE | 6 |

v1.4 rows (`design-v1-4-data-inventory.md`; requirements R1-R9 in
`design-v1-4-data-inventory-requirements.md`):

| # | Requirement | Design element | Section |
|---|-------------|----------------|---------|
| 24 | Register of data locations with lifecycle history | `data_assets` and append-only `data_asset_events`; per-collector `inventory_mode`; deletion only from definitive removal signals | 2, 4, 5 |
| 25 | Partial changes of list-shaped data | Keyed item fingerprints and counts (collector port, ingest v1 additive members, police contract 1.2), value-fingerprint diffs, manual changes | 6 |
| 26 | Free metadata schema with required fields and values | `data_fields`, `data_field_values`, `Redactor`-checked values, computed `incomplete` | 7 |
| 27 | Planned deletion date | `deletion_due` role, Overdue view, one notification per due date | 8 |

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
  The runtime image runs as a non-root user, so every path a service writes
  (`TELESCREEN_EVIDENCE_DIR`, created lazily by `LocalFileEvidenceStore.put`) is created
  in the image owned by that user and backed by a named volume mounted on `bureau` and
  `engine`; otherwise the first evidence upload fails with a permission error. The
  example Police reads `TELESCREEN_FINGERPRINT_KEY` as a raw value (not a ref), so its
  service overrides it from a host-exported value, as it does for the enrollment token,
  instead of inheriting the Bureau's `file:` ref string from `.env`.
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
| `IssueRecorder.record_scan(ResourceScan) -> RecordOutcome`, `record_external(ExternalScan) -> RecordOutcome`, `add_history(IssueHistoryEntry)`, `resolve_unchecked(collector_config_id, checked_before) -> int` | Persist masked findings, grouping, auto-resolve, `last_checked_at`; `record_external` re-scans with `Redactor` and re-keys fingerprints; `add_history` appends an `issue_history` row (kinds `workflow_started`, `evidence_added`, `sla_breached`) in the caller's session, with the note redacted; `resolve_unchecked` is sweep removal reconciliation | issues-ingest | collectors-core, police-bureau, ingest router, workflow-engine, evidence, workflow-mapreduce |
| `ResourceScanner.scan_resource(ResourceContent, scan_job_id)` | Transient raw-content scan path | issues-ingest | collectors-core, ingest router |
| `ResourceRescanner.rescan(issue_id)` | Rescan an issue resource | collectors-core | workflow-nodes (`detect`) |
| `CollectorAdapter` plus DTOs (`InboundWebhook`, `EventCheck`, `ResourceRef`, `ResourceContent`, `Segment`, `ChangePage`, `CollectorCursor`, `CollectorConfig`, `WatchState`, selector dataclasses), in `ports/collectors.py` | Per-source collector contract | collectors-slack, collectors-google, collectors-github | collectors-core |
| `IssueReader.get(issue_id)`, `open_resource_refs(collector_config_id)` | Masked issue snapshot; refs of open issues for removal reconciliation | issues-ingest | workflow, assistant, collectors-core |
| `IssueEventSink.on_issue_event(IssueEvent)` | Workflow triggers | workflow-engine | issues-ingest |
| `NotificationSender.send(NotificationRequest) -> NotificationReceipt` | In the caller's transaction: DLP gate, insert the `notifications` row (status `queued`, or `blocked`) and its audit record, then enqueue a `notification_delivery` task (idempotency key `notify:<notification_id>`). No network I/O | notifications | workflow (notify executor, SLA escalation) |
| `PoliceGateway.dispatch/cancel` | Police task dispatch | police-bureau | workflow-nodes, collectors-core (`police` kind) |
| `RunSignaler.signal(step_run_id, StepSignal)` | Resume waiting steps | workflow-engine | police-bureau, api human tasks |
| `EvidenceQuery.accepted_count(issue_id, step_run_id)` | Evidence gate; counts only accepted evidence whose `issue_id` matches the run's issue, so evidence filed against another issue cannot satisfy the gate | evidence | workflow-engine |
| `TaskQueue` and `TaskHandler(kind, handle)` | Durable queue and pluggable task kinds | workflow-engine (queue) | collectors-core (`collector_scan`, `collector_watch_renew`), police-bureau (`police_push_delivery`), notifications (`notification_delivery`), workflow-mapreduce (`map_partition`, `map_dispatch`, `map_shard`, `map_reduce`) |
| `ScheduleProvider.tick(now)` | Scheduler hook for cron work | collectors-core | workflow-engine scheduler |
| `StepExecutor`, `StepContext`, `StepOutcome`, `StepSignal` | Workflow node execution | workflow-engine, workflow-nodes | workflow-engine |
| `RecipientResolver.resolve(ref)` | Recipient ref to contacts | api-common (`telescreen.directory`) | notifications, workflow-engine |
| `SweepCapable`, `CollectorSweeper`, `SweepLauncher`, `MapResultSink` plus DTOs (`PartitionRequest`, `PartitionPage`, `ShardSpec`, `ShardEntry`, `ShardSummary`, `ShardFailure`, `RateLimited`), in `ports/sweeps.py` | Map-reduce sweeps (`design-map-reduce-sweeps.md`) | `SweepCapable`: collectors-slack/google/github; `CollectorSweeper`: collectors-core; `SweepLauncher`, `MapResultSink`: workflow-mapreduce | collectors-core; workflow-mapreduce; collectors-core (sweep API, `sweep_cron`, CLI); police-bureau |

v1.1 port additions (`design-v1-1-dspm-adoption.md` section 2):

- `ports/policy.py` `PolicyDecider` and `RiskScorer` are implemented in
  `telescreen.issues.policy` / `telescreen.issues.risk` and consumed by the issue record
  path.
- `IssueRemediation` is implemented by issues and consumed by the human-tasks router.
- `ResourceIssueReader.open_issues_for_resources` is consumed by the coverage router.
- The new protocol `OwnerDirectory` (`member_ref`, `team_refs_of`, `member_for_owner`,
  `team_lead`) is implemented by directory and consumed by issues and the routers.
- The collector DTO fields `Exposure`, `Coverage`, `OwnerIdentity` and `PlacementRef` are
  added, plus `RescanKind.inconclusive`.

The v1.1 tables are owned as listed in `design-data-model.md` ("v1.1 additions").

v1.2 additions (`design-v1-2-notifications-and-identity.md` section 2):

- `telescreen.core.permissions` (L0) holds `Permission`, the role and scope permission sets
  and the intersection function.
- `ports/notifications.py`: `NotificationRequest` gains `event`, `template_name`,
  `context: NotificationContext`, `set_name`, `target_index` and `record_unreachable`.
- `ports/templates.py` `TemplateCatalog` is implemented by notifications and consumed by
  workflow upload validation.
- `ports/identity.py`: `BearerVerifier` is implemented by the new L3 package
  `telescreen.identity` and consumed by `telescreen.api.common`. `MemberProvisioner` is
  implemented by directory and consumed by identity.
- `IssueSnapshot` gains `masked_excerpt`.
- New tables and columns are listed in `design-data-model.md` ("v1.2 additions").

v1.3 additions (`design-v1-3-sentry-parity.md` section 2):

Status: implemented 2026-10-13.

- New L3 packages:
  - `telescreen.alerts` (alert rules, evaluator, maintenance);
  - `telescreen.integrations` (integration status and tests).
- New `telescreen.issues` subpackages and modules:
  - `query` (stream query language, counts, trend);
  - `activity` (comments, mentions, subscriptions, bookmarks, activity feed);
  - `external` (links, GitHub create);
  - `views`, `archive`, `groups`, `bulk`, `merge`, `ownership`, `dashboard`.
- `ports/issues.py` adds `FindingsObserver`, `IssueActivityNotifier`, `IssueReportSource`
  and `AssigneeSuggester`. `IssueSnapshot` gains `priority` and `assignee_team_slug`.
- `ports/notifications.py` adds `NotificationRequest.category`,
  `NotificationSender.send_to_member`, `NotificationTester` and `OutboundContentGuard`.
- `ports/workflow.py` adds `RunStarter`. `ports/directory.py` adds `ActorDirectory`.
- The issue event sink becomes a fan-out to workflow triggers and the alert evaluator,
  built in `wiring_v13.py`.
- New tables and columns are listed in `design-data-model.md` ("v1.3 additions").

v1.4 additions (`design-v1-4-data-inventory.md` sections 3 and 13):

Status: designed 2026-10-13.

- New L3 package `telescreen.inventory` owns `data_assets`, `data_asset_events`,
  `data_asset_items`, `data_fields` and `data_field_values`. It reads issue, directory and
  collector data only through ports: `ResourceIssueReader`, `ActorDirectory`,
  `RecipientResolver`, `CollectorConfigReader`, `MemberNotifier` and
  `NotificationSender`.
- New `ports/inventory.py`:
  - `InventoryObserver` (called by `IssueService.record_scan`);
  - `InventoryReconciler` (called by the map-reduce reduce step, the full-listing path and
    the collector service);
  - `AssetOwnerResolver` (implemented in `issues/asset_owner.py`).
- `ResourceContent`, `ResourceScan`, `ExternalScan`, `CollectorConfig` and
  `NotificationContext` gain optional fields. `Fingerprinter.item_key` adds the item key
  domain.
- `issues/service.py` moves `record_external` to `issues/external_scan.py` to stay under
  1000 lines. The new `wiring_v14.py` binds the v1.4 services, ports, scheduler provider,
  task handler and routers.

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
   (see `design-docs/user-qa/qa-ui-authentication.md`). v1.2 replaces scope checks with
   per-route permissions:
   - members get the roles `admin`, `responder` and `viewer`;
   - a member-bound token's permissions are the token's scopes intersected with the
     member's role;
   - OIDC access tokens are validated against the configured issuer;
   - static API tokens stay valid.

   See `design-v1-2-notifications-and-identity.md` sections 5 and 6 and
   `design-docs/user-qa/qa-oidc-sso.md`.
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
| Decoding json/yaml natives | Converted inside `telescreen/core/json.py` only, using structural Protocols in the style of `TomlTable` in `tools/typelint.py`. If ty cannot narrow those Protocols, that single file gets a `TL005` per-file-ignore (justified: stdlib json/PyYAML return untyped natives). The SDK has the same boundary in `src/telescreen_police_sdk/_json.py` with the same rule. These two files are the only pre-approved per-file-ignore candidates; both entries are present in `pyproject.toml` (accepted with p01). |
| JSON on the wire | `JsonValue` is never a pydantic dataclass field. UI-facing APIs carry free-form JSON as `*_json` strings. External contracts (police v1, ingest source config) carry native JSON written and read through the JSON boundary. See "Free-form JSON on the wire" in `design-api-and-ui.md` |
| ASGI (SDK only) | `telescreen_police_sdk/asgi.py` types ASGI with local structural Protocols and passes typelint and ty without any per-file-ignore. An ASGI `TL005` ignore is NOT pre-approved: it would need serial reconciliation through the foundation owner and a review call-out. The Bureau needs no ASGI typing because FastAPI owns it |
| Typed payloads | Inbound and outbound API bodies, LLM structured outputs, connector responses and contract envelopes are pydantic dataclasses or stdlib dataclasses parsed with `TypeAdapter.validate_json(bytes)`. Outbound mappings are produced by `dataclasses.asdict(...)` passed inline to the library call. |
| FastAPI | Request/response bodies are `pydantic.dataclasses.dataclass`; never `BaseModel`. |
| Settings | `TelescreenSettings` is a frozen stdlib dataclass built by `load_settings(env: EnvSource)` where `EnvSource` is a Protocol with `get(key) -> str | None` (satisfied by `os.environ`). No pydantic-settings. |
| ORM | `class Base(MappedAsDataclass, DeclarativeBase)`; table names via unannotated `__tablename__ = "..."` (typelint exempts class-body dunders; annotating them breaks ty or creates dataclass fields); no `TypeDecorator` subclasses (JSON is `Text`, timestamps normalized in repositories). |
| Interfaces | `typing.Protocol`; stateful services are dataclasses. |
| Exceptions | `TelescreenError(code: ErrorCode, detail: str)` where `detail` must be a static or already-masked string. Raw content is never formatted into exception messages. |

Any further per-file-ignore requires serial reconciliation through the foundation plan
owner and must be called out in review.

### 7. Data-Handling Guarantees (summary)

1. Raw detected values exist only in memory inside the scan or upload coroutine.
2. Persisted, logged, indexed, notified and returned data contains only masked values,
   masked snippets and HMAC-SHA256 fingerprints keyed by `TELESCREEN_FINGERPRINT_KEY`
   (from v1.1, the versioned keys of `design-v1-1-dspm-adoption.md` section 9).
   v1.1 exposure, placement, owner, coverage, policy, health, occurrence and exception
   records add only enums, counts, keyed references and redacted text, never file names or
   emails.
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
| 4 | collectors-core | `src/telescreen/collectors/{__init__,service,registry,scheduler,rescanner,police,tasks,sweep}.py`, `src/telescreen/api/routers/collectors.py`, `tests/collectors/core/**` | masking, api-common |
| 4 | collectors-slack | `src/telescreen/collectors/slack/**`, `tests/collectors/slack/**` | ports, masking |
| 4 | collectors-google | `src/telescreen/collectors/google/**` (gdrive, gdocs, gsheets adapters and service-account JWT auth), `tests/collectors/google/**` | ports, masking |
| 4 | collectors-github | `src/telescreen/collectors/github/**`, `tests/collectors/github/**` | ports, masking |
| 4 | workflow-engine | `src/telescreen/workflow/{engine,queue,scheduler,escalation,triggers}/**`, `src/telescreen/workflow/executors/{__init__,flow,human}.py`, `src/telescreen/api/routers/{workflows,runs,human_tasks,escalation}.py`, `tests/workflow/engine/**` | workflow-dsl, masking, api-common |
| 4 | workflow-nodes | `src/telescreen/workflow/executors/{detect,ai_agent,notify,police_verify}.py`, `tests/workflow/executors/**` | workflow-dsl, masking, detection-llm-ml |
| 4 | workflow-mapreduce | `src/telescreen/workflow/mapreduce/**`, `src/telescreen/workflow/executors/map.py`, `src/telescreen/api/routers/map_jobs.py`, `tests/workflow/mapreduce/**` | workflow-dsl, masking, api-common |
| 4 | assistant | `src/telescreen/assistant/**`, `src/telescreen/api/routers/chat.py`, `tests/assistant/**` | masking, api-common, detection-llm-ml |
| 5 | assembly | `src/telescreen/wiring.py`, `src/telescreen/api/app.py`, `src/telescreen/cli.py`, `src/telescreen/__main__.py`, `tests/e2e/**`, `tests/leak/**`, `tests/test_package.py` | all wave 4 plans |
| 5 | examples-compose | `examples/compose/**`, `examples/workflows/**`, `tests/examples/**` | workflow-dsl, assembly (CLI names only) |

Constraints:

- Foundation writes the final `README.md` (setup with `mise run setup`, architecture
  overview, CLI and API usage, UI build, compose quick start) from this design. Later plans
  never touch it.
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

Run B (2026-10-09): waves 1-4 and the backend part of wave 5 are accepted at commit
28ce81b (all plans except ui-issues/ui-workflows/ui-ops, assembly and examples-compose).
The remaining work follows the plan boundaries in `design-ui-sentry.md` section 8.7:
ui-design-system (p30, new) and ui-mock-api (p31, new) first; then ui-issues, ui-workflows,
ui-ops and assembly in parallel; then examples-compose. Two ownership exceptions to the
rules above apply to run B only: p30 is the single serial owner of
`src/web/package.json`/`bun.lock` (foundation is accepted and closed), and
examples-compose (p29) updates `README.md` (UI, `serve` with `TELESCREEN_WEB_DIST`, compose
quick start). No run B plan edits `pyproject.toml` or `uv.lock`. ui-mock-api (p31) also
aligns the UI client types with the router serializers and makes the matching mechanical
page edits as an explicit wave-1 -> wave-2 handoff (`design-ui-sentry.md` section 8.1a).

Run B2 (2026-10-09, final): 28 plans are accepted at commit d6cffb3 (p01-p12, p14-p27,
p30, p31). Only ui-workflows (p13), assembly (p28) and examples-compose (p29) remain, and
their ownership is unchanged. p13 is complete; it only re-verifies and wraps its
`ErrorBanner` uses in `<Show keyed>` as p12 and p14 do. Assembly may be split into
sequential plans, each with explicit file-level, disjoint write paths and each under 400
lines:

| Wave | Plan | Owns | Depends on |
|------|------|------|------------|
| B2-1 | p13 | `src/web/src/pages/{workflows,runs}/**` | accepted |
| B2-1 | p28a (container/CLI/serve) | `wiring.py`, `wiring_builders.py` and further `wiring_*.py` split modules, `api/app.py`, `cli.py`, `__main__.py`, `tests/test_package.py`, `tests/e2e/test_serve_static.py`, `tests/e2e/test_api_routes.py`, plus new `tests/e2e/test_wiring*.py` | accepted |
| B2-2 | p28b (E2E flows) | new `tests/e2e/` files for GitHub remediation, sweep, push ingestion and chat, and their shared e2e harness module | p28a |
| B2-2 | p28c (leak matrix) | `tests/leak/**` (including the existing `test_ui_fixtures.py`), with its own harness under `tests/leak/` | p28a |
| B2-3 | p29 | as section 8.7 of `design-ui-sentry.md` | p13, p28* |

p28b and p28c may run in parallel because they share no files. They read `tests/support/**`
and the container but do not edit them. A wiring defect found by p28b or p28c becomes a
reconciliation request against p28a; it is not fixed in place. E2E tests may drive the
standalone `telescreen_police_sdk` `PullWorker` in process. This is test code, not Bureau
runtime, so the rule that the Bureau never imports the SDK still holds.

### 9. Verification Strategy

| Level | Tooling | Network |
|-------|---------|---------|
| Python unit/contract/leak | `uv run pytest` with pytest-asyncio, SQLite repositories, httpx MockTransport, fake LLM clients | none |
| Integration | `@pytest.mark.integration`, skipped unless `TELESCREEN_TEST_DATABASE_URL` / `TELESCREEN_TEST_MEILI_URL` | local services only |
| Live (v1.2) | `tests/live`, `@pytest.mark.live`, skipped unless the Mailpit/SMTP, Keycloak or Slack variables are set (`design-v1-2-notifications-and-identity.md` 7.2) | Mailpit and Keycloak locally, Slack API |
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
| `design-docs/specs/design-map-reduce-sweeps.md` | Concurrent map-reduce sweeps: partition/map/reduce, `map` step, Police sweep tasks, quotas |
| `design-docs/specs/design-synthetic-test-dataset.md` | Approved synthetic test dataset sources, fictitious-value rules, labeled corpus generator (follow-up) |
| `design-docs/specs/design-ui-sentry.md` | Sentry-based UI: tokens, components, shell, issue list/detail and page patterns, licensing constraints |
| `design-docs/specs/design-v1-1-dspm-adoption.md` | v1.1 gap analysis against comparable DSPM/DLP systems and adopted features F1-F10 (exposure, placement policies, verified resolution, coverage, owner routing, health, linked occurrences, risk score, exceptions) |
| `design-docs/specs/design-v1-2-notifications-and-identity.md` | v1.2: editable notification templates, workflow notification sets, live delivery verification, roles and permission matrix, OIDC SSO, threat model, v1.2 plan shape |
| `design-docs/specs/design-v1-3-sentry-parity-review.md` | v1.3 Sentry parity self-review: defects B1-B7, gap table, adopted scope S0-S10, deferrals, and its verification against the code |
| `design-docs/specs/design-v1-3-sentry-parity.md` | v1.3: stream tabs, query language, saved views, bulk and merge, archive and escalating, issue detail, alert rules, notification settings and weekly report, dashboard, organization pages, UX polish, leak matrix, v1.3 plan shape |
| `design-docs/specs/design-v1-4-data-inventory-requirements.md` | v1.4 accepted requirements R1-R9, safety constraints, out of scope, acceptance, and the design author's evidence re-check (section 7) |
| `design-docs/specs/design-v1-4-data-inventory.md` | v1.4: data asset register, inventory modes, lifecycle events, item and value diffs, ingest and police 1.2 changes, field schema and values, retention, API, UI, leak matrix, v1.4 plan shape |
| `design-docs/specs/design-api-and-ui.md` | REST API, auth, Assistant MCP/chat, evidence API, UI |
| `design-docs/specs/command.md` | CLI, environment variables, exit codes, dependencies |
| `design-docs/specs/notes.md` | Engine rationale vs Airflow/n8n/Kestra, riela mapping, tooling notes |

---
