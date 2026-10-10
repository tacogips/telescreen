# V11-P02 Database Models and Idempotent Migration 0002_v1_1

**Status**: Ready
**Design Reference**: design-docs/specs/design-data-model.md ("v1.1 additions (revision `0002_v1_1`)"); design-docs/specs/design-v1-1-dspm-adoption.md sections 3-11
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p02-db-migration`
- planPath: `impl-plans/active/v11-p02-db-migration.md`
- wave: 1
- dependsOn: none (base: main at 289699e)
- writePaths:
  - `impl-plans/active/v11-p02-db-migration.md`
  - `src/telescreen/db/models`
  - `src/telescreen/db/migrations/versions/0002_v1_1.py` (new)
  - `tests/db/test_v11_migration.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Add the v1.1 columns and tables as ORM models, plus the single new Alembic revision.
Feature plans in wave 2 write these tables, so names and types are a contract. Copy them
exactly from design-data-model.md "v1.1 additions".

Critical repository fact: `src/telescreen/db/migrations/versions/0001_initial.py:upgrade`
creates tables from the CURRENT ORM metadata (`_models.AuditRecordRow.metadata`). On a
fresh database, 0001 will therefore already create every v1.1 table and column. On a
database that was migrated under v1, they are missing. `0002_v1_1` MUST be idempotent: it
inspects the live schema and adds only what is missing.

## Non-goals

- Never edit `0001_initial.py`.
- No repositories or services (feature plans own them). No change to `db/base.py`,
  `db/session.py` or `db/migrate.py`.
- Enum-like values are `String(32)` and get no DB CHECK constraints.

## Invariants

- Every new mapped column has a Python-side `default` (and `server_default` where it is
  NOT NULL). v1 code that constructs `IssueRow(...)`, `FindingRow(...)`, `TeamRow(...)` or
  `PoliceWorkerRow(...)` without them must still type-check and run.
- New columns are declared at the END of each model class. The migration parity test in
  `tests/db/test_migration.py` compares column order with metadata on a fresh database.
- Table and column names exactly as in the design.

## Deliverables

### 1. Models (`src/telescreen/db/models/`)

Imitate `db/models/issues.py:IssueRow` (`Mapped[...] = mapped_column(...)`,
`kw_only=True`).

`issues.py`:

- `IssueRow` appends:
  - `base_severity: Mapped[str] = mapped_column(String(32), default="medium", server_default="medium")`;
  - `exposure_visibility` (String(32), default and server_default `"unknown"`, `index=True`);
  - `exposure_json: Mapped[str | None]` (Text);
  - `exposure_changed_at` (DateTime(timezone=True), nullable);
  - `placement_keys_json` (Text, nullable);
  - `resource_modified_at` (DateTime(timezone=True), nullable);
  - `owner_ref` (String(80), nullable);
  - `policy_name` (String(64), nullable);
  - `policy_version` (Integer, nullable);
  - `risk_score` (Integer, default 0, server_default "0");
  - `asset_tier` (String(32), nullable);
  - `__table_args__` adds `Index("ix_issues_status_risk", "status", "risk_score")`.
- `FindingRow` appends `fingerprint_key_version: Mapped[int] = mapped_column(Integer,
  default=1, server_default="1")`, and `__table_args__` adds
  `Index("ix_findings_key_fingerprint", "fingerprint_key_version", "fingerprint")`.
- New `PlacementPolicyRow` (`placement_policies`): id, name String(64), version Integer,
  source_format String(16), source_text Text, compiled_json Text, effect String(16),
  enabled Boolean, active Boolean, owner_team String(128) nullable, created_by
  String(256), created_at, `UniqueConstraint("name", "version")`.
- New `IssueExceptionRow` (`issue_exceptions`): id, issue_id String(36), reason Text,
  duration String(32), status String(32), requested_by String(256), approver_ref
  String(256) nullable, decided_by String(256) nullable, decided_at nullable, expires_at
  nullable, created_at. Indexes `ix_issue_exceptions_issue` (issue_id) and
  `ix_issue_exceptions_status_expiry` (status, expires_at).

`directory.py`:

- `TeamRow` appends `lead_member_id: Mapped[str | None]` String(36), default None.
- New `MemberIdentityRow` (`member_identities`): id, member_id String(36) index, kind
  String(32), identity_key String(64), origin String(32), created_at,
  `UniqueConstraint("kind", "identity_key")`.

`collectors.py`:

- New `ResourceCoverageRow` (`resource_coverage`): id, collector_config_id String(36),
  resource_key String(64), source String(32), state String(32), reason String(32)
  nullable, truncated_bytes Integer default 0, exposure_visibility String(32) default
  "unknown", first_seen_at, last_attempt_at,
  `UniqueConstraint("collector_config_id", "resource_key")`.
- New `ScanCoverageRow` (`scan_coverage`): scan_job_id String(36) PK, state String(32)
  PK, reason String(32) PK (empty string when none), count Integer.
