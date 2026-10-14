# Inventory and alert management boundaries

Status: implemented and verified, 2026-10-14. This design extends the v1.4 inventory
baseline. The [completion audit](design-inventory-boundary-verification.md) records
requirement-level evidence, final checks and supported operating constraints.

## Overview

Keep one deployable application while making inventory and alert/issue management
independently persistent modules. Inventory is authoritative for the asset register,
resource identity, lifecycle, custom field definitions and values, asset owner metadata,
and deletion dates. Issue management owns findings, issue status, remediation,
ownership rules, and alert evaluation. Notifications own delivery policy and delivery
state. Identity, collector configuration, audit and workflow are explicit supporting
modules, not tables that every module may access through an arbitrary session.

An issue's remediation assignee and an asset's registered owner are distinct concepts.
Issue ownership may supply a fallback for existing overdue behavior; it must never
silently overwrite the inventory master. Inventory deletion must not cascade into issue
history. References use opaque stable resource keys and IDs, not cross-database foreign
keys. Existing identifiers must survive extraction.

## Current evidence

- `ports/inventory.py` compatibility hooks take the producer session. Runtime wiring
  now binds all four hooks to `InventoryPublisher`, which writes only producer outbox
  and queue rows. Inventory application runs in the receiving transaction.
- `inventory/queries.py` previously passed inventory sessions to `ResourceIssueReader`.
  It now calls `AssetIssueLookup`, whose issue-side adapter owns the read session.
- Inventory queries now use session-free `ActorLookup`; metadata reference validation
  uses session-free `AssigneeLookup`. Identity-side adapters open and close their own
  sessions. Collector actor names resolve inside the identity-side read session.
- `inventory/retention.py` publishes revision-specific masked obligations to its own
  outbox. The notification-side receiver owns recipient/fallback resolution, delivery
  policy, audit, deduplication and queue writes; no inventory session crosses that boundary.
- Runtime composition supports `TELESCREEN_INVENTORY_DATABASE_URL` and an optional injected
  inventory factory. Data API sessions are separate from authentication, and inventory
  workers/outbox readers use that factory. Empty stores have independent frozen migrations;
  existing shared deployments can explicitly adopt validated owner schemas, transfer
  inventory, verify manifests and roll back after new writes.
- Inventory tables contain logical references rather than physical foreign keys.
  `InventoryRepository` allocates numbers atomically from inventory-owned `inventory_counters`.
  `db/ownership.py` declares ten inventory tables and fifty-two main tables.

## Contracts and consistency

Cross-module public contracts contain immutable dataclasses, enums, scalar values and
bounded collections. They must not accept SQLAlchemy sessions, ORM rows, SQL expressions,
or transaction callbacks. SQL and private transaction-bearing ports remain internal.
In-process adapters implement these contracts now; transport adapters can implement the
same semantics later. Network deployment and a broker are not prerequisites.

Durable handoff codecs enforce a 16 MiB UTF-8 payload limit before decoding or persistence.
Each inventory listing-key, item-fingerprint and value-fingerprint collection is limited
to 100,000 entries, with the byte limit applying as well. The entire snapshot is rejected
on overflow; it is never truncated into a false complete-listing absence signal. Size
failure rolls back producing business state, stream allocation, outbox and wakeup together.
Issue/actor lookup requests are batched with inventory pages; these internal read adapters
do not share a transaction or promise a cross-store snapshot.

Inventory issue enrichment and fallback ownership read committed issue state in an
independent session. They do not see pending caller writes or provide a cross-database
snapshot. Errors propagate rather than appearing as a misleading zero-issue result.
Asset reads must never update master metadata from issue projections.

For writes, replace shared transactions with a durable outbox at the producing module
and an inbox at the consuming module. A producer commits its business state and event
atomically in its own database. A consumer applies its business changes and inbox receipt
atomically in its database. Delivery is at least once; unique event IDs make retries
idempotent. Do not substitute an in-memory callback or best-effort second commit.

Observation events carry safe resource metadata, fingerprint values, observation time,
source identity, scan identity, completion/removal flags, and an event/schema version.
Preserve the existing masking boundary; raw content and item keys must not enter events.
Ordering must be per resource and source scan generation. An older observation must not
resurrect a newer deletion. Sweep completion is a durable barrier: reconcile absence only
after every observation from that sweep has been applied. Listing and stop-tracking
commands need the same retry and ordering guarantees. Inbox/outbox retention must exceed
the supported replay window; replay after expiry requires a new reconciliation snapshot.
There is currently no automatic inbox/outbox/receipt expiry or pruning. Operators retain
these records; manual deletion is not supported replay. A future expiry policy must add
an explicit snapshot/checkpoint protocol rather than treating missing receipts as safe.

