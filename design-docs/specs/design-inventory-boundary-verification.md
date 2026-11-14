# Inventory boundary completion verification

Audit date: 2026-10-14. Status: completed and verified.

This audits the original request: keep one application, loosely couple inventory from
alert/issue and notification management, permit independent databases and later process
extraction, and keep inventory authoritative for asset master data. The acceptance source
is [the boundary design](design-inventory-alert-boundaries.md); the implementation plan
and [cutover procedure](design-inventory-store-cutover.md) remain part of the scope.
Passing a read-only split-store test alone is insufficient evidence.

## Requirements and direct evidence

Paths below are relative to the repository root. Tests were read for their assertions,
and production paths were inspected for ownership and transaction placement. The full
suite includes both the targeted boundary cases and existing application regressions.

| ID | Requirement/invariant | Implementation and authoritative evidence | Result |
| --- | --- | --- | --- |
| R1 | Keep a single deployable application while allowing later transport adapters | `wiring.py`/`wiring_v14.py` compose the existing API and worker; public receiver protocols have no transaction parameter. Runtime tests use the same application with shared and split URLs. | PASS |
| R2 | Inventory exclusively owns asset identity, lifecycle, fields, values, registered ownership and deletion dates | `inventory` imports only its own SQL models and supporting value contracts. `test_import_boundaries.py` exhaustively maps all 62 tables and rejects concrete support/issue/API dependencies and other business modules' inventory-model imports. | PASS |
| R3 | Source replay cannot overwrite registered master records; discovered custom metadata remains authoritative | `observer.py` guards registered records before mutation and after an insert-conflict reload; reconciliation excludes them. `test_master_authority.py` exercises observation/removal/sweep/listing/stop, manual lifecycle changes, custom kind/field preservation and monotonic timestamps. The insert-race case simulates uniqueness-conflict reload; it is not claimed as a real parallel registration race. | PASS |
| R4 | Resource/asset/issue references survive separation without cascaded issue deletion | No physical cross-owner foreign keys exist; issue enrichment uses opaque keys/IDs, and inventory delete changes only owned state/events. Migration/transfer/cutover tests retain asset IDs, numbers, field revisions and receipts. | PASS |
| R5 | Public handoff contracts are immutable values, versioned, validated and bounded | `ports/{inventory_commands,inventory_types,audit_commands,audit_types,retention,notification_types,identity_context,asset_context}.py`; subprocess import tests load public contracts without SQLAlchemy. Wire tests cover all operation variants, strict integer versions, malformed inputs, hidden payload repr, UTF-8 framing and collection limits. | PASS |
| R6 | Issue/actor/reference enrichment uses owner-controlled committed reads; issue projections do not write master data | `issues/asset_context.py` and `directory/inventory_context.py` open their own sessions. Query code does not swallow lookup errors into zero counts. `test_database_boundary.py` and `test_identity_boundary.py` execute real supporting SQL through disjoint stores while the inventory transaction remains intact. | PASS |
| R7 | Producing business state, inventory command and wakeup commit atomically | `InventoryPublisher`/`InventoryOutbox` stage only main-owned rows in the producing session. `handoff/test_runtime.py` verifies policy/wakeup rollback; `test_delivery.py` verifies producer rollback; `test_publication_limits.py` proves an oversized complete listing rolls back business state, sequence allocation, command and queue. | PASS |
| R8 | Every producer uses one allocator and captures policy under a consistent lock order | Issue record, map-reduce, complete collector listing and config stop all bind to `InventoryPublisher`. `wiring_v14` tests verify actual service bindings. PostgreSQL tests race direct/publisher first use and observe a real collector lock wait followed by refreshed OFF policy. | PASS |
| R9 | Consumer application, receipt and cursor advance are atomic; duplicates/conflicts/gaps are safe | `inventory/handoff/inbox.py` owns its transaction and stream lock; digest identity is checked before apply. Delivery and PostgreSQL tests cover consumer rollback, concurrent receivers, lost reply, conflicting ID/position and missing predecessor. | PASS |
| R10 | Sweep/listing absence cannot overtake observations or resurrect newer lifecycle state | All four commands share the collector stream. `test_sweep_cannot_overtake_a_missing_observation` proves the barrier; listing/stop/stale tests and observer/reconciler suites exercise occurrence watermarks and captured OFF mode. | PASS |
| R11 | Failed delivery stays durable, fair and retryable, including exhausted attempts | Source/audit/retention recovery providers rotate stream/ID heads, preserve active backoff and enqueue new recovery work. Runtime/recovery tests exercise blocked streams, exhausted attempts, paging and lost acknowledgments. No normal delivery path deletes outbox or inbox rows. | PASS |
| R12 | Audit publication is local to inventory, masked and independent of audit availability | `inventory/audit.py` stages masked intent locally; audit receiver owns receipt and global chain append. Audit tests cover producer/receiver rollback, lost reply, payload conflicts/masking, mixed PostgreSQL local/external writers and initially absent head. The assembled outage test commits the field before the chain catches up. | PASS |
| R13 | Due-date changes create stable revision-specific obligations without conflating publication and delivery | `ValueService` rotates revisions only on actual canonical change; role reassignment clears publication markers. Retention tests cover same-clock A -> B -> A, no-op/clear/re-add, role replacement, repeated publication and old-marker migration backfill. | PASS |
| R14 | Notification management owns recipients, preferences, DLP, delivery state, queue and deduplication | `notifications/retention.py` owns the receipt/notification/queue/audit transaction; inventory has no team/notification SQL access. Disjoint-store tests cover current/stale owners, issue fallback, no recipient, preference off, member/team email/Slack, masking/DLP and receiver rollback. | PASS |
| R15 | Superseded/cleared/deferred/archived/deleted obligations do not notify; committed duplicates can acknowledge during inventory outage | `InventoryRetentionLookup` checks asset/source/key, active field role, current revision/date and lifecycle. Tests cover each invalidation and reuse of an existing receipt with an unavailable inventory lookup. Eligibility remains a pre-commit snapshot, not a distributed lock. | PASS |
| R16 | Inventory and issue handling remain available when remote delivery fails | Assembled inventory, audit and retention outage tests exercise real API/worker retry. Issue ingestion commits during inventory receiver failure; metadata edits commit during notification/audit failure; recovery produces only the correct effects. | PASS |
| R17 | Separate production database configuration, sessions and engine lifecycle retain safe shared defaults | `core/config.py`, `wiring_builders.py`, all four data router dependency sets and `wiring_v14.py` route inventory SQL independently from authentication/main services. Configuration tests cover unset/empty/equal URLs, separate pools, injection precedence and cleanup ownership. | PASS |
| R18 | Full API/worker works with credentials that cannot cross owner tables, including retained rollback tables | `test_inventory_split_runtime.py` runs complete owner schemas migrated under restricted logins. `test_inventory_runtime_grants.py` grants non-owner runtime logins only local DML, denies main access to retained local inventory tables and the target schema, denies inverse schema access, denies version tables and trigger DDL, and then runs the same complete API/worker flow. | PASS |
| R19 | Asset numbers are inventory-local, concurrent and rollback-safe | Owned counter UPSERT allocates transactionally. SQLite rollback and PostgreSQL 24-way first-allocation tests pass. Migration 0009 merges legacy/asset high-water marks and returns the newer mark on downgrade; transfer/cutover continues numbering after rollback. | PASS |
| R20 | Nodetto remains DDL source of truth; ownership is exhaustive | Canonical document, both generated exports, model/index/reference checks and 62-table coverage pass. New stream references are logical field relations; transport event identities are opaque IDs rather than cross-database foreign keys. | PASS |
| R21 | Legacy and owner migrations are immutable and genuinely incremental | Frozen legacy revisions reproduce targets 0001-0008 before upgrading to head; explicit backfills retain data/markers. Independent owner baselines create only their tables/seeds/triggers and version state. Tests cover repeat upgrade, downgrade, SQLite DDL rollback, future ORM drift and packaged resources. | PASS |
| R22 | Existing schema adoption is explicit, checked and atomic | Adoption requires stopped writers and a known baseline, validates types/defaults/keys/indexes/constraints, initialized allocation, audit head and PostgreSQL trigger policy. Tests reject schema/state drift, partial indexes, unrelated serial sequence defaults and late stamp failure. | PASS |
| R23 | Existing-data split and rollback preserve all master and delivery state | Transfer hashes every column/row of ten tables, leaves source/main supporting state unchanged, and commits only a verified target. Tests cover empty/identical/different targets, mid-copy failure/corruption, missing/conflicting lineage, counter/cursor/ack rewind, multiple pages, trigger restoration and cross-dialect UTC manifests. | PASS |
| R24 | Cutover procedure works after new inventory writes and pending work | `test_inventory_store_cutover.py` runs shared -> split -> shared on SQLite and PostgreSQL with lost producer acknowledgment, copied inbox receipt, pending observation/audit/retention, new assets and metadata revision, continued allocation, valid audit chain and one notification. The runbook documents every command, switch, credential scope and stopped-writer requirement. | PASS |
| R25 | Existing public API, permissions, masking and UI behavior remain compatible | API shape manifest tests, route permission suites, end-to-end/leak matrix and existing inventory/issue/notification tests run in backend CI. Web CI typechecks, runs all 262 existing tests and builds without UI-source changes. | PASS |
| R26 | Required format/lint/strict typing/tests/schema/build checks pass on final sources | Final backend CI, schema checks and web CI pass; the extracted wheel executes migrations, adoption, transfer and wire limits outside the checkout. All touched Python files are below 1000 lines. Full command evidence follows. | PASS |

