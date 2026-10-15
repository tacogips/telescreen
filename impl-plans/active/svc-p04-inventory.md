# SVC-P04 Inventory Independence: Replica Auth and Lookups, Issue Links, Local Audit, Retention Dispatch, Store Transfer

**Status**: Not Started
**Design Reference**: design-docs/specs/design-service-split.md sections 4.2, 6, 7, 8, 11, 13 (step 4)
**Created**: 2026-10-15
**Last Updated**: 2026-10-15

## Plan Metadata

- planId: `svc-p04-inventory`
- planPath: `impl-plans/active/svc-p04-inventory.md`
- wave: 4
- dependsOn: `svc-p01-foundation`, `svc-p02-db`, `svc-p07-service-runtime`
- writePaths:
  - `impl-plans/active/svc-p04-inventory.md`
  - `src/telescreen/inventory_service/app.py`
  - `src/telescreen/inventory_service/cli.py`
  - `src/telescreen/inventory_service/worker.py`
  - `src/telescreen/inventory_service/lookups.py`
  - `src/telescreen/inventory_service/links.py`
  - `src/telescreen/inventory_service/dispatch.py`
  - `src/telescreen/inventory_service/notify_outbox.py`
  - `src/telescreen/inventory_service/audit_drain.py`
  - `src/telescreen/inventory/handoff/apply.py`
  - `src/telescreen/db/transfer.py`
  - `src/telescreen/db/transfer_rows.py`
  - `src/telescreen/db/transfer_check.py`
  - `src/telescreen/db/notification_transfer.py`
  - `src/telescreen/cli_inventory_store.py`
  - `tests/inventory_service/conftest.py`
  - `tests/inventory_service/test_config.py`
  - `tests/inventory_service/test_runtime.py`
  - `tests/inventory_service/test_transport.py`
  - `tests/inventory_service/test_split_inventory.py`
  - `tests/inventory/handoff/test_split_operations.py`
  - `tests/db/test_inventory_transfer.py`
  - `tests/db/test_inventory_transfer_cli.py`
  - `tests/db/test_inventory_transfer_postgres.py`
  - `tests/db/transfer_support.py`
  - `tests/db/test_notification_transfer.py`
  - `tests/e2e/test_inventory_store_cutover.py`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

R1/R5: the inventory process must not call Telescreen. Inventory work in this plan:

- authenticate with `replica.ReplicaAuthenticator`;
- serve enrichment from the issue-link projection and the directory replica;
- drain its audit outbox into its own chain;
- dispatch overdue obligations to Notification through its own outbox, with eligibility
  checked in the committing transaction.

This plan also owns the store transfer code: inventory transfer v2 and the new
`notification-store` copy/verify (design 13 step 4).

Imitate:

- `inventory_service/app.py:build_inventory_runtime` (current structure);
- `inventory_service/worker.py:OutboxPump`;
- `notifications/retention.py:RetentionNotificationReceiver._notify` (recipient rules,
  audit fields and actions to preserve exactly);
- `issues/asset_owner.py` (fallback order);
- `db/transfer.py` and `cli_inventory_store.py`.

## Non-goals

- Telescreen-side files (`inventory_service/wiring.py`, `bureau.py`, `wiring*.py`,
  `cli.py`) belong to svc-p06. Do not edit them.
- `src/telescreen/inventory_service/clients.py` belongs to svc-p06 (F3). Do not edit or
  delete it; only stop importing it from `app.py`.
- `tests/inventory_service/test_process.py` and `test_postgres.py` belong to svc-p08. They
  exercise the old inventory-only remote mode, are expected to fail until svc-p08 rewrites
  them, and must not be edited here.
- **Wave independence (F3).** svc-p05 and svc-p06 run concurrently. This plan's scoped
  tests must not import `telescreen.wiring*`, `build_container`,
  `telescreen.inventory_service.wiring`, `telescreen.inventory_service.bureau` or
  `telescreen.directory`. They depend only on svc-p01, svc-p02 and svc-p07 output and
  on files this plan owns.
- The single-process retention path (`notifications/retention.py`, `wiring_v14.py`) is
  unchanged.

## Deliverables and File-Level Changes