- New `CollectorHealthRow` (`collector_health`): collector_config_id String(36) PK,
  last_success_at, last_failure_at, consecutive_failures Integer default 0,
  last_event_at, watch_expires_at (all timestamps nullable), open_conditions String(256)
  default "", alerted_at nullable, updated_at.

`police.py`: `PoliceWorkerRow` appends `health_alerted_at` (nullable DateTime).

Export every new class from `db/models/__init__.py` in the existing style.

### 2. Migration `src/telescreen/db/migrations/versions/0002_v1_1.py`

- `revision = "0002_v1_1"`, `down_revision = "0001_initial"`. Typed module attributes as
  in 0001.
- `upgrade()`:
  1. `inspector = sa.inspect(op.get_bind())`.
  2. For each new table: if absent, create it from a `to_metadata` clone of the model
     table (imitate 0001's clone loop for that table only, including its indexes).
  3. For each new column of existing tables: if absent, `op.add_column` with the
     server_default.
  4. For each new index: create it if it is missing (`inspector.get_indexes`).
  5. Backfill with `op.execute(text(...))`: `UPDATE issues SET base_severity = severity`
     only for rows where the column was just added (when the column existed before,
     skip). `fingerprint_key_version` gets 1 from its server_default.
- `downgrade()`: drop the new indexes, columns (use `op.batch_alter_table` for SQLite
  column drops) and tables that exist.

## Pitfalls

- A non-idempotent `add_column` crashes on every fresh database (0001 already created the
  column). Test both paths.
- Do not import feature packages in the migration; use only `telescreen.db.models`.
- `server_default` values are strings (`"0"`, `"1"`, `"medium"`, `"unknown"`).
- `ScanCoverageRow.reason` is part of the PK, so it must be non-null (use `""`).

## Tests (`tests/db/test_v11_migration.py`)

- Fresh SQLite file: `upgrade(url)` reaches head `0002_v1_1`; all v1.1 tables and columns
  exist (inspect).
- v1-shaped database:
  1. Create the tables from a metadata clone with the v1.1 tables and columns removed.
  2. Insert one issue row with `severity="high"`.
  3. Stamp `0001_initial` (alembic `command.stamp` with the config built as in
     `telescreen.db.migrate`; read that module and imitate how it builds the Alembic
     config).
  4. Upgrade.

  Expected: the columns are added, `base_severity == "high"` for the row,
  `fingerprint_key_version` default 1, `risk_score` 0, `exposure_visibility` `"unknown"`.
- Downgrade to `0001_initial` on the fresh database removes the v1.1 tables; upgrading
  again succeeds.
- Model defaults: construct `IssueRow` with only the v1 fields -> `base_severity ==
  "medium"`, `risk_score == 0`.
- The existing `tests/db/test_migration.py` and `tests/db/test_models.py` must still pass
  unchanged.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/db` and `<tests>` = `tests/db`. Also
run the full offline suite:
`uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v11-p02-db-migration-pytest-all.log 2>&1; echo "exit=$?"`.
It must show `exit=0`, because v1 code is unaffected thanks to the defaults, with exactly
one tolerance. The concurrent wave-1 plans v11-p01 and v11-p03 may still be editing their
files. The only tolerated failures are tests that exercise or live in their owned paths:

- v11-p01: `src/telescreen/core/vocabulary.py`, `src/telescreen/core/config.py`,
  `src/telescreen/core/risk_weights.py`, `src/telescreen/ports`,
  `src/telescreen/masking/fingerprint.py`, `src/telescreen/masking/seal.py`,
  `src/telescreen/wiring_builders.py`, `src/telescreen/workflow/triggers/service.py`,
  `tests/core/test_v11_foundation.py`, `tests/masking/test_v11_fingerprint.py`,
  `tests/ports/test_v11_ports.py`;
- v11-p03: `src/telescreen/contracts/police/v1`, `src/telescreen_police_sdk`,
  `tests/contract`, `tests/police_sdk/test_v11_exposures.py`.

If a tolerated failure occurs: record each failing test id and the sibling path it
traces to in the Progress Log as informational, then rerun the full suite with one
`--deselect <test id>` per tolerated id, to
`/tmp/telescreen-v11-p02-db-migration-pytest-all-deselected.log`; it must show `exit=0`.
Any failure in a path this plan owns (including every `tests/db` test) blocks completion.
Per conventions 7a, a run with tolerated failures goes only in the Progress Log; only the
passing run or the passing deselected rerun goes in the step `verification` list.

## Completion Criteria

- [ ] All models and columns exist with the exact names; `db/models/__init__.py` exports
      them.
- [ ] `0002_v1_1` is idempotent (fresh and v1-shaped paths tested) and downgrades.
- [ ] Scoped gates pass; the typelint grep is empty; the full pytest exit is 0, or it fails
      only in the named v11-p01/v11-p03 paths and the deselected rerun shows `exit=0`.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