## Plan and artifact coverage

Workstream 1 is covered by R6; workstream 2 by R7-R11; workstream 3 by R2-R5, R12 and
R19; workstream 4 by R13-R16; and workstream 5 by R17-R26. These preserve all six design
acceptance bullets rather than substituting a single split-store smoke test.

The implementation deliverables are present in `ports`, `issues/asset_context.py`,
`directory/inventory_context.py`, `collectors/inventory_publish.py`, `integrations`,
`inventory/handoff`, inventory business services, notification/audit receivers, production
configuration/composition and data routers. Schema artifacts are the Nodetto document,
both SQL exports, matching models, shared revisions 0006-0009, frozen legacy resources
and the `main_0001`/`inventory_0001` owner histories. `db/{adopt,schema_check,transfer,
transfer_check,transfer_rows}.py` and `cli_inventory_store.py` implement the operating
commands. The boundary design, architecture/command indexes, runbook and implementation
plan link the design to these artifacts and their validation.

Command coverage is explicit: shared/owner `migrate`, single-owner `--store`, checked
`--adopt --writers-stopped`, split `inventory-store copy/verify`, authoritative merge
copy/verify, and API/worker startup with shared or independent stores. CLI tests call the
real parser and command entry point. Migration/transfer tests invoke the programmatic
entry points, while full cutover tests restart the actual composition at every switch.