1. **Stop using the Bureau clients.** Remove the `AuditClient`, `BureauClient` and
   `RetentionClient` imports and uses from `app.py`. Do not touch `clients.py`: svc-p06
   recreates `InventoryClient.receive` in `integrations/inventory_client.py` and deletes
   `clients.py` (F3).
   - Remove the client-specific tests from `tests/inventory_service/test_transport.py`
     (the `BureauClient`/`AuditClient`/`RetentionClient` and `InventoryClient.status`
     cases); svc-p06 re-adds `/commands` client tests.
   - Keep the existing `build_proxy_router(ServiceHttp(...))` test call in
     `test_transport.py` unchanged. svc-p06 keeps a default `paths` argument.
1b. **Test fixtures without Telescreen composition (F3).**
   - Rewrite `tests/inventory_service/conftest.py` and `test_runtime.py` to build only
     `build_inventory_runtime` and `create_inventory_app`.
   - The fixture's in-process service network hosts a fake Notification
     `/internal/notification/v1/commands` endpoint (an `httpx.MockTransport` recording
     receipts). A helper posts `InventoryCommand` envelopes carrying `DirectoryUpdate` and
     `IssueLinkState` to the inventory `/commands` route to seed the replica and links.
   - Replace the old Bureau-callback outage scenarios with the `test_split_inventory.py`
     cases below.
   - In `tests/inventory_service/test_config.py`, delete
     `test_remote_default_migration_creates_main_tables_only` and
     `test_missing_service_credentials_fail_before_resources_are_created`. They test
     Telescreen composition; svc-p06 adds full-split equivalents to
     `tests/e2e/test_wiring_split.py` and `test_wiring_cli.py`. Keep the inventory-runtime
     config tests and the inventory import-gate test, updated so its forbidden list adds
     `telescreen.notifications` and `telescreen.directory`.
   - `tests/e2e/test_inventory_store_cutover.py` (single-process cutover) is updated for
     transfer v2 (`inventory_0002`) and stays green. The notification-store cutover e2e
     belongs to svc-p08.
2. **`app.py:build_inventory_runtime(settings, env, http, sessions=None, clock=None)`.**
   - Requirements: `inventory_database_url`, `inventory_service_token_ref`,
     `notification_service_url` and `notification_inventory_token_ref`; otherwise
     `ConfigError`. It never reads `bureau_service_url`.
   - Build:
     - `DirectoryReplica(InventoryDirectoryEntryRow)`;
     - the OIDC verifier from `settings.oidc` (reuse the identity verifier/discovery
       builder that `wiring_v12.py` uses; import the identity modules, not the wiring);
     - a `Fingerprinter` from the existing key settings;
     - `ReplicaAuthenticator` as `ApiContext.authentication`.
   - `AssetQueries(issues=ReplicaIssueLookup, actors=ReplicaActorLookup)` and
     `ValueValidator(ReplicaAssigneeLookup, redactor)` come from `lookups.py`.
   - `/commands` is unchanged. The applier handles the new kinds.
   - Remove `/retention-status`.
   - `/health` returns `{"status":"ok","directory_revision":N}`.
   - The service-token middleware is unchanged.
3. **`lookups.py`.** These session-free adapters own their transactions on inventory
   sessions:
   - `ReplicaIssueLookup(AssetIssueLookup)` reads `inventory_issue_links` where status is
     in `OPEN_STATUSES` (the same set as `issues/service.py:837`). It returns
     `ResourceIssueRef(resource_key, issue_id, short_id)` in the order
     `IssueAssetLookup` returns.
   - `ReplicaActorLookup(ActorLookup)` wraps `ReplicaActorDirectory`.
   - `ReplicaAssigneeLookup(AssigneeLookup)` wraps `ReplicaRecipientResolver.assignee_of`.
   - `ReplicaAssetOwnerLookup(AssetOwnerLookup)` reproduces `IssueAssetOwnerResolver`:
     1. Find the lowest `issue_id` link for the resource with a `RECONCILED_STATUSES`
        status and a non-null `owner_ref`.
     2. Map it with `ReplicaOwnerDirectory.member_for_owner`.
     3. Otherwise use `ReplicaOwnershipRules.match(source, locator)`.