Retention should publish a due-date event in the inventory transaction. Notification
management consumes it, resolves recipients through identity contracts, and owns delivery
retry and deduplication. The event identity must include asset and due-date revision;
a due-date edit creates a new obligation, while replay cannot duplicate the old one.
Notification failure must not roll back inventory changes. Preserve existing DLP,
permissions, preferences and audit behavior while moving these responsibilities.

## Write handoff implementation contract

`ports/inventory_commands.py` defines schema version 1 with four discriminated
operations: `ObserveResource`, `CompleteSweep`, `CompleteListing`, and `StopTracking`.
The envelope contains an immutable event ID, stream ID, positive stream sequence and
occurrence time. Payloads contain no sessions or ORM rows; `ports/inventory_types.py`
owns the observation values. Legacy ports re-export those values for compatibility.
JSON decoding rejects unknown versions, unknown operations and invalid field types.
The producer-local outbox, inventory-local inbox, operation applier and detached
dispatcher now implement this contract. Runtime producers and the existing worker now use them.

All four runtime producer hooks now publish these operations:

| Producer | Producer-local hook | Published operation |
| --- | --- | --- |
| `issues/service.py:IssueService.record` | `inventory.observe` | `ObserveResource` in the issue transaction |
| `workflow/mapreduce/reduce.py` | `inventory.reconcile_sweep` | `CompleteSweep` only for a successful map job |
| `collectors/tasks.py` | `inventory.reconcile_listing` | `CompleteListing` only for complete, unskipped listing |
| `collectors/service.py` | `inventory.stop_tracking` | `StopTracking` atomically with the config change |

Allocate sequences transactionally per collector stream (or source stream for
observations without a collector). Every one of these producer paths must use the same
allocator for a given stream. Capture inventory mode in observation/reconciliation
commands rather than querying mutable collector configuration during replay.

The implemented consumer locks its inventory-owned stream cursor. It applies only the next
sequence, records the inbox receipt and cursor advance with the inventory changes, and
acknowledges only after that commit. Missing predecessors wait; they are not silently
skipped. Duplicate event IDs must match their original stream/sequence. Outbox delivery
failure leaves committed producer work intact and retryable. Split-store SQLite and PostgreSQL tests cover these transaction, duplicate, failure
and gap invariants, including runtime producer integration and concurrent delivery.

Independent read sessions require independent database connections. The v1.4 end-to-end
and leak tests explicitly request file-backed SQLite rather than the legacy in-memory
`StaticPool` fixture, which aliases all sessions onto one transaction. The latter cannot
model this boundary. File-backed SQLite also has one-writer limitations, so it does not
replace PostgreSQL concurrency verification.

## Durable storage and delivery implementation

`0006_inventory_handoff` adds four tables designed in Nodetto, with logical
field references to their local stream cursors:

| Owner | Tables | Constraint |
| --- | --- | --- |
| Producer | `inventory_outbox_streams`, `inventory_outbox` | Unique `(stream_id, sequence)`; command ID primary key |
| Inventory | `inventory_inbox_streams`, `inventory_inbox` | Stream-position primary key; globally unique command ID |

`integrations/inventory_outbox.py` allocates the next sequence and inserts its command
inside the producer's existing transaction. `inventory/handoff/inbox.py` applies each
command, records its payload digest and advances its cursor in one inventory transaction.
Changed content under an existing ID or reuse of an applied stream position is rejected.
`inventory/handoff/apply.py` uses captured policy and occurrence times; it never reads
collector tables during command application. Observation and reconciliation services
require captured policy and have no collector repository dependency.

`integrations/inventory_dispatch.py` reads detached stream heads, closes its producer
read transaction, calls the receiver, and acknowledges in a new producer transaction.
A failure after the inventory commit leaves the original event pending; retry returns a
duplicate receipt without repeating inventory history. Failure or a sequence gap blocks
only that stream in the selected batch. A large backlog from one unavailable stream
cannot occupy every batch slot. The worker path now uses `InventoryDeliveryHandler` for event-specific delivery,
including retry/backoff and sequence-gap retry. `InventoryRecoveryProvider` rotates
through stream heads and requeues abandoned/exhausted deliveries once per event and
minute, without accelerating an active retry. Failed delivery is visible in engine-task
state and safe logs. No outbox command is deleted merely because its task exhausted retries.

