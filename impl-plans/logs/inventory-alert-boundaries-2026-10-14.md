# Inventory and alert boundary progress archive

Historical entries for the [active plan](../active/inventory-alert-boundaries.md).
Later entries supersede their statements about remaining work.

### 2026-10-14

Inspected the clean worktree and identified shared-session coupling in read enrichment,
observation, reconciliation and retention. Added the boundary design, session-free read
contracts and issue-owned adapters. Full objective remains in progress; no split-store
runtime or durable handoff is claimed yet.

Read-boundary verification: `uv run pytest -q` -> 1,112 passed, 11 skipped
(`/tmp/telescreen-boundary-pytest-verified.log`). Focused inventory/API/ports checks
also passed. The first end-to-end attempt exposed connection aliasing in the in-memory
SQLite fixture. Added explicit `independent_sessions=True` for v1.4 assembled tests,
using file-backed SQLite. Expanding that fixture globally exposed unrelated legacy
multi-writer SQLite assumptions, so other suites retain their existing fixture mode.
All 13 affected end-to-end/leak tests passed after the targeted fixture change.

Added persistence-free observation types, backward-compatible legacy re-exports, and
versioned write commands with per-stream ordering metadata and captured inventory mode.
Fifteen command tests cover all operations, invalid envelopes, strict version types
and ORM-free imports.
No DDL, outbox/inbox, consumer or runtime write wiring has changed yet. Final full-suite
verification after the value-type extraction passed: 1,124 tests, 11 external-service
skips (`/tmp/telescreen-boundary-pytest-current.log`). The subsequent schema-version
strictness correction passed all 15 command tests. Format, Ruff, typelint and ty pass;
`mise run ddl-check` passed (10 tests). The schema document and exports are unchanged.

### 2026-10-14, durable handoff foundation

Added the four handoff tables in Nodetto, drew stream references field to field as
logical many-to-one relations, ran tidy layout, regenerated both DDL exports, and added
matching models plus `0006_inventory_handoff`. The application UI's native selector and
external-save conflict behavior required checking the saved document; final reference
and schema checks pass. No SQL-only or model-only schema edit was used.

Implemented producer-local sequence allocation/outbox insertion, inventory-local
transactional inbox/receipt/cursor application, and detached delivery with acknowledgment
only after consumer commit. Delivery selects one head per stream so a failed stream's
backlog cannot hide another selected stream. Added safe static-code failure logging.
Observation application now accepts captured inventory policy without querying collector
tables; reconciliation accepts captured policy and occurrence times. Older source
observations cannot reverse a newer lifecycle watermark.

Evidence: eight real-business split-store tests cover producer rollback, consumer rollback,
lost acknowledgment, duplicate/conflicting identity, sweep gaps, lifecycle replay, captured
off mode, and recovery beside an unavailable stream. The incremental migration test proves
upgrade/downgrade/repeated upgrade preserve an existing asset master. Existing metadata
contracts now include all 57 tables. `uv run pytest -q` -> 1,136 passed, 11 external-service
skips (`/tmp/telescreen-handoff-pytest.log`). Format, Ruff, typelint, ty, `mise run ddl-check`
(10 tests) and `uv build` pass.

Next: implement producer adapters and ensure policy reads are serialized with collector
tracking changes before stream allocation; verify this with PostgreSQL concurrency tests.
Then switch all four producer paths and register durable delivery in the runtime. Runtime
still uses synchronous observation/reconciliation, so workstream 2 and the full goal remain
in progress. Identity/config/audit/notification isolation and runtime store ownership also
remain outstanding. No blocker requires user input.

### 2026-10-14, producer and worker integration

Completed workstream 2. `collectors/inventory_publish.py` now stages observations,
sweep/listing completion and stop-tracking in the producer outbox and engine queue.
It locks/refetches collector policy before stream allocation. Collector configuration
updates acquire that same first lock before reading defaults or changing tracking.
`wiring.py` binds all four producer paths to this adapter; `wiring_v14.py` registers
`InventoryDeliveryHandler` and `InventoryRecoveryProvider` with the existing worker.
Recovery rotates stream heads, skips active retries and gives exhausted commands new
minute-scoped recovery tasks. Event-specific delivery preserves ID and sequence when
retrying a gap or a lost receipt. Public inventory command contracts remain ORM-free.

