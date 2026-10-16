# SVC-P04 Inventory Independence: Replica Auth and Lookups, Issue Links, Local Audit, Retention Dispatch, Store Transfer

**Status**: In Progress
**Design Reference**: design-docs/specs/design-service-split.md sections 4.2, 6, 7, 8, 11, 13 (step 4)
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

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
  - `src/telescreen/db/notification_transfer.py`
  - `src/telescreen/cli_inventory_store.py`
  - `tests/inventory_service/conftest.py`
  - `tests/inventory_service/test_runtime.py`
  - `tests/inventory_service/test_transport.py`
  - `tests/inventory_service/test_split_inventory.py`
  - `tests/inventory/handoff/test_split_operations.py`
  - `tests/db/test_notification_transfer.py`
- sharedPaths: none
- artifactRoots: none

**g2 ownership change.** Inventory transfer v2 (`db/transfer.py`, `transfer_rows.py`,
`transfer_check.py`, their tests, `tests/db/transfer_support.py` and
`tests/e2e/test_inventory_store_cutover.py`) moved to svc-p02, because svc-p02's
revisions break transfer v1. `tests/inventory_service/test_config.py` also moved to
svc-p02, which deletes its two inventory-only-remote tests. This plan reuses svc-p02's
transfer helpers and puts its new config and import-gate tests in `test_split_inventory.py`.

## Intent and Context

R1/R5: the inventory process must not call Telescreen. Inventory work in this plan:

- authenticate with `replica.ReplicaAuthenticator`;
- serve enrichment from the issue-link projection and the directory replica;
- drain its audit outbox into its own chain;
- dispatch overdue obligations to Notification through its own outbox, with eligibility
  checked in the committing transaction.

This plan also owns the new `notification-store` copy/verify (design 13 step 4).
Inventory transfer v2 is svc-p02's.

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
   - Do not edit `tests/inventory_service/test_config.py` (svc-p02 deleted its two
     Telescreen-composition tests). Add to `test_split_inventory.py`:
     - the config tests from the Tests section;
     - an isolated-subprocess import gate: importing `telescreen.inventory_service.cli`
       and `telescreen.inventory_service.app` loads none of `telescreen.wiring`,
       `telescreen.wiring_v14`, `telescreen.notifications`, `telescreen.directory`,
       `telescreen.issues` or `telescreen.collectors.service`. Imitate
       `tests/inventory_service/test_config.py:81-113`.
   - The notification-store cutover e2e belongs to svc-p08.
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
   - `apply.py` must stay inside the gate in
     `tests/inventory/core/test_import_boundaries.py`: it may import only
     `telescreen.inventory`, `.core`, `.ports` and `.masking`, plus `typing`. It must not
     import `telescreen.replica` or `telescreen.inventory_service`.
   - Add two protocols in `apply.py`:
     - `IssueLinkSink.apply_link(session, state: IssueLinkState) -> None`;
     - `DirectorySink.apply_entity(session, entity: DirectoryEntity) -> None`.
     `InventoryOperationApplier` gains `links: IssueLinkSink | None = None` and
     `directory: DirectorySink | None = None`.
   - `apply` dispatches every kind by `isinstance`, including an explicit `StopTracking`
     branch; the current `else` must not swallow new kinds. A link or directory operation
     with no sink -> `TelescreenError(VALIDATION_ERROR, "unsupported inventory operation")`,
     so the inbox does not acknowledge it. Single-process wiring passes no sinks, and
     Telescreen never emits these kinds in single process.
   - `inventory_service/links.py:IssueLinkStore(IssueLinkSink)` upserts
     `inventory_issue_links`, and deletes the row when `present=False`.
     `ReplicaDirectorySink(DirectorySink)` calls `DirectoryReplica.apply` with
     `for_inventory` already applied by the producer.
   - Both run inside the existing inbox transaction, so the ordering and receipts are the
     existing ones. Observation and sweep behavior is unchanged.
   - The svc-p02 gate allows `inventory_service/` to import the five new inventory models.
     It still forbids the original 10 inventory models outside `inventory/`. Reach those
     only through `telescreen.inventory` services and repositories.
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
9. **Notification-store transfer.** Inventory transfer v2 is done by svc-p02. Reuse its
   row-copy helpers from `db/transfer_rows.py`; do not edit `db/transfer*.py`.
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
- `tests/inventory/handoff/test_split_operations.py`: a link operation on an applier with
  no sink -> `validation_error`, and the inbox row stays unacknowledged.
