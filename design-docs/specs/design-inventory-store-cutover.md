# Inventory store cutover and rollback

Operating procedure for moving the inventory master out of the shared database while
keeping one deployed application. See [boundaries](design-inventory-alert-boundaries.md)
and [CLI settings](command.md). These commands use configured URLs and do not provision
databases, copy identity/issue/notification tables, change runtime credentials or restart
processes.

## Preconditions

Use this application version for every API, worker, collector and other writer. Stop all
of them before legacy backfill, adoption, copy, verification and either configuration
switch. Take restorable backups of both stores before changing data. Keep writers stopped
until verification and the configuration change are complete. The `--writers-stopped`
flag acknowledges this operator precondition; locks protect only each command's lifetime.
Do not run opposing transfer commands concurrently.

The transfer supports the frozen inventory baseline `inventory_0001`, equivalent to
shared revision `0009_inventory_counter`, on PostgreSQL or file-backed SQLite. A future
schema revision requires a matching transfer implementation. Both stores must exist and
have the expected inventory schema, initialized allocation state and append-only policy.
The tool refuses implicit schema adoption or repair.

Use migration/transfer credentials with access to the existing inventory tables and
migration state in each store. PostgreSQL target credentials must own the event table
to change its user trigger within the copy transaction. Separate databases or separately
owned schemas are supported; each connection's default schema must select its intended
store. Use appropriately scoped runtime credentials when restarting the application.

## Existing shared deployment to separate inventory

1. Keep `TELESCREEN_DATABASE_URL` pointing to the shared store and leave
   `TELESCREEN_INVENTORY_DATABASE_URL` unset. With all writers stopped, run
   `telescreen migrate`. This upgrades the legacy graph through 0009, assigns revisions
   to existing metadata values, and transfers the asset allocation high-water mark to
   inventory-owned storage. Existing IDs and numbers remain unchanged.
2. Run `telescreen migrate --store main --adopt --writers-stopped`. Adoption validates
   main schema, allocation and audit-head state and stamps main's independent history.
   It does not move or recreate business rows.
3. Set `TELESCREEN_INVENTORY_DATABASE_URL` to the new empty inventory store. Run
   `telescreen migrate --store inventory` to create its owner baseline. Do not run
   baseline creation over existing inventory tables; those require checked adoption.
4. Run `telescreen inventory-store copy --writers-stopped`, followed by
   `telescreen inventory-store verify --writers-stopped`. Retain their JSON manifests
   as the cutover record. A successful verification compares the actual two stores;
   its table names, counts and content hashes cover every inventory column and row.
   It exposes no stored values or connection URLs. The `copied` field differs between
   a copy and verification, so the complete JSON documents need not be identical.
5. Give every API and worker the same main and inventory runtime URLs, then restart
   them. The existing worker recovers pending delivery. Asset projection may advance
   while the producer outbox drains, and pending audit/retention obligations are
   acknowledged only after their owner transactions commit.

Do not resume the old shared inventory writer alongside the new writer. Producer tables,
queue state, audit/notification receipts and identities remain in main. Main runtime
credentials should have access only to its fifty-two declared business tables; inventory
runtime credentials should have access only to its ten tables. In an existing deployment,
the old inventory tables remain in main as the rollback target. Retaining them does not
grant main's runtime credential permission to use them: use a non-owner runtime role with
grants limited to the main table set in `db/ownership.py`. The migration/transfer owner
can retain access for the controlled rollback procedure.

The full API/worker flow is also tested with non-owner runtime logins granted only those
local DML permissions, including a main schema that retains old inventory tables. The
test denies reads of retained/remote inventory, inverse schema access, migration version
tables and trigger DDL before exercising the application. See
`tests/e2e/test_inventory_runtime_grants.py`.

Once the worker resumes, the old local inventory projection is intentionally stale.
Run manifest verification while writers are stopped immediately before a switch; it
is not a continuous assertion that the two projections stay identical after split.

