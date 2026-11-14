# P02 DB Schema Implementation Plan

**Status**: Completed
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-p02-db-schema-typelint.log 2>&1; ! grep -E '^(src/telescreen/db|tests/db|tests/support/db.py)' /tmp/telescreen-p02-db-schema-typelint.log
```

## Completion Criteria

- [x] 27 tables with the exact names; all `*Row` classes exported from `telescreen.db.models`
- [x] Migration and metadata parity test passes; PostgreSQL trigger present (integration test written)
- [x] `tests/support/db.py` harness seeds the counter and the chain head
- [x] Table dunders are unannotated: `grep -rnE '__(tablename|table_args)__ *:|ClassVar' src/telescreen/db` prints nothing
- [x] Scoped verification commands pass; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 20:08 JST
**Tasks Completed**: Added all 27 ORM rows and exports, async session helpers, replay nonce storage, programmatic Alembic migration, PostgreSQL audit immutability trigger, SQLite harness and scoped tests. Updated completion criteria.
**Tasks In Progress**: None
**Blockers**: None. PostgreSQL integration test is present and skipped by the offline suite because `TELESCREEN_TEST_DATABASE_URL` is unset.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/db/__init__.py` before=absent after=210d9f7c01e252ae27a89ad45fc0e89a33cd4d016241c9cf5ee9e70285baa385; `base.py` before=absent after=c8badf5ed4d9e3ec94a431e5cbcb230cd31e79355661953ac6ee0d244586ba58; `session.py` before=absent after=90b7055f32b986831a93686347eaa72cadefec9a1b618bcbf37a2991c9bacc72; `migrate.py` before=absent after=53cc3c2923557e2b66c5702aadad2a75bae028444c4c7e469859c7e3a8467212; `replay.py` before=absent after=cdb07442116c1950a51ec57b54ab686cd6dc8c4f48e56d395ae2f83e5f0ad282; `migrations/env.py` before=absent after=9973e4cf45e40884572c012c5d57c47de967a635dbc04ea377a980682ea29cbd; `migrations/script.py.mako` before=absent after=45a7779dddd9250c6637afa91e21b23ad9ec352b9791b4a19dc78e3a7bbf6d4d; `migrations/versions/0001_initial.py` before=absent after=28cdc53f6acea544e1c78acce26260eb6a4d64dfb984eca92b6b3339c4dbc005; `models/__init__.py` before=absent after=0e651583b35ac657e795f581853192320f9eb01358abfa99850f0388e1c0ee67; `models/audit.py` before=absent after=4328a02070067fdc8f7622df40a30d43e9a7c240ec23bf22e6a55957b52c21eb; `models/collectors.py` before=absent after=30febff167c30c2e0755ebda86b40e2e5597c8aa751e7c56963be3b23ff9cdaf; `models/directory.py` before=absent after=65c62f5fdbaf818e511d71b6f8b2c32fa53a06739bfdc6175e26c43f5e606d1e; `models/evidence.py` before=absent after=082a612d830b724cba888a5707b7ed917b54b2dce2521482f7194335138767f9; `models/issues.py` before=absent after=2ec07e7db406957492ce0fcf3115a0f0f075a770274ce684ba3534deaf79c9e0; `models/mapreduce.py` before=absent after=c78b7c2388c34f11d10a513da589b7d40c796862ee5f14653fd13168cf5ef74f; `models/notifications.py` before=absent after=4802002f12e1633892516e097c0743516a2b35720e3e2819fc34b9d65f8d71e7; `models/police.py` before=absent after=13a2fccd97d1f5eaca85f76c4c562a07c28a595e8e50bc16a009fd69bc2a0deb; `models/workflow.py` before=absent after=2b008110ea4436381c8ce910e2e1922052b9b3a9da34164b57f710caa4218ef9; `tests/db/test_migration.py` before=absent after=38b465a9b2aa6415687449b0da4a63d5a32305e9745ab5ff19cd03b142773ac8; `tests/db/test_models.py` before=absent after=ea1d3b520141810e96ed58ad9a8552acfce320edf4db30865046e12ea8fbf06f; `tests/db/test_replay.py` before=absent after=8a9a6358fc579b3da65b73028b9745ac3123c057a77420f60553850e5936ba6c; `tests/support/db.py` before=absent after=71fcaf4a0b57a189f5cbff406dc25e8786af93e53b0a8582f562f18edf3a0c12; `impl-plans/active/p02-db-schema.md` before=33c2060b9272db3bed0fa7540154a4bce6517ab24446eb7af5fe1a0172a8b1b4 after=96c6c2eabecb8707819b8277ccd0a20ac106dfc9c3a7bc88480cd2ffe0f9a886.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/db tests/db tests/support/db.py` -> exit 0 (log: `/tmp/telescreen-p02-db-schema-format.log`); `uv run --frozen ruff check --no-cache src/telescreen/db tests/db tests/support/db.py` -> exit 0 (log: `/tmp/telescreen-p02-db-schema-ruff.log`); `uv run --frozen ty check src/telescreen/db tests/db tests/support/db.py` -> exit 0 (log: `/tmp/telescreen-p02-db-schema-ty.log`); `uv run --frozen pytest -p no:cacheprovider tests/db` -> exit 0, 5 passed / 1 skipped (log: `/tmp/telescreen-p02-db-schema-pytest.log`); `uv run --frozen python tools/typelint.py` plus p02 path filter -> exit 0, no p02 diagnostics (log: `/tmp/telescreen-p02-db-schema-typelint.log`); `! grep -rnE '__(tablename|table_args)__ *:|ClassVar' src/telescreen/db` -> exit 0, no matches (log: `/tmp/telescreen-p02-db-schema-dunders.log`).
**Notes**: SQLite migration parity passed. The model test inserts a minimal typed row into every metadata table. The PostgreSQL update/delete trigger integration test is available but could not execute without its configured database URL.

### Session: 2026-10-08 test-integrity repairs
**Tasks Completed**: Resolved P02-TI-01 by annotating `__all__`, `AsyncEngine` bindings, and `SqliteHarness` bindings. Resolved P02-TI-02 by bounding the PostgreSQL audit sequence to int32 and matching both expected trigger errors to `append-only`.
**Tasks In Progress**: Independent integrity/adversarial re-review.
**Blockers**: None for this plan. PostgreSQL integration remains skipped because `TELESCREEN_TEST_DATABASE_URL` is unset; its sequence is at most 1,999,999,999 and generated id is within `String(36)`.
**Reconciliation requests**: None.
**Finding correction**: The earlier “No p02 diagnostics” claim was a false pass: the anchored grep did not match absolute paths from typelint. The full typelint rerun is clean (68 files), and the corrected unanchored p02-path filter prints no matches.
**Hashes**: `src/telescreen/db/models/__init__.py` 6910f6c05a5ee353bdfc5796205a0d757240dcbce08a564f72e02d0268948973; `tests/db/test_migration.py` cd213efaa8b1d4ddc3d9799414bcca32ae6bcf585afddba717111100d90cfac8; `tests/db/test_models.py` 737d2af09e30890e4b3c83a42a62dba107373d021d722a81f96b73f415048421; `tests/db/test_replay.py` 6ed8bfd44a6be2e7411bd172185d62593817ca8ccf3665981d90a9cbc5583816.
**Verification**: Scoped ruff format -> exit 0 (`/tmp/telescreen-p02-repair-format-final.log`); scoped ruff check -> exit 0 (`/tmp/telescreen-p02-repair-ruff-final.log`); scoped ty -> exit 0 (`/tmp/telescreen-p02-repair-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/db` -> exit 0, 5 passed / 1 skipped (`/tmp/telescreen-p02-db-schema-pytest.log`); full `uv run --frozen python tools/typelint.py` -> exit 0, 68 files clean (`/tmp/telescreen-p02-db-schema-typelint.log`); unanchored p02-path grep -> exit 0, no matches; ORM dunder/ClassVar grep -> exit 0, no matches.
