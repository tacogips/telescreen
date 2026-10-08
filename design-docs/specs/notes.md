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
