# Inventory and alert boundary implementation

Status: COMPLETED
Design: [Inventory and alert boundaries](../../design-docs/specs/design-inventory-alert-boundaries.md)
Created / updated: 2026-10-14

## Scope

Implement the full modular-monolith boundary, including database separability, while
keeping inventory the asset master. No deployment as microservices is required.
The workstreams below are ordered dependencies, not a claim of completed extraction.

## Workstreams

### 1. Independent issue enrichment reads

Deliverables: `ports/asset_context.py`, `issues/asset_context.py`, inventory query and
retention callers, `wiring_v14.py`, boundary and existing behavior tests.
Status: COMPLETED
Validation: disjoint-database list/detail/owner test; 1,112 full-suite tests passed,
11 external-service skips; format, Ruff, typelint, ty and package build passed.

- [x] Introduce session-free issue and owner lookup contracts.
- [x] Wire issue-owned read adapters into inventory.
- [x] Verify separate-table tests and full regression suite.

### 2. Durable observation and reconciliation handoff

Deliverables: producer outbox, inventory inbox, ordered observation/reconciliation
contracts and adapters, Nodetto changes/exports, models and migrations, failure tests.
Status: COMPLETED
Validation: runtime end-to-end flows, fault/recovery tests, five PostgreSQL concurrency
and cross-role ownership tests; full PostgreSQL-enabled suite 1,153 passed / 6 skipped.

- [x] Define transport-independent, versioned commands for all four producer operations.
- [x] Add producer outbox/allocator, inventory inbox/cursor, applier and dispatcher.
- [x] Verify rollback, lost acknowledgment, duplicate/conflict, gap and stale-scan behavior
  with disjoint SQLite databases.
- [x] Implement producer adapters with config-before-stream locking and register runtime delivery.
- [x] Replace cross-module observation and reconciliation transactions.
- [x] Prove no lost inventory updates or premature absence reconciliation.

### 3. Inventory master and supporting dependency isolation

Deliverables: session-free identity/config/audit/workflow contracts and adapters;
master ownership and import-boundary tests; owner-local numbering.
Status: COMPLETED
Validation: owner-only master and allocation tests, import gates, migrations and PostgreSQL allocation/replay checks; full regression verification recorded below.

- [x] Isolate actor display and metadata reference validation behind session-free identity lookups.
- [x] Commit inventory-local audit intent and deliver through an idempotent audit-owned receiver.
- [x] Remove remaining supporting table/session leakage from inventory operations.
- [x] Enforce inventory as the only asset master writer.

### 4. Retention to notification handoff

Deliverables: inventory due-date outbox events, notification consumer and deduplication,
updated runtime handlers and tests.
Status: COMPLETED
Validation: split-store fault/policy tests, PostgreSQL concurrency/privilege test and assembled outage recovery pass; full PostgreSQL suite 1,197 passed / 6 skipped.

- [x] Move delivery responsibility and delivery state into notification management.
- [x] Preserve overdue semantics without cross-module transactions.

### 5. Persistence ownership, migration and final verification

Deliverables: module session/engine configuration, migration ownership, backfill and
cutover procedure, split-store end-to-end tests, architectural gates and updated docs.
Status: COMPLETED
Validation: configured stores, non-owner PostgreSQL runtime grants, migrations/adoption, real pending-work cutover/rollback and all 26 audited requirements pass; final CI/build/schema/web checks pass.

- [x] Configure independently owned stores with safe legacy defaults.
- [x] Prove runtime/API/worker behavior with disjoint database privileges.
- [x] Freeze legacy history and implement verified schema adoption.
- [x] Implement verified data copy/cutover/rollback preserving pending handoffs.
- [x] Audit every design acceptance criterion and record evidence.

## Status table

| Workstream | Status | Validation |
| --- | --- | --- |
| Independent reads | COMPLETED | Split-table test, full suite and static checks pass |
| Durable handoff | COMPLETED | Runtime, fault/recovery and PostgreSQL concurrency/privilege checks pass |
| Master/support isolation | COMPLETED | Owner-only operations, master authority, atomic numbering, migration and import gates pass |
| Retention delivery | COMPLETED | Split-store policy/fault, PostgreSQL concurrency, runtime outage and full regression checks pass |
| Persistence and audit | COMPLETED | Configured stores, non-owner grants, migration/adoption, cutover/rollback and all acceptance/quality checks pass |