- Notification-store transfer (`tests/db/test_notification_transfer.py`): copy/verify
  round-trip for the four moved tables; merge refused when a receipt or template version
  is missing; manifests contain no values.
- `test_split_inventory.py` config cases: a missing notification URL or inventory token
  -> `ConfigError`, and `bureau_service_url` is not required. The import gate passes.

## Verification

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py
uv run --frozen ruff check --no-cache <same paths>
uv run --frozen ty check <same paths>
uv run --frozen pytest -p no:cacheprovider tests/inventory_service/test_config.py tests/inventory_service/test_runtime.py tests/inventory_service/test_transport.py tests/inventory_service/test_split_inventory.py tests/inventory tests/db 2>&1 | tee /tmp/telescreen-svc-p04-inventory-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p04-inventory-typelint.log 2>&1; grep -E '^(src/telescreen/(inventory_service|inventory/handoff|db/notification_transfer|cli_inventory_store)|tests/(inventory_service|inventory/handoff|db/test_notification_transfer))' /tmp/telescreen-svc-p04-inventory-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p04-inventory-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: the scoped commands exit 0 and the typelint grep is empty. `tests/inventory`
includes the svc-p02 import gate and must pass. The full-suite run is informational
because svc-p05 and svc-p06 run concurrently. Failures are acceptable only in the
svc-p08-owned `tests/inventory_service/test_process.py`/`test_postgres.py` or in files
owned by an in-flight wave-4 plan. List every failing test id in the Progress Log.

## Completion Criteria

- [x] `grep -rn "bureau_service_url\|/retention-status" src/telescreen/inventory_service/app.py src/telescreen/inventory_service/cli.py src/telescreen/inventory_service/worker.py` prints nothing.
      `grep -n "clients" src/telescreen/inventory_service/app.py` prints nothing.
- [x] `grep -rln "build_container\|inventory_service.wiring\|from telescreen.directory\|from telescreen.wiring" tests/inventory_service/conftest.py tests/inventory_service/test_runtime.py tests/inventory_service/test_split_inventory.py`
      prints nothing. The gate's forbidden-name strings are quoted module names, not imports.
- [x] `grep -n "telescreen.replica\|telescreen.inventory_service" src/telescreen/inventory/handoff/apply.py` prints nothing.
- [ ] The R5 tests pass. Retention dispatch is one transaction (review the code path).
- [ ] The scoped verification exits 0. The Progress Log has hashes and lists informational failures.

## Progress Log

### Owner note: 2026-10-15 (after session 20)

`inventory/handoff/apply.py` now types `operation` as `InventoryOperation`, dispatches
`StopTracking` explicitly and raises `ValueError` for `IssueLinkState` and
`DirectoryUpdate` (an owner fix for a `ty` error after svc-p01 widened the union).
Replace that `else` branch with the real issue-link and directory application.

(append sessions per conventions section 8)

### Owner resolution: 2026-10-16 (after session 23)

