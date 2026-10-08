# Design Notes

This document contains research findings, rationale and miscellaneous design notes.

## Overview

Rationale for the self-built workflow engine, the mapping from riela concepts, and tooling
compatibility notes that affect implementation.

---

## Sections

### Why a self-built engine instead of Airflow, n8n or Kestra

| Requirement | Airflow | n8n | Kestra | Workflow (self-built) |
|-------------|---------|-----|--------|----------------------|
| Human-in-the-loop waits of hours/days with SLA escalation | Sensors/deferrable operators poll; no native human task model | Wait node exists, but escalation tiers must be hand-built per workflow | Pause tasks exist; escalation is custom | First-class `human_task` with tiered escalation |
| Send-back to a prior step (cycles) | DAGs are acyclic by definition | Possible with loops, untyped | Limited (flows are mostly DAG-shaped) | `send_back` with visit counters and loop guards |
| Raw data never persisted | XCom and task logs persist values | Execution data stores full node input/output by default | Execution outputs and logs are stored | Only masked context is persisted; every output passes `Redactor` |
| DB-visible progress in our own schema for the Sentry-like UI and audit chain | Separate metadata DB | Separate DB, separate UI | Separate JVM stack and DB | Same PostgreSQL schema, same audit chain |
| Stack fit (Python, PostgreSQL only) | Python, but scheduler/webserver/executor stack | Node.js service | JVM service | Part of the Bureau |

Conclusion: an external engine would add a second data plane that stores workflow payloads
outside the masking guarantees, and its human-task and send-back semantics would still need
custom code. n8n remains supported where it fits best: as a push-mode Police.
Kestra 2.0's worker model inspired the pull-mode police protocol.

### Map-reduce sweeps: self-built vs Airflow

The owner asked whether Airflow should run whole-estate sweeps (for example all of Google
Drive) as concurrent map-reduce. Decision: implement map-reduce in the self-built engine
(`design-map-reduce-sweeps.md`); do not adopt Airflow as the core engine.

| Concern | Airflow dynamic task mapping (`expand`) | Self-built map-reduce |
|---------|----------------------------------------|-----------------------|
| Raw-data guarantee | Mapped inputs/outputs go through XCom and task logs capture stdout; masking cannot be enforced centrally | Shard descriptors are opaque ids that pass the `Redactor`; summaries are counts only |
| Private-network Police | Celery/Kubernetes executors need inbound access to broker/metadata DB | Pull-mode Police only make outbound HTTPS calls; n8n can join via push mode |
| Shard volume (10^4-10^5 Drive shards) | Each mapped task instance is a scheduler-managed task with per-instance overhead and a default `max_map_length` | Shards are rows claimed in batches with `SKIP LOCKED`; global concurrency cap in the database |
| Quotas and backpressure | Pools/slots per Airflow deployment | Per-job `max_concurrency` plus `paused_until` on upstream 429 |
| Operations | Scheduler, webserver, triggerer, executor, metadata DB | Existing `telescreen worker` processes and PostgreSQL |

Airflow remains usable from the outside: an Airflow DAG can act as a push-mode Police or
push masked findings through the ingest API.

### Map step scope in v1

The first map-reduce draft also allowed `source.items` (fan-out over a prior step's output
list) and `map.via: sub_workflow` (one child run per item). Both are dropped from v1.

- No accepted requirement needs them. The owner requirement is whole-estate inspection by
  collectors and Police, which `source.partition` with `via: collector|police` covers.
- Each would need its own undefined integration: child-run completion routed into
  `map_items`, and redaction and typing of item lists taken from step outputs.

They can be added later as new DSL fields without schema changes to the map tables, apart
from a nullable `child_run_id`.

### Sweep removal reconciliation by watermark