4. **`links.py` and `inventory/handoff/apply.py`.**
   - `InventoryOperationApplier` applies `IssueLinkState`: upsert
     `inventory_issue_links`; delete the row when `present=False`.
   - `DirectoryUpdate` goes to `DirectoryReplica.apply`.
   - Both run inside the existing inbox transaction, so the ordering and receipts are the
     existing ones. Observation and sweep behavior is unchanged.
5. **`audit_drain.py`.** `LocalAuditDrain.tick()`: per pending `inventory_audit_outbox`
   id, in one inventory transaction:
   1. `get_pending`;
   2. `AuditLogService(models=INVENTORY_AUDIT_MODELS).append` with the stored entry; keep
      the command's `occurred_at` if the service supports it, otherwise use the append
      time and log nothing;
   3. `acknowledge`.

   It rotates batches like `OutboxPump`.
6. **`dispatch.py` and `notify_outbox.py`.** `RetentionDispatcher.tick()` runs per pending
   `inventory_retention_outbox` id in **one** inventory transaction:
   1. `InventoryRetentionLookup` status, computed inside this transaction.
   2. If the obligation is not current: append `data_asset.overdue_superseded` (same
      summary and details as `notifications/retention.py`) to the local chain and
      acknowledge.
   3. Otherwise resolve the assignee. Use the asset `owner_ref` through
      `ReplicaRecipientResolver.assignee_of`, where `recipient_unknown` falls back. Then
      try `ReplicaAssetOwnerLookup.fallback_owner`.
   4. Redact the context strings with `Redactor` and count the hits (same rule as svc-p03
      deliverable 8).
   5. Build `NotificationCommand(event_id=command.event_id, ...)`:
      - member -> one `MemberSendOperation(member_id, ASSIGNED, DATA_OVERDUE, context, redaction)`;
      - team -> two `SendOperation`s (EMAIL then SLACK, recipients `(f"team:{slug}",)`,
        template `system.data_overdue`, `record_unreachable=True`, actor
        `system:data-overdue`).
   6. Append to `inventory_notification_outbox`.
   7. Append `data_asset.overdue_notified` with `recipient_kind`, `notification_count`
      and `obligation_id`.
   8. Acknowledge the retention row.

   `NotificationOutboxPump` (an `OutboxPump`) posts to Notification `/commands` using
   `ServiceHttp(prefix=NOTIFICATION_PREFIX)` and the inventory token. It acknowledges only
   `applied` or `duplicate`. On `deferred` the row stays pending.
7. **`worker.py`.** `InventoryWorker` runs four loops: due publication, audit drain,
   retention dispatch and notification delivery. Each is independent and keeps the
   existing cancellation and logging style.
8. **`cli.py`.** Add `audit verify` (exit 0 when valid, 4 on a broken chain, and print
   only seq and counts) and `audit export` (JSON lines to stdout) using
   `INVENTORY_AUDIT_MODELS`. `serve` and `migrate` are unchanged.
9. **Transfer v2.**
   - `db/transfer*.py` accepts the `inventory_0002` schema. The copy moves the original 10
     tables plus `inventory_audit_records` and the head (append-only lineage on merge: the
     target is a subset of the source).
   - It excludes `inventory_directory_entries` and `inventory_issue_links` (rebuilt by
     resync).
   - It refuses when `inventory_notification_outbox` has undelivered rows.
   - `db/notification_transfer.py` and `cli_inventory_store.py` add
     `telescreen notification-store copy|verify [--direction merge] --writers-stopped` for
     the four moved tables.
   - Merge also copies `notification_audit_records` and the head, and requires
     `retention_notification_receipts` and the template (name, version) pairs of the
     target to be a subset of the source. Otherwise it refuses without changes.
   - Manifests are value-free, like the inventory ones.
   - The commands use `settings.notification_database_url` as the split store and
     `database_url` as main.

## Key Pitfalls

- Eligibility, command write, audit and acknowledgment must be one transaction. Never
  check eligibility in one transaction and write the command in another (R5).
- The dispatcher must reuse the obligation id as the `NotificationCommand.event_id`. That
  id is what makes deduplication work across cutover.
- Never send raw context. Always run the redaction step.
- `recipient_unknown` from the replica falls back. Infrastructure errors propagate and
  leave the row pending.