Added six publisher/worker/recovery tests and an assembled outage/recovery test.
Updated inventory end-to-end, leak and API-shape tests to wait for actual worker delivery
before asserting projections. The assembled outage test separately asserts the issue
commit and absent inventory before delivery, failed delivery without issue rollback,
and successful catch-up after recovery. Updated command documentation for this
consistency behavior and the requirement to run the existing worker continuously.

Ran five PostgreSQL tests against a temporary PostgreSQL 16.14 instance using two
schemas with different login owners. They prove concurrent first-stream allocation,
serialized policy publication, duplicate receiver races, actual cross-schema permission
denial and inventory rollback. The policy race observes `pg_stat_activity` lock waiting
before releasing stop-tracking, then verifies the stale cached mode is refreshed to OFF.
The complete PostgreSQL-enabled regression suite passed: 1,153 tests, six live/Meilisearch
skips (`/tmp/telescreen-runtime-handoff-full-pg-final.log`). The initial full run exposed
an unrelated audit test fixture generating values above int32; constrained its generated
sequence to the actual schema range so it verifies the append-only trigger as intended.

The full goal remains active. Remaining dependency inventory: `inventory/actors.py`
and `queries.py` pass inventory sessions into actor/config reads; `values.py` passes one
into reference validation; inventory services append audit entries through the caller
session; `retention.py` directly reads `TeamRow` and invokes notification services;
data routers use `ApiContext.sessions` for both authentication and data. Next workstream
is isolating these owner-specific operations, then retention delivery and production
store/migration configuration. The current role tests prove the write handoff only,
not the complete split-store API/retention runtime. No user input is blocking progress.


### 2026-10-14, identity lookup isolation

Started workstream 3. Added ORM-free immutable `ActorDisplay`/`AssigneeIds` DTOs and
session-free `ActorLookup`/`AssigneeLookup` contracts in `ports/identity_context.py`.
The existing directory port reexports the DTOs for compatibility. Identity-owned
adapters open and close their own sessions; runtime queries wrap collector actor
resolution in that adapter. Asset list/detail/timeline queries no longer pass inventory
sessions into actor/config lookup. Metadata validation no longer accepts a session at
all; field-value callers and API validation use the new signature. Reference errors
continue to translate only `RECIPIENT_UNKNOWN` into `unknown_reference`.

A separate-file-database test executes real directory member/team reads while an
inventory field insert is uncommitted, verifies duplicate/stale actor references and
member/team validation, and confirms the inventory insert commits. Inventory has only
its field table; the identity database has only member/team/token tables. The existing
subprocess contract test also verifies identity contracts load without SQLAlchemy.
Updated query/route/field fakes to implement the new interfaces.

Evidence: targeted inventory/API/port/wiring tests -> 82 passed, five PostgreSQL skips;
new split identity test -> one passed; full suite -> 1,144 passed, 16 external-service
skips (`/tmp/telescreen-identity-boundary-pytest.log`). Format, Ruff, typelint (628 files),
ty, build and diff whitespace checks pass. The previous turn's PostgreSQL cluster is
stopped; no new PostgreSQL coverage is claimed for these identity changes.

Next: inventory-local durable audit publication, retention-to-notification delivery,
and separate API data sessions/store migration ownership. `retention.py` still calls
session-bound recipients and reads teams, deliberately pending its delivery ownership
change. Audit service currently allocates a shared sequence and hash chain inside the
caller's transaction; isolation must preserve atomic inventory publication and receiver
deduplication, rather than appending independently before the inventory commit. Master
metadata authority and owner-local numbering remain to audit. No blocker requires input.


### 2026-10-14, durable inventory audit publication

