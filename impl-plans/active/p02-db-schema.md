# P02 DB Schema Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-data-model.md, design-docs/specs/design-map-reduce-sweeps.md (Data model), design-docs/specs/architecture.md section 5 item 3
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p02-db-schema`
- planPath: `impl-plans/active/p02-db-schema.md`
- wave: 2
- dependsOn: `p01-foundation`
- writePaths:
  - `impl-plans/active/p02-db-schema.md`
  - `src/telescreen/db`
  - `tests/db`
  - `tests/support/db.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Provide every v1 table as a SQLAlchemy 2 async ORM model, plus one Alembic revision, the
session factory, the shared replay-nonce store and an SQLite test harness. Each feature
plan writes its own repositories on top of these models, so table names, column names
and class names are a cross-plan contract. They are pinned here and in
`design-data-model.md`.

## Non-goals

- No repositories or business logic (those belong to the feature plans).
- No root `alembic.ini`; migrations run programmatically.
- No `TypeDecorator` subclasses, no JSONB, no database enums. Enum values are stored as
  `String(32)` holding the `StrEnum` value.
- No `ForeignKey` constraints in v1 (plan decision: ids are indexed `String(36)` columns;
  services keep integrity). This keeps per-plan fakes and SQLite tests independent of
  insert order.

## Deliverables