The owner reproduced both blockers and fixed them at the root, outside this plan's paths:
`api/routers/data_assets.py` now imports the pure `actor_kind_for` from
`ports/identity_context.py` (not `telescreen.directory`), and the dispatch eligibility
query moved into inventory as `inventory/retention_outbox.py:retention_status(session,
command, clock)`, which `InventoryRetentionLookup.status` also uses; `inventory_service/
dispatch.py` calls it and `RetentionOutbox.get_pending` inside its single dispatch
transaction and no longer imports inventory SQL models. The svc-p08-owned
`tests/inventory_service/test_postgres.py` and `test_process.py` exercised the removed
inventory-only remote mode; they are now explicit skips with notes until svc-p08 rewrites
them. Owner gates on the checkpoint: ruff format/check, typelint 782 files, ty, full
pytest 1380 passed / 36 skipped. Remaining for this plan: add the R5 retention-dispatch
behavior cases the plan requires, re-run final gates, record hashes, mark Completed
(conventions 7a).

Earlier run 6 sessions (the blockers fixed above) are in
`impl-plans/logs/svc-p04-inventory-2026-10-16.md`.

### Run 7 Scope (manifest g7, base a5392d9)

Do not re-implement anything. Deliverables 1-9 are in the tree. Do not undo the owner
fixes (`inventory/retention_outbox.py:retention_status`, `api/routers/data_assets.py`).
Neither file is in `writePaths`, so do not edit them either. Work in order:

1. Re-run the Verification block on the planning commit. Every command must exit 0
   before you add tests.
2. Audit the Tests section against the tests that exist today:
   - `test_split_inventory.py` has 6 tests;
   - `test_runtime.py` has 2;
   - `tests/inventory/handoff/test_split_operations.py` has only the missing-sink case;
   - `tests/db/test_notification_transfer.py` has 2.
   Add every missing case to `test_split_inventory.py` or `test_split_operations.py`.
   Record a case -> test-id map in the Progress Log.
3. Drive the R5 cases through the real runtime from the `inventory_world` fixture:
   - seed the replica and links through `/commands`;
   - seed an overdue asset with the runtime's asset, field and value services;
   - call `runtime.worker.overdue.handle(...)` (imitate `InventoryWorker.publish_due`);
   - run `runtime.worker.retention_tick()`, then `runtime.worker.notification_tick()`.
   Assert on `inventory_notification_outbox`, `inventory_retention_outbox`, the local
   audit chain and the Notification requests recorded by `ServiceNetwork`. In
   `conftest.py`, extend `ServiceNetwork` with two fields: `notification_receipt`
   (default `APPLIED`, so the existing tests keep working) and the recorded Notification
   request bodies (same `None`-then-list pattern as `requests`). Cases:
   - member owner -> 1 command, `event_id` = obligation id, 1 `MemberSendOperation`,
     `overdue_notified` audit with `recipient_kind=member` and `notification_count=1`;
   - team owner -> 2 `SendOperation`s, EMAIL then SLACK, recipient `team:<slug>`,
     `record_unreachable=True`;
   - value cleared before dispatch -> no command, `overdue_superseded` audit, row
     acknowledged;
   - no owner, reconciled link with `owner_ref` mapped through an identity -> member
     command; no link but a matching rule -> team command;
   - a sample secret (`tests.support.samples`) in the asset locator -> the raw value is
     absent from the outbox payload, and `producer_redaction_hits` is > 0;
   - fake Notification answers `deferred` -> outbox row stays pending; `applied` ->
     acknowledged;
   - one transaction: patch the audit append to raise on `data_asset.overdue_notified`.
     After a tick there is no notification outbox row and the retention row is still
     pending. Remove the patch, and the next tick writes exactly 1 command;
   - every recorded request URL starts with the Notification origin, and no Telescreen
     URL is configured.
4. Never weaken an assertion to pass. If a case fails because of production behavior,
   fix the root cause inside `writePaths`. If the cause is outside them, write a
   Reconciliation request.
5. Re-run Verification. Record hashes under
   `tmp/telescreen-svc-20261016-g7/svc-p04-inventory/`. Tick both open Completion
   Criteria and set Status to Completed. Append one session entry of at most 15 lines.
   Keep `priorVerification` empty (conventions 7a).