Added inventory-owned `inventory_audit_outbox` and audit-owned `audit_receipts` in the
Nodetto app, then ran tidy layout and regenerated both SQL exports (59 tables, 94 indexes).
Added matching ORM models and revision `0007_inventory_audit`; its downgrade refuses
pending audit publication. The migration test preserves an existing asset master ID and
audit chain hash/head through upgrade, repeated revision execution and guarded downgrade.

Moved `AuditEntry` into an ORM-free types module with a compatibility reexport. Added a
versioned session-free `AuditCommand`/`AuditReceiver` contract. The wire codec serializes
canonical public JSON details, avoiding inactive internal JSON storage slots; validates
strict integer schema versions and schema-width identifiers; and emits static failures.
Shared masking code lives in `masking/audit.py`, so inventory does not import an audit
service implementation. Payloads are masked before inventory outbox persistence and
again before chain insertion.

`InventoryAuditPublisher` stages intent in the inventory transaction and returns zero
until the audit owner allocates a chain sequence. All runtime inventory services, export
routes and retention audit hooks use it. `AuditCommandReceiver` owns the main audit
transaction. Receipt digest checking, global chain-head locking and append are atomic;
changed payloads under a received ID conflict, and lost replies deduplicate. Existing
local audit writers use the same head lock; first-writer initialization handles a race.
Occurrence timestamps are preserved while global sequence reflects insertion order.

Registered audit delivery/recovery with the existing worker. Recovery rotates pending
IDs, preserves active backoff and requeues exhausted tasks. Publication requires only
inventory tables, never a main queue write. The integration provider supports an explicit
inventory source session factory for split stores; its legacy same-store mode reads in
the main scheduling transaction. Production still has one configured factory, pending
workstream 5. No separate database configuration is claimed here.

Evidence: five SQLite split-store failure/recovery tests, ten public contract tests, an
incremental migration test, a real API/worker outage test and wiring assertions. Two
PostgreSQL cases race 24 duplicate deliveries with 12 local appends, with and without a
seeded head; each produces exactly one external receipt, a valid 13-record chain and
actual cross-schema permission denial. Full PostgreSQL-enabled suite -> 1,173 passed,
six live/Meilisearch skips (`/tmp/telescreen-audit-handoff-full-pg-final.log`). After moving
the shared pure masking helper, the affected audit/contract/runtime tests passed again
(22 passed, one PostgreSQL skip), and build passed. Format, Ruff, typelint (641 files),
ty, schema canonical/export/model checks and diff whitespace checks pass.

Workstream 3 and the overall goal remain active. Next: retention must publish durable
notification intent rather than read team tables or write notifications in its inventory
transaction. Master metadata authority/numbering, separate API data sessions, store URL
configuration, migration ownership/cutover and full restricted-credential runtime proof
remain pending. The temporary PostgreSQL cluster is stopped after verification. No
blocker requires user input.


### 2026-10-14, durable retention notification handoff

Completed workstream 4. Added `inventory_retention_outbox`,
`retention_notification_receipts` and `data_field_values.revision_id` through Nodetto,
ran tidy layout and regenerated both exports (61 tables, 94 indexes). Added matching
models and `0008_retention_handoff`. Existing values receive stable revision IDs without
resetting old notified-date markers, so upgrade does not resend already notified dates.
Downgrade refuses pending retention publication. Migration/backfill tests preserve old
values and markers through repeat upgrade and guarded downgrade/re-upgrade.

`DataOverdueHandler` now publishes masked revision-specific obligations and updates only
inventory rows. It retains the keyset scan, lifecycle/date recheck and per-asset failure
isolation. The legacy `overdue_notified_for` name now describes publication, not remote
channel delivery. Actual due-value edits rotate a UUID revision and clear the marker;
no-op edits preserve both. Changing which field owns the deletion-due role clears markers
so an equal date under a new field cannot be hidden. A deterministic asset/field/revision
UUID identifies each obligation, independently of wall-clock precision.