## Dependencies

Read contracts precede store separation. Durable handoff precedes changing write stores.
Retention and supporting dependencies must be isolated before claiming extraction ready.
DDL work must follow the repository's Nodetto source-of-truth workflow.

## Completion criteria

- [x] All five workstreams verified.
- [x] Every acceptance item in the design has direct evidence.
- [x] Full checks pass; migration/cutover and rollback are documented and tested.

## Progress log

Earlier entries: [progress archive](../logs/inventory-alert-boundaries-2026-10-14.md).

### 2026-10-14, configured owner stores and migrations

Added `TELESCREEN_INVENTORY_DATABASE_URL`, with shared legacy factory defaults and
caller-owned injection precedence. The container owns and closes a distinct configured
inventory engine. Data routes and inventory handlers use it; support adapters remain on
main. CLI `migrate` selects independent histories for distinct URLs; `--store` migrates
one owner without connecting to the other store. Existing shared defaults retain the
legacy migration command.

Packaged frozen PostgreSQL/SQLite owner baselines and separate owner version tables.
They preserve all 62 tables' owned columns/indexes/defaults, allocation/audit seeds and
PostgreSQL append-only triggers, without importing live models or ownership lists.
Existing owner tables require explicit adoption rather than implicit baseline creation.
SQLite DDL, seeds and stamps roll back together after an injected late failure. Tests
cover repeat upgrades, independent downgrade, future ORM drift and legacy refusal.

The full assembled API/worker flow now runs with configured SQLite URLs and complete
PostgreSQL stores migrated by separate restricted login owners. It covers registration,
enrichment, ingestion, read/export APIs, numbering, worker projection, audit and retention.
Actual cross-schema queries fail under these roles. Pool/default/injection ownership and
the real migration CLI are covered separately. The built wheel was extracted outside
the checkout and successfully migrated both owners using its packaged SQL resources.

Verification: 1,228 passed, six external-service skips in the full PostgreSQL-enabled
suite (`/tmp/telescreen-owned-stores-full-pg.log`). Format, Ruff, typelint (675 files),
ty, canonical schema/exports/model checks, build and diff checks pass. Temporary
PostgreSQL is stopped. Goal remains active: freeze the legacy graph, implement checked
adoption and data copy/cutover/rollback preserving pending handoffs, then audit every
design acceptance criterion. No user input is blocking progress.

### 2026-10-14, frozen legacy history and checked owner adoption

Revisions 0001 through 0005 and the legacy environment no longer import live ORM models.
Immutable definitions reproduce each historical target's tables, columns, constraints,
indexes, defaults and autoincrement policy. Later revisions retain their explicit DDL and
backfills. Genuine old targets now upgrade to head without manually removing future
columns. Tests also prove that future ORM edits cannot rewrite either migration history.

Added explicit `migrate --store main|inventory --adopt --writers-stopped`. It validates
existing schema against a frozen owner baseline, requires initialized allocation state
and a matching main audit head, and stamps only migration state. PostgreSQL also checks
the enabled append-only trigger and its exception function. Owner/version locks precede
validation; SQLite validation and stamp DDL share an immediate transaction. Late stamp
failure rolls back cleanly. Repeat adoption preserves records and allocation high-water
marks. Ordinary migrate still refuses implicit adoption.

PostgreSQL cases execute the entire legacy graph under an isolated login owner and adopt
both owners, validate the independent baseline stores, and reject width/default/nullability
and trigger-policy drift. SQLite cases cover missing indexes, extra columns, missing seeds,
bad audit head, wrong legacy revision, stamp rollback and CLI preconditions. A further
test detects a filtered index whose name and columns match the expected full index.

Full PostgreSQL-enabled suite: 1,254 passed, six external-service skips
(`/tmp/telescreen-frozen-adoption-full-pg.log`). The additional filtered-index case then
passed with all eleven adoption tests. Format, Ruff, typelint (680 files), ty, schema checks
and build pass. The extracted wheel performs a frozen old-target upgrade, both adoptions
and both owner baselines outside the checkout. Temporary PostgreSQL is stopped.

The goal remains active. Schema adoption does not copy data: implement inventory transfer,
manifest comparison, stopped-writer cutover and rollback after new inventory writes,
preserving IDs, numbers, field revisions and all inbox/outbox state. Then audit all design
acceptance criteria. No user input is blocking progress.