A sweep cannot list every resource it enumerated: that would mean storing locators, and
holding up to 10^7 keys. Instead, every scan of a resource sets `issues.last_checked_at`,
including incomplete and size-skipped scans. After a `succeeded` sweep, open issues of the
config with `sweep_covered = true` whose `last_checked_at` is older than the job's
`created_at` belong to resources the sweep never saw, and are auto-resolved.
`sweep_covered` (set by the adapter on each scan) keeps issues that sweeps never enumerate,
such as GitHub `refs/pull/*`, from being resolved by mistake. Those refs are exactly where
the incident workflow's Police re-check looks for leftover data.

Four reasons this is correct:

- Clean resources that still exist were already auto-resolved during the map phase by
  their complete scans.
- Resources that still have findings were touched.
- Resources that failed to fetch or scan inside an otherwise successful shard are recorded
  as incomplete scans for collectors, and counted in `resources_failed` for both collectors
  and Police. Any `resources_failed > 0` makes the sweep `partial`, so a sweep with
  resource-level failures never reconciles. Only an explicit `removed` entry (for example
  an upstream 404), or absence from a Police result that reports zero failures, counts as
  removal.
- A `partial` or `failed` sweep never reconciles, because a failed shard could hide a
  resource that still exists.

### Why the v1 queue polls instead of using LISTEN/NOTIFY

The issue suggests `FOR UPDATE SKIP LOCKED` plus LISTEN/NOTIFY. v1 keeps `SKIP LOCKED` but
uses a 1 s poll. Reasons:

- LISTEN needs a dedicated asyncpg connection outside SQLAlchemy sessions, and it has no
  SQLite equivalent, so offline tests could not cover it.
- The workloads (human tasks measured in hours, SLA timers, scans) do not need sub-second
  dispatch.
- Correctness never depends on notifications: a poller is needed anyway for timers
  (`available_at`) and lease recovery.

NOTIFY can be added later as a wake-up hint without schema changes.

### riela concept mapping

| riela concept | Telescreen equivalent | Difference |
|---------------|----------------------|------------|
| Workflow of nodes | Workflow definition with typed steps | YAML/JSON DSL `telescreen.workflow/v1` |
| Manager/worker roles | Engine worker claiming `engine_tasks`; police workers claiming `police_tasks` | Workers are processes, not LLM sessions |
| Branching | `condition` cases, outcome routing, `parallel`/`join` | Safe expression language, no eval |
| Loops | `send_back` and conditional back-edges | Persisted visit counters and `max_step_visits` |
| In-memory execution state | `workflow_runs`, `step_runs`, `transitions` rows | Durable; resumes after restart |

riela (https://github.com/tacogips/riela) is a conceptual reference only; no code is reused.

### Tooling compatibility notes

- ty 0.0.85 (pinned in `uv.lock`) can hang on recursive `type` aliases. `JsonValue` is a
  self-referencing frozen dataclass, never an alias. Pydantic's own `JsonValue` alias is not
  used either.
- SQLAlchemy: `MappedAsDataclass` is an allowed base in `tools/typelint.py`. Use
  `__tablename__: ClassVar[str] = "..."` so the annotation satisfies TL003 without becoming a
  dataclass field. Avoid `TypeDecorator` subclasses (they would need class-level typed
  attributes on a non-allowed base).
- FastAPI accepts pydantic dataclasses as body and response models. Endpoints that must see
  raw bytes (signatures, contract validation, evidence) take `Request` and parse with
  `TypeAdapter.validate_json`.
- Third-party calls that take mappings receive `dataclasses.asdict(...)` or literal dicts
  inline; nothing is bound to a mapping-typed variable.
- `anthropic` tool-use inputs are re-serialized with `json.dumps` inline and parsed into
  dataclasses with `TypeAdapter`.
- If ty reports a false positive in third-party typing (for example SQLAlchemy or `mcp`), the
  fix is a small typed adapter function or Protocol at the boundary, not a suppression.
- The gitleaks pre-commit hook scans staged files: test sample secrets are assembled from
  fragments at runtime, and examples contain only placeholders (`example.invalid`,
  `env:...` refs).
- bun is available through mise (`mise.toml` `[tools] bun = "latest"`).

---