## Copy and verification behavior

All ten inventory tables move together: assets, append-only events, items, fields,
field values and revision IDs, allocation counter, inbox stream cursors and receipts,
and audit/retention outboxes including delivered markers. Main's producer outbox and
delivery queue are unchanged. A command committed before cutover can therefore replay
against the copied inbox receipt or advance its copied stream cursor without creating
a duplicate asset or losing a pending intent.

Reads and writes use batches of at most 500 rows. Primary keys sort as binary text on
both dialects, including composite numeric keys, so database locale does not change the
manifest. UTC-naive SQLite timestamps are normalized to UTC when binding to PostgreSQL;
hashes preserve timestamp values, exact strings and scalar types.

The source stays unchanged. Target replacement and full manifest verification share one
transaction. PostgreSQL's event trigger is temporarily disabled and re-enabled inside
that transaction; both data and trigger state roll back on failure. SQLite uses explicit
immediate transactions. The tool takes write-excluding locks on both inventory stores.

Initial copy requires an empty target, apart from its allocation seed, or a fully
identical completed copy. A different populated target is refused. If a reply is lost
after commit, rerunning copy recognizes the identical target and returns `copied:false`.
If a transaction fails before commit, rerunning performs the entire copy. Verification
only compares; it never repairs or replaces rows.

## Return to the shared database after new inventory writes

This returns database placement to main while keeping this application version and its
durable delivery protocols. It is not a downgrade to an older application version.

1. Stop every writer again and back up the current authoritative inventory and main
   stores. Keep both configured URLs pointing to their current stores. Keep main's
   original inventory schema available as the rollback target.
2. Run `telescreen inventory-store copy --direction merge --writers-stopped`, followed
   by `telescreen inventory-store verify --direction merge --writers-stopped`. Merge
   copies the current inventory into main, including new assets, new metadata revisions,
   deleted field values, lifecycle changes, updated counters and delivery progress.
3. Unset `TELESCREEN_INVENTORY_DATABASE_URL` (or set it to main). If main's inventory
   history has not yet been adopted, run
   `telescreen migrate --store inventory --adopt --writers-stopped`. This validates and
   stamps the restored inventory schema without changing its contents. Subsequent
   shared `migrate` commands continue the two adopted owner histories. If only one
   owner is adopted and the other has existing unadopted tables, adopt that owner
   explicitly; ordinary migration will not infer adoption.
4. Restart every API and worker using the shared configuration. Asset numbering
   continues from the copied inventory counter. Copied inbox and delivery markers,
   together with receipts that stayed in main, prevent repeat effects during replay.
   Keep the separate store as a stopped backup until the rollback is accepted.

Merge treats the source as authoritative for mutable inventory data. It first verifies
that every target asset keeps its ID, number and resource key; every existing event and
inbox receipt exists unchanged in the source; allocation and stream cursors do not move
backwards; and existing outbox identities/payloads persist without rewinding a committed
acknowledgment. A missing or conflicting lineage is refused without changing target
data. There is no force option: resolve an incorrect source, conflicting writer or
incomplete restore before attempting the switch.

## Verification evidence

`tests/db/test_inventory_transfer.py` covers complete-table manifests, retries, interrupted
copy, target refusal, history/progress guards, mutable rollback and multiple keyset pages.
The CLI tests cover configuration and value-free output. PostgreSQL tests use owner
roles, interrupt actual replacement DML, verify trigger restoration, and round-trip
SQLite/PostgreSQL timestamp and content manifests.

`tests/e2e/test_inventory_store_cutover.py` runs the actual application through both
switches on SQLite and PostgreSQL. It copies a committed inbox receipt whose producer
acknowledgment was lost, an unconsumed observation, pending audit intents and a pending
retention obligation. After split the worker acknowledges/replays them once; new assets
and a new field revision then survive merge. The restored application allocates the next
number, verifies the audit chain and does not send a second overdue notification.