| File | Contents |
|------|----------|
| `src/telescreen/db/__init__.py` | docstring |
| `src/telescreen/db/base.py` | `class Base(MappedAsDataclass, DeclarativeBase)` |
| `src/telescreen/db/models/__init__.py` | imports every model module so `Base.metadata` is complete; re-exports all `*Row` classes |
| `src/telescreen/db/models/directory.py` | `MemberRow`, `TeamRow`, `TeamMembershipRow`, `ApiTokenRow`, `CounterRow` |
| `src/telescreen/db/models/collectors.py` | `CollectorConfigRow`, `ScanJobRow`, `ReplayNonceRow` |
| `src/telescreen/db/models/issues.py` | `IssueRow`, `FindingRow`, `IssueHistoryRow` |
| `src/telescreen/db/models/workflow.py` | `WorkflowDefinitionRow`, `EscalationPolicyRow`, `WorkflowRunRow`, `StepRunRow`, `TransitionRow`, `EngineTaskRow`, `HumanTaskRow` |
| `src/telescreen/db/models/mapreduce.py` | `MapJobRow`, `MapPartitionRow`, `MapItemRow` |
| `src/telescreen/db/models/notifications.py` | `NotificationRow` |
| `src/telescreen/db/models/evidence.py` | `EvidenceRow` |
| `src/telescreen/db/models/police.py` | `PoliceWorkerRow`, `PoliceTaskRow` |
| `src/telescreen/db/models/audit.py` | `AuditRecordRow`, `AuditChainHeadRow` |
| `src/telescreen/db/session.py` | `create_engine(url: str) -> AsyncEngine`; `create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`) |
| `src/telescreen/db/migrate.py` | `upgrade(database_url: str, revision: str = "head") -> None` (synchronous; builds an Alembic `Config` in code with `script_location` = the package's `migrations` dir) |
| `src/telescreen/db/migrations/env.py`, `script.py.mako`, `versions/0001_initial.py` | async Alembic env (`connection.run_sync`); revision `0001_initial` creates every table and index explicitly with `op.create_table` |
| `src/telescreen/db/replay.py` | `ReplayNonceStore` dataclass: `async def remember(self, session: AsyncSession, scope: str, nonce: str, expires_at: datetime, now: datetime) -> bool` (True when stored now; False when the pair already exists and has not expired; deletes expired rows of that scope first) |
| `tests/support/db.py` | `SqliteHarness(engine, sessions)` dataclass with `async close()`; `async def make_sqlite_harness() -> SqliteHarness` (aiosqlite in memory, `StaticPool`, `create_all`, seeds the counters row `issue_number`=0 and the audit chain head `id=1, seq=0, hash="0"*64`) |

Table and column rules (everything not listed comes verbatim from `design-data-model.md`
and the map-reduce data model):

- Table names: `members`, `teams`, `team_memberships`, `api_tokens`, `counters`,
  `collector_configs`, `scan_jobs`, `replay_nonces`, `issues`, `findings`,
  `issue_history`, `workflow_definitions`, `escalation_policies`, `workflow_runs`,
  `step_runs`, `transitions`, `engine_tasks`, `human_tasks`, `map_jobs`,
  `map_partitions`, `map_items`, `notifications`, `evidence`, `police_workers`,
  `police_tasks`, `audit_records`, `audit_chain_head`.
- Attribute names equal column names exactly. A JSON column keeps the `_json` suffix.
  List-valued columns (`api_tokens.scopes`, `police_workers.task_types`,
  `police_workers.labels`, `police_tasks.labels`, `evidence.rejection_rule_classes`) are
  `Text` holding comma-separated values.
- Types: ids `String(36)`; enum values `String(32)`; free text `Text`; counters
  `Integer`; `confidence` `Float`; flags `Boolean`; timestamps `DateTime(timezone=True)`;
  `audit_records.seq` `Integer` primary key (no autoincrement; the service assigns it);
  `audit_chain_head.id` `Integer` primary key.
- Additions confirmed by the accepted design:
  - `issues`: `last_checked_at` (DateTime, not null) and `sweep_covered` (Boolean, not
    null), plus INDEX(`collector_config_id`, `status`, `last_checked_at`).
  - `map_items`: `scan_job_id` (nullable).
  - `map_partitions`: `police_task_id` (nullable) and UNIQUE(`map_job_id`, `corpus_key`).
  - `map_jobs`: `collector_config_id` NOT NULL.
  - `scan_jobs.trigger` may hold `sweep` (`ScanTrigger.SWEEP`).
- Unique constraints: as the data model states. That includes `findings` UNIQUE(issue_id,
  scan_job_id, fingerprint), `step_runs` UNIQUE(run_id, step_id, visit, branch_token),
  `engine_tasks.idempotency_key`, `human_tasks.step_run_id`, `issues.grouping_key`,
  `issues.number`, `api_tokens.token_hash`, `workflow_definitions` UNIQUE(name, version)
  and `replay_nonces` PK(scope, nonce).
- Extra plain indexes (performance only): `findings(issue_id)`, `issues(resource_key)`,
  `issues(status, last_seen)`, `engine_tasks(status, available_at)`,
  `engine_tasks(ref_id)`, `step_runs(run_id)`, `transitions(run_id)`,
  `human_tasks(status, due_at)`, `police_tasks(status)`, `police_tasks(step_run_id)`,
  `notifications(status)`, `evidence(issue_id)`, `audit_records(at)`,
  `workflow_runs(issue_id)`, `map_items(police_task_id)`.
- On PostgreSQL only (check `op.get_bind().dialect.name == "postgresql"`), the migration
  installs a trigger function and trigger that raise on UPDATE or DELETE of
  `audit_records`. The migration also inserts the `counters` row (`issue_number`, 0) and
  the `audit_chain_head` row (`1`, `0`, 64 zeros) on every dialect.

## Key Pitfalls

- `MappedAsDataclass` field order: fields without defaults come first. Nullable columns use
  `default=None`; timestamps the service sets have no default. Do not use `init=False` for
  primary keys: services pass `new_id()`.
- Write `__tablename__ = "..."` and `__table_args__ = (...)` unannotated. typelint TL003
  exempts class-body dunder attributes. Do NOT annotate them: ty 0.0.85 reports
  `invalid-attribute-override` for `ClassVar[...]`, and `__tablename__: str = ...` becomes a
  dataclass field with a default, so every required column after it fails
  `dataclass-field-order`. Verified by spike on 2026-10-08.
- `Base` itself declares no typed fields.
- `env.py` must not read `alembic.ini` or logging config. It reads the URL from
  `config.get_main_option("sqlalchemy.url")`, which `upgrade` sets.
- `upgrade` calls `asyncio.run` inside Alembic. Async tests must call it through
  `await asyncio.to_thread(upgrade, url)`.
- The SQLite migration test uses a temp file URL (`sqlite+aiosqlite:///<tmp>/t.db`),
  because in-memory databases do not survive across connections.
- Keep every file under 1000 lines (split models by area as listed).

## Tests (`tests/db/`)

- `make_sqlite_harness()` creates all 27 tables; each `*Row` can be inserted with minimal
  values and read back; the seeded counter and chain head exist.
- Migration on a temp SQLite file -> the set of tables and the columns per table equal
  `Base.metadata` (compare names only).
- `ReplayNonceStore.remember`: first call True; second call with the same scope and nonce
  False; after expiry (FakeClock time past `expires_at`) True again.
- Inserting two `findings` rows with the same (issue_id, scan_job_id, fingerprint) ->
  `IntegrityError`.
- `@pytest.mark.integration` (uses `require_env("TELESCREEN_TEST_DATABASE_URL")`):
  upgrade on PostgreSQL; insert an audit record; UPDATE and DELETE both raise.

## Verification

Follow conventions section 7 with `<src>=src/telescreen/db`, `<tests>=tests/db tests/support/db.py`:

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/db tests/db tests/support/db.py
uv run --frozen ruff check --no-cache src/telescreen/db tests/db tests/support/db.py
uv run --frozen ty check src/telescreen/db tests/db tests/support/db.py
uv run --frozen pytest -p no:cacheprovider tests/db 2>&1 | tee /tmp/telescreen-p02-db-schema-pytest.log   # all pass; integration skipped
uv run --frozen python tools/typelint.py > /tmp/telescreen-p02-db-schema-typelint.log 2>&1; grep -E '^(src/telescreen/db|tests/db|tests/support/db.py)' /tmp/telescreen-p02-db-schema-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] 27 tables with the exact names; all `*Row` classes exported from `telescreen.db.models`
- [ ] Migration and metadata parity test passes; PostgreSQL trigger present (integration test written)
- [ ] `tests/support/db.py` harness seeds the counter and the chain head
- [ ] Table dunders are unannotated: `grep -rnE '__(tablename|table_args)__ *:|ClassVar' src/telescreen/db` prints nothing
- [ ] Scoped verification commands pass; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