### 2026-10-14, inventory transfer and authoritative rollback

Implemented `inventory-store copy|verify --direction split|merge --writers-stopped`.
The tool validates known migration state, exact schema and allocation safety, locks both
inventory stores, and compares canonical count/content manifests for every owned column
and row. Initial copy permits an empty target or an identical completed retry. The source
is unchanged; replacement and full target verification share one transaction. Only
inventory tables move; main producer outbox, queue, identity and audit/notification
receipts remain in main. CLI output contains hashes/counts, not URLs or business values.

Authoritative merge restores mutable metadata and physical value removal while checking
old asset identities, immutable event/receipt/outbox lineage and monotonic allocation,
stream cursors and acknowledgment. Failed or corrupt replacement rolls back completely.
PostgreSQL replacement changes its user event trigger inside the same transaction and
restores policy before commit. A real mid-copy failure proves both DML and trigger state
roll back under owner credentials. Initial server-side cursor testing exposed active
portals during trigger DDL; bounded binary-text keyset reads now avoid that conflict.
Multi-page composite-key and SQLite/PostgreSQL round-trip cases verify ordering and UTC
timestamp preservation.

The assembled application now runs shared -> split -> shared on SQLite and PostgreSQL.
It carries a lost-acknowledgment inbox receipt, an unconsumed observation and pending
audit/retention intent through copy; resumes each effect once; accepts new assets and a
new field revision; then merges back and allocates the next number without duplicate
notification. Adopted shared deployments now continue their owner migration histories,
rather than accidentally replaying the legacy creator. The full operating procedure is
in `design-docs/specs/design-inventory-store-cutover.md`, linked from command/design docs.

Final PostgreSQL-enabled suite: 1,276 passed, six external-service skips
(`/tmp/telescreen-cutover-full-pg-final.log`). Format, Ruff, typelint (689 files), ty,
canonical schema/export/model checks, build and diff checks pass; every touched Python
file is below 1000 lines. The extracted wheel performs nonempty copy, verification and
merge through the real CLI outside the checkout. Temporary PostgreSQL is stopped.

The goal remains active for the final requirement-by-requirement completion audit,
including checking that runtime privilege evidence matches the documented deployment
and cutover boundaries. No production data was moved; no user input is blocking work.

### 2026-10-14, completion audit

Completed all five workstreams and the 26-item requirement audit in
`design-docs/specs/design-inventory-boundary-verification.md`. Added a complete API/worker
test with non-owner DML-only runtime logins and retained old inventory tables in main;
actual reads of those tables, remote schema access, migration-state reads and trigger
DDL are denied. Added transport framing limits (16 MiB UTF-8 and 100,000 entries per
inventory collection) with exact-boundary, malformed/oversized wire and atomic producing
rollback tests. Full snapshots are rejected, never truncated. Tightened PostgreSQL
serial-default validation to verify the column-owned sequence, with a rejecting test.

Final verification: PostgreSQL-enabled `PYTEST_ADDOPTS=-q mise run ci` -> exit 0,
1,288 passed / six opt-in live/Meilisearch skips, format/Ruff/typelint (693 files)/ty
and sdist/wheel build passed (`/tmp/telescreen-boundary-final-ci-current.log`).
`mise run ddl-check` -> exit 0, 10 tests passed and exports canonical/current
(`/tmp/telescreen-boundary-final-ddl.log`). `mise run web-ci` -> exit 0, typecheck,
262 tests and build passed (`/tmp/telescreen-boundary-web-ci.log`). Final extracted-wheel
smoke -> exit 0 for limits, legacy/owner migrations, adoption, nonempty copy/verify/merge
and shared-owner routing. Diff and touched-Python size checks pass.

The initial unquiet CI attempt had one 10,000-shard throughput failure (250.5 seconds
versus 240); all other 1,286 tests passed. The unchanged test passed alone in 72.5 seconds
and in the final complete rerun. This superseded timing attempt is recorded rather than
treated as current passing evidence. No test/threshold was weakened.

The boundary design, architecture index, command index and operating procedure now
describe the implemented result and its asynchronous/size/replay/stopped-writer semantics.
Every acceptance criterion has direct code/test/command evidence; no required work
remains. Temporary PostgreSQL is stopped. No production data was moved, and no commit,
deployment or message to an external party was performed.