## Supported operating semantics

- Asset source projection, inventory audit insertion and retention delivery are
  asynchronous. Run the existing worker. Successful ingestion does not promise that
  the derived asset is already visible; inventory mutation does not promise channel
  delivery or immediate audit-chain visibility.
- Cross-owner reads see committed owner state with no global snapshot. Retention
  eligibility is checked before notification commit; later metadata edits do not retract
  a notification already accepted. Delivery failure cannot undo committed inventory.
- Handoff payloads are limited to 16 MiB of UTF-8. Inventory listing keys, item
  fingerprints and value fingerprints each have a 100,000-entry maximum, with the
  payload byte limit also applying. Excess snapshots fail atomically, never truncate.
  A future larger-list protocol must preserve the same complete-listing barrier.
- Outbox/inbox/receipt rows currently have no automatic expiry or pruning. Keep them
  through the supported replay window; manual deletion is not a supported replay path.
  Any future expiry needs an explicit reconciliation snapshot/checkpoint protocol.
- Copy/adoption flags do not stop processes. Operators stop all writers, use migration
  credentials for DDL/transfer, verify while quiesced, and restart with scoped runtime
  credentials. The tool retains old tables for controlled rollback, not a second writer.
- The result is a modular monolith with value-only adapter seams, independently owned
  persistence and durable delivery. A separate network service, broker or deployment
  has not been introduced or claimed. No production data was moved during verification.

## Final command evidence

| Command/check | Final result | Local evidence |
| --- | --- | --- |
| `TELESCREEN_TEST_DATABASE_URL=<temporary PostgreSQL URL> PYTEST_ADDOPTS=-q mise run ci` | Exit 0: format, Ruff, typelint (693 files), ty, 1,288 passed / 6 skipped, sdist and wheel | `/tmp/telescreen-boundary-final-ci-current.log` |
| `mise run ddl-check` | Exit 0: canonical Nodetto document, both exports current, 10 schema tests passed | `/tmp/telescreen-boundary-final-ddl.log` |
| `mise run web-ci` | Exit 0: frozen install, typecheck, 49 files / 262 tests passed, production build | `/tmp/telescreen-boundary-web-ci.log` |
| Extracted final wheel, outside checkout | Exit 0: wire-size enforcement, legacy/owner migrations, adoption, nonempty copy/verify/merge and shared-owner migration routing | Executed using the built wheel with its packaged SQL/JSON resources; imported paths asserted to be under the temporary installation |
| `git diff --check`; touched-Python size scan | Exit 0; no touched Python file has 1000 or more lines | Final working tree inspection |

The six skips are five opt-in live service tests and Meilisearch; PostgreSQL integration
tests ran. An earlier unquiet CI attempt had one throughput-guard failure: the existing
10,000-shard case took 250.5 seconds against its unchanged 240-second assertion. It then
passed alone in 72.5 seconds and in the complete final CI rerun above. No assertion,
test selection or time limit was weakened. The earlier attempt is retained at
`/tmp/telescreen-boundary-final-ci.log`; the isolated pass is at
`/tmp/telescreen-mapreduce-throughput-check.log`.

All 26 requirements and all five workstreams have direct evidence. No implementation or
verification item remains open. The temporary PostgreSQL cluster was stopped after the
checks. These logs are local execution evidence; the test sources and commands above
allow the checks to be repeated. Source changes remain in the working tree; committing
or deploying them was not part of the requested task.