Moved `NotificationContext` into a pure types module with compatibility reexport. Public
retention command/lookup/receiver contracts are ORM-free. New notification consumption
uses the inventory owner's session-free eligibility/owner lookup before its transaction.
The snapshot check skips superseded, cleared, deferred, archived and deleted obligations;
it is not a distributed lock against later edits. Current registered owner metadata
remains master; only missing/stale identities consult the issue fallback. Notification
management owns recipient SQL, member preferences, team fanout, template/DLP checks,
notification and task rows, audit and deduplication. Receipt claim uses transactional
DML on both PostgreSQL and SQLite, so receipt, notification, queue and audit roll back
together. Duplicate digest conflicts fail; committed duplicates can be acknowledged even
while inventory is unavailable. An inventory write no longer waits for notification work.

Registered main-queue recovery and independent delivery with the existing worker.
Recovery preserves active retry backoff, recovers exhausted attempts and pages past earlier
pending events. The split-store tests execute real metadata and notification services
with disjoint table sets. They cover publication/consumer rollback, lost replies, A -> B
-> A at one timestamp, invalidation, stale/current owners, no recipient, preference off
and Slack, team email/Slack fanout, masking/DLP, recovery fairness, due-field replacement
and duplicate acknowledgment during inventory outage. Existing retention behavior tests
now run publication and receipt as separate commits through a small test harness.

The PostgreSQL case races twelve publishers and twenty-four receiver attempts under
separate login owners, yields one obligation and one notice, verifies the audit chain and
actual cross-schema permission denial. The assembled API/worker outage case commits two
due-date revisions while notifications are unavailable, then skips the old revision and
sends the latest one after recovery. Full PostgreSQL-enabled suite: 1,197 passed, six
live/Meilisearch skips (`/tmp/telescreen-retention-handoff-full-pg.log`). Two additional
boundary cases were then added; the delivery file passed all 15 cases. Format, Ruff,
typelint (656 files), ty, canonical schema/export/model checks, build and diff whitespace
checks pass. The temporary PostgreSQL server is stopped after verification.

Goal remains active. Workstream 3 still needs the master metadata/numbering and import
boundary audit. Workstream 5 still needs separate production session/engine URLs, API data
sessions distinct from authentication, migration ownership/cutover and complete runtime
verification under restricted credentials. Existing migration 0005 derives new tables
from live ORM metadata; the incremental test explicitly reconstructs the deployed old
column set. Freeze that historical behavior as part of the remaining migration ownership
work rather than mistaking a fresh current-metadata install for incremental compatibility.
No user input is blocking progress.

### 2026-10-14, inventory authority and separate runtime sessions

Completed workstream 3 and started workstream 5. Registered assets now reject source
observation and reconciliation mutations, including the uniqueness-conflict reload path.
Manual inventory edits remain available. Discovered assets retain custom metadata and
source lifecycle changes cannot rewind a newer master timestamp. Consumers use captured
policy rather than reading collector configuration through their inventory transaction.

Added the inventory-owned allocation counter through Nodetto and regenerated exports
(62 tables, 94 indexes). Migration 0009 preserves the greater legacy or asset high-water
mark; downgrade transfers the owned high-water mark back before removing the table.
Atomic allocation is covered by concurrent PostgreSQL first-use and rollback tests.
Explicit table ownership and import gates enforce the inventory model boundary. Shared
identity and cursor helpers no longer require concrete directory or API dependencies.

Container injection and all data routes now accept independent inventory sessions while
authentication, issues, identity, notification delivery and the worker queue use main.
The assembled SQLite runtime uses disjoint table sets and exercises registration,
ingestion, enrichment, asset reads/export, audit and retention delivery. Attempts to
query the other owner's tables fail. Production URL configuration and engine lifecycle
remain pending; factory injection does not yet provide deployment configuration.

Final PostgreSQL-enabled suite: 1,210 passed, six live-service/Meilisearch skips
(`/tmp/telescreen-master-split-runtime-full-pg-final.log`). Format, Ruff, typelint
(666 files), ty, canonical schema/export/model checks, build and diff whitespace checks
pass. The temporary PostgreSQL cluster was stopped after verification.

Overall work remains active. Next: production database URL and engine ownership,
owner-specific migrations with frozen history, tested copy/cutover/rollback preserving
pending handoffs, and the complete assembled runtime under restricted PostgreSQL
credentials. No user input is blocking progress.