Inventory ignores source observations older than its last-seen or lifecycle watermark.
This prevents an older scan from restoring a newer deletion or stop-tracking state.
Listing replay also skips assets seen after the listing occurrence time.

`collectors/inventory_publish.py` locks and refreshes the collector configuration
before capturing its mode and allocating the stream sequence. Configuration updates
use the same collector-then-stream order. PostgreSQL testing observes a real lock wait
and verifies that a cached `all` policy is refreshed to `off` after a concurrent
stop-tracking commit; it cannot appear later in the stream as a new `all` observation.

The split-store tests install only producer tables on one side and inventory tables
(including a local asset-number counter) on the other. They exercise real inventory
business changes, not a mock inventory writer. The migration test removes the new tables,
recreates them through the incremental revision, repeats that revision, and confirms that
existing asset master data survives.

## Runtime consistency and verification

Issue ingestion, Police results, completed sweeps, complete listings and tracking changes
now commit the local outbox and an `inventory_delivery` task with their producer work.
Successful ingestion does not promise that the asset projection is already visible.
Inventory API schemas are unchanged; consumers wait for delivery before requiring new
source observations in inventory. Manual inventory changes still use their own local
transaction. Keep the existing worker running; see [commands](command.md#inventory-delivery).

`wiring.py` binds issue ingestion and all collector/reducer reconciliation hooks to the
publisher. `wiring_v14.py` registers the receiving inbox, delivery handler and recovery
provider. The default shares one session factory; a distinct configured inventory URL
creates a separate owned engine. Explicit injected inventory sessions take precedence.

Five PostgreSQL tests use separate producer/inventory schemas owned by different login
roles, verify permission denial for cross-module SQL, and exercise concurrent first-stream
allocation, policy-locked publication, duplicate receipt races and consumer rollback.
An assembled API/worker test proves that issue ingestion succeeds while the inventory
receiver is unavailable and projects the asset after retry. Current complete-check
results and requirement traceability are in the
[completion audit](design-inventory-boundary-verification.md).

Actor display and metadata reference validation now use independent identity-owned read
sessions. A separate-database test executes real member/team SQL while an uncommitted
inventory field write remains intact and then commits; stale references retain their
existing safe validation errors. Public identity contracts also import without SQLAlchemy.

The complete API/worker passes against disjoint SQLite stores and full migrated PostgreSQL
stores with separate restricted login owners. Existing shared deployments still require
explicit adoption and copy/cutover: the legacy migration graph touches both
owners. New owner baselines refuse implicit adoption of existing tables.

## Inventory audit delivery

Inventory service mutations and export audit events now use `InventoryAuditPublisher`.
It masks summary/detail text and commits an immutable versioned `AuditCommand` in
inventory-owned `inventory_audit_outbox` alongside the business operation. Its local
`AuditLog.append` returns zero because the global chain sequence belongs to the audit
owner and is allocated on delivery. The public receiving contract, `AuditReceiver`,
contains no session or ORM types. JSON details use their canonical public JSON form on
the wire, so inactive internal `JsonValue` slots are never serialized.

`InventoryAuditRecoveryProvider` polls committed pending IDs and enqueues
`inventory_audit_delivery` work in the main queue. A rotating ID cursor prevents a
failed entry from permanently hiding later pending entries. It preserves active retry
backoff and creates new minute-scoped recovery tasks after exhausted attempts. In a
shared-store deployment, polling reuses the scheduling transaction for a local read;
with split stores, its explicit source session factory closes the inventory read before
enqueueing main-owned work. Inventory publication requires no workflow tables.

`InventoryAuditDeliveryHandler` closes the inventory read session, calls the audit
receiver, and acknowledges in a new inventory transaction only after the receiver commits.
`AuditCommandReceiver` serializes receipt lookup and chain append under the same global
chain-head lock as local audit writers. Audit-owned `audit_receipts` stores the external
event ID and canonical payload digest in that transaction; a changed payload under an
existing ID is a conflict. Its ID identifies an external command rather than referencing
an audit record ID. Receipt and hash-chain append either both commit or both roll back.
Lost replies retry as duplicates. First-writer head initialization handles concurrent
inserts. The existing PostgreSQL append-only trigger remains intact.

The chain sequence describes audit insertion order, while the record timestamp preserves
source occurrence time. Delivery is asynchronous and may interleave producers; source
time is not a global ordering guarantee. An audit outage does not roll back a committed
inventory change or discard its audit intent. As with source inventory projection, keep
the existing worker running. Revision `0007_inventory_audit` adds the two tables without
rewriting existing master IDs or chain records; downgrade refuses a pending audit outbox.
Writers must be stopped before downgrading the handoff protocol.

Separate-file tests exercise real field mutations, producer/consumer rollback, lost replies,
payload masking, receipt conflicts, chain verification and exhausted-task recovery. Two
PostgreSQL tests mix 24 duplicate deliveries with 12 local appends, with and without a
seeded chain head, and verify actual cross-schema privilege denial. An assembled API/worker
test confirms a field definition commits during an audit outage and the chain catches up
after retry. Full composition and the retention boundary are verified separately below.

## Retention notification delivery

`DataOverdueHandler` now reads and writes only inventory tables. It locks each selected
asset, rechecks active due-field/value and lifecycle state, masks notification metadata,
and commits a versioned `RetentionCommand` in `inventory_retention_outbox` with the local
publication marker. It keeps the keyset batch cursor and per-asset failure isolation.
The legacy `overdue_notified_for` column now means the date whose obligation was published;
it is not authoritative notification delivery state.

Each metadata value has a UUID `revision_id`, rotated by `ValueService` only when its
canonical value changes. Actual due-value edits clear the publication marker; no-op edits
retain it. Changing which field carries the deletion-due role clears markers as well.
An obligation ID is a deterministic UUID derived from asset ID, due-field ID and value
revision. Thus A -> B -> A at one clock timestamp produces distinct obligations, while
retrying one publication retains its original ID and payload. Clearing and re-adding a
value creates a new revision. Revision `0008_retention_handoff` backfills stable revisions
for existing values without resetting old notified-date markers, avoiding upgrade resends.
Stop application writers while applying the migration; old writers do not supply revisions.
Downgrade requires stopped writers and refuses a pending retention outbox.

`RetentionRecoveryProvider` reads committed inventory obligations and enqueues main-owned
`retention_delivery` work. As with audit handoff, split stores use an explicit inventory
read factory, while legacy same-store polling can use the local scheduling transaction.
It rotates pending IDs, preserves active backoff and recovers exhausted tasks. Delivery
closes the inventory read before calling a session-free `RetentionReceiver`, then records
an inventory acknowledgment only after a committed receipt.

`RetentionNotificationReceiver` first recognizes committed receipts, so an already applied
obligation can be acknowledged while inventory is unavailable. New obligations use a
session-free `RetentionLookup`. A single inventory-owned eligibility query checks the
asset/source identity, current value revision/date, active role and nondeleted asset;
the lookup also supplies the master's current registered owner. Cleared, deferred,
archived, deleted or superseded obligations receive a durable skipped receipt and audit,
without a notification. This is a snapshot check before notification commitment, not a
distributed lock: edits after that check do not retract an already accepted notification.
The command carries masked display context captured at publication.

The receiver resolves current member/team identities in the main transaction, consults
the session-free issue fallback only for stale/missing owners, and uses the existing
member preference, team email/Slack fanout, template and DLP services. It never overwrites
the inventory master owner. A transactional `INSERT ... ON CONFLICT DO NOTHING` claims
`retention_notification_receipts` by external event ID, followed by digest verification
on duplicates. Receipt, notification outcomes, delivery tasks and audit either all commit
or all roll back. Its ID identifies an external command, not a foreign key to a
notification row. Outbound channel delivery and its retry state remain notification-owned.

Tests exercise real notification services with disjoint inventory/main tables: rollback,
lost acknowledgments, revision edits/replay, stale and current owner policy, no-recipient
outcomes, preference off/Slack, team fanout, masking/DLP, recovery and paginated fairness.
A PostgreSQL case races twelve scans and twenty-four receiver attempts under different
login owners, confirms one obligation/notice, verifies the audit chain and denies actual
cross-schema reads. An assembled API/worker outage test edits the due date while the
receiver is unavailable and sends only the latest obligation after recovery. The complete
runtime also exercises notification delivery with configured PostgreSQL owner URLs.

## Inventory master authority and allocation

Registered assets are inventory-managed records. Source observation application ignores
a registered asset even when a producer supplies its opaque resource key, including after
a concurrent unique-key insert reloads a registered winner. Collector sweep/listing and
stop-tracking also exclude registered assets. Inventory's manual delete, restore, count
and metadata operations remain available; a source sighting cannot reverse them.
For discovered assets, source observations own derived name/count/lifecycle information,
while custom kind and field values remain authoritative inventory metadata. Source hints
do not overwrite an existing custom kind, and older source/lifecycle timestamps cannot
move a newer master edit timestamp backwards. Registration's existing resource-key
namespace, public IDs and numbers remain unchanged.

Observation/reconciliation services no longer receive collector repositories. Applying an
observation, sweep or listing requires the policy captured in its durable command. The
producer owns policy lookup/locking; replay cannot reach collector configuration tables.
Collector actor-name resolution lives in the identity adapter. Shared actor classification
and cursor encoding are pure helpers, so inventory no longer imports concrete identity
services or HTTP pagination code.

`inventory_counters` exclusively owns asset allocation. A single transactional UPSERT
allocates numbers, including concurrent first use of an empty store. Both SQLite and
PostgreSQL roll allocation back with the caller transaction. Revision `0009_inventory_counter`
seeds the larger of the legacy allocated high-water mark and existing asset numbers,
without renumbering or lowering an already advanced inventory counter. Downgrade returns
the new high-water mark to legacy storage before removing the inventory counter. Stop
all old/new writers during this allocator cutover and rollback.

`db/ownership.py` declares every table's owner explicitly. The ten inventory tables are
its five asset/field tables, counter, two inbox tables and audit/retention outboxes. Main
owns the other fifty-two tables, including the source inventory outbox, queue, issue,
notification, audit and identity tables. Static gates require exhaustive disjoint ownership,
reject supporting SQL models/concrete services in inventory, and reject inventory model
imports from other business modules. Data routers are permitted to query inventory-owned
models through their explicitly injected data factory; inventory services remain writers.

Runtime composition (`build_container`/`WiringV14Parts`) now accepts a separate inventory
session factory. All data routers retain main authentication and use the inventory factory
for data. Inbox application, audit/retention outbox reads and acknowledgment, scheduled
retention scans and eligibility reads use inventory; producer queues, identity, issue
lookups and receiving audit/notification commits use main. Shared-store defaults remain
compatible. One assembled test installs only each owner's tables in separate SQLite
files, exercises registration/reference validation, source ingestion, actor/issue enrichment,
counts/detail/events/items/series/export APIs, number isolation, worker projection, audit
and overdue notification delivery, and confirms cross-store model reads fail.

The same assembled flow now runs through configured SQLite URLs and complete PostgreSQL
stores migrated by restricted owner roles. This covers production settings and migrations
for empty stores. The cutover tests also run shared -> split -> shared against SQLite
and PostgreSQL with pending work and new master writes; see the
[operating procedure](design-inventory-store-cutover.md).

## Owner connection and migration histories

`TELESCREEN_DATABASE_URL` remains main. An unset, empty or identical
`TELESCREEN_INVENTORY_DATABASE_URL` shares its factory; a distinct URL creates an inventory
engine that the container closes alongside main. Explicitly injected inventory sessions
override configuration and remain caller-owned. Data routes and all inventory handlers
use the selected factory; authentication and support adapters use main.

`migrate` retains the legacy graph for an unadopted shared default. Once owner histories
have been adopted, it continues those histories even with a shared URL. An existing
unadopted owner still requires explicit adoption. With distinct configured URLs,
it upgrades main and inventory independently, each using only its configured credentials.
`migrate --store main|inventory` selects one owner history. The owner version tables are
`alembic_version_main` and `alembic_version_inventory`; neither history needs access to
the other's tables or version state. Both histories can also coexist in a physical database.
There is no distributed transaction between the two migrations; completed owner upgrades
are idempotent when the other needs retry.

`db/owned_migrations` packages immutable dialect-specific SQL snapshots at shared revision
`0009_inventory_counter`. They were compiled from the models verified against the Nodetto
source, preserve constraints/indexes/defaults and do not import live models or ownership
lists. `main_0001` seeds issue allocation and audit genesis; `inventory_0001` seeds asset
allocation. Each installs its own PostgreSQL append-only trigger. Future schema changes
need new owner revisions, rather than regenerating these snapshots. SQLite DDL, seeds and
version stamps explicitly share a transaction so a failed baseline can be retried cleanly.

Baselines require an empty owner store. Existing legacy stores can use
`migrate --store main|inventory --adopt --writers-stopped` after upgrade through
`0009_inventory_counter`. Adoption validates the existing owner's tables against the
packaged baseline schema, including column types/defaults, keys, indexes and constraints.
It requires initialized allocation state at or above existing numbers and a main audit
head matching the last record. PostgreSQL validation also checks the enabled append-only
trigger and its exception function. The tool does not repair mismatches or reseed data.

Validation and stamping share a transaction. PostgreSQL locks owner and migration-state
tables before validation; SQLite uses `BEGIN IMMEDIATE`, including version-table DDL in
rollback. Stop every API, worker, collector and other writer before adoption and keep
them stopped through cutover. The flag records this operator precondition; database locks
only protect the adoption transaction. Repeat adoption is idempotent at the baseline head.

The legacy graph is now frozen too: revisions 0001 through 0005 load the immutable
`migrations/legacy_schema.json`, projected by the introduction versions from the original
incremental revisions. Old targets omit later additions; revisions 0006 through 0009
retain their explicit definitions and real backfills. Neither graph imports runtime ORM
metadata. Snapshot resources preserve explicit autoincrement policies, including the
application-assigned audit sequence. Tests upgrade genuine old targets to head, exercise
backfills and prove that future ORM edits do not rewrite either history or adoption.

Schema adoption alone does not move records. `inventory-store copy|verify` provides
stopped-writer transfer and manifest comparison for all ten inventory tables. Initial
copy accepts an empty target or an identical completed retry. The source stays unchanged;
target replacement commits only after a full content comparison. Keyset pages bound
memory, binary-text primary-key order removes locale differences, and UTC normalization
preserves SQLite/PostgreSQL timestamp values. PostgreSQL trigger policy and replacement
DML roll back together on failure.

`--direction merge` restores authoritative inventory to main after new writes. It preserves
mutable metadata changes and removals, requires all old asset identities/events/receipts
and outbox payloads in the source, and refuses counter/cursor/acknowledgment rewind.
The [cutover procedure](design-inventory-store-cutover.md) specifies backups, stopped
writers, scoped credentials, schema adoption, verification and configuration changes.
No production data has been moved during implementation; verification used temporary stores.

## Persistence and extraction

Inventory exclusively owns `data_assets`, `data_asset_events`, `data_asset_items`,
`data_fields`, `data_field_values` and its numbering allocation. Issue/alert and
notification tables remain owned by their respective modules. Shared audit/workflow
and identity dependencies need session-free adapters or owner-local projections.
No SQL join may cross these ownership boundaries.

Module-specific engine/session factories default to the existing URL for compatibility.
Independent owner migrations and credentials support new split deployments. Existing
deployments still require explicit data adoption/cutover. All schema changes originate
in `db/schema/telescreen.nodetto.jsonl`, are
exported through Nodetto, and include matching models and Alembic revisions.

Migration order: introduce contracts; implement durable handoff; move retention delivery;
isolate supporting dependencies; separate migration/session ownership; backfill and
reconcile; verify split databases; optionally extract a process later. Backfill records
counts, keys and event watermarks. Cutover requires draining or replaying the outbox and
comparing inventory state. Keep one authoritative writer during cutover and rollback;
do not dual-write master tables from both modules.

## Acceptance evidence

- Session-free read contracts work against two databases with disjoint table sets.
- Observation, deletion, sweep, listing, and stop-tracking survive producer/consumer
  crashes, duplicate delivery, out-of-order events and replay without lost updates.
- Inventory remains the sole writer of asset master metadata.
- Retention changes and issue handling remain available during notification failure;
  deliveries honor existing deduplication, DLP and recipient policy.
- Runtime, API, workers and migrations operate with separate inventory and issue stores,
  and with the legacy single URL. Test credentials must lack cross-module table access.
- Import/schema ownership checks prevent accidental direct dependencies. Full project
  checks and relevant end-to-end tests pass.

The first criterion alone is not proof of full database separability.