- Do not import `telescreen.notifications`, `telescreen.directory`, `telescreen.issues`
  or `telescreen.wiring*` from `inventory_service` (R7). Use only `telescreen.replica`,
  `telescreen.inventory`, `telescreen.ports`, `telescreen.core`, `telescreen.masking`,
  `telescreen.identity` and `telescreen.api.common`/routers already used.
- Transfer code: keep the batch size (500), the locks and the trigger handling. Do not
  weaken lineage checks.

## Tests

`tests/inventory_service/test_split_inventory.py`, using an in-process ASGI app and
`make_sqlite_harness(store=INVENTORY)`:

- A bearer for an active replica member with role responder -> `GET /api/data/assets` is 200.
- A revoked API token -> 401. An unknown OIDC subject -> 401.
- An asset detail shows open issues from links, and actor names from the replica (a
  token actor shows its label).
- Owner validation with an unknown ref -> `recipient_unknown`.
- Retention dispatch:
  - an overdue asset with a member owner gives 1 command with a `MemberSendOperation` and
    an `overdue_notified` audit with count 1;
  - a team owner gives 2 `SendOperation`s;
  - a superseded obligation gives no command and an `overdue_superseded` audit;
  - with no owner, the fallback goes first through an issue link plus identity, then
    through the rules team;
  - the context sample secret is absent from the outbox payload and has a hit count.
- A notification `deferred` response -> the row stays pending. `applied` -> acknowledged.
- A fake `http` transport records every request URL and asserts that none goes to a
  Telescreen URL; there is no Telescreen URL configured at all.

Other tests:

- `tests/inventory/handoff/test_split_operations.py`: `IssueLinkState` ordered by
  sequence, a `present=False` removal, and `DirectoryUpdate` applied only if newer.
- The audit drain gives a local chain that verifies, an acknowledged outbox, and a CLI
  `audit verify` exit 0.
- Transfer: copy/verify round-trip for the notification tables; merge refused when a
  receipt is missing; inventory transfer refuses an undelivered notification outbox;
  manifests contain no values. The existing transfer tests are updated for
  `inventory_0002`.
- `tests/inventory_service/test_config.py`: a missing notification URL or inventory token
  -> `ConfigError`, and `bureau_service_url` is not required.

## Verification

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/transfer.py src/telescreen/db/transfer_rows.py src/telescreen/db/transfer_check.py src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db
uv run --frozen ruff check --no-cache <same paths>
uv run --frozen ty check <same paths>
uv run --frozen pytest -p no:cacheprovider tests/inventory_service/test_config.py tests/inventory_service/test_runtime.py tests/inventory_service/test_transport.py tests/inventory_service/test_split_inventory.py tests/inventory tests/db tests/e2e/test_inventory_store_cutover.py 2>&1 | tee /tmp/telescreen-svc-p04-inventory-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p04-inventory-typelint.log 2>&1; grep -E '^(src/telescreen/(inventory_service|inventory/handoff|db/(transfer|notification_transfer)|cli_inventory_store)|tests/(inventory_service|inventory/handoff|db))' /tmp/telescreen-svc-p04-inventory-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p04-inventory-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: the scoped commands exit 0 and the typelint grep is empty. The full-suite run
is informational: only the svc-p08-owned `tests/inventory_service/test_process.py` and
`test_postgres.py` may fail until svc-p08.
List any failing test ids in the Progress Log.

## Completion Criteria

- [ ] `grep -rn "bureau_service_url\|/retention-status" src/telescreen/inventory_service/app.py src/telescreen/inventory_service/cli.py src/telescreen/inventory_service/worker.py` prints nothing.
      `grep -n "clients" src/telescreen/inventory_service/app.py` prints nothing.
- [ ] `grep -rln "telescreen.wiring\|build_container\|inventory_service.wiring\|telescreen.directory" tests/inventory_service/conftest.py tests/inventory_service/test_runtime.py tests/inventory_service/test_config.py tests/inventory_service/test_split_inventory.py`
      prints nothing, except the import-gate test's forbidden-name strings in `test_config.py`.
- [ ] The R5 tests pass. Retention dispatch is one transaction (review the code path).
- [ ] The scoped verification exits 0. The Progress Log has hashes and lists informational failures.

## Progress Log

(append sessions per conventions section 8)
