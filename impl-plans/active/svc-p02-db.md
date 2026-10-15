# SVC-P02 Database: Nodetto, Exports, Models, Three-Store Ownership, Revisions and Owner Histories

**Status**: Code complete at `c3ed4bc`; run 4 re-runs the final gates, records hashes and marks Completed
**Design Reference**: design-docs/specs/design-service-split.md section 12; design-docs/specs/design-data-model.md "Service split additions"; db/schema/README.md; AGENTS.md "Database Schema (DDL)"
**Created**: 2026-10-15
**Last Updated**: 2026-10-15

## Plan Metadata

- planId: `svc-p02-db`
- planPath: `impl-plans/active/svc-p02-db.md`
- wave: 2
- dependsOn: `svc-p01-foundation`
- writePaths:
  - `impl-plans/active/svc-p02-db.md`
  - `db/schema/telescreen.nodetto.jsonl`
  - `db/schema/telescreen.postgresql.sql`
  - `db/schema/telescreen.sqlite.sql`
  - `src/telescreen/db/models`
  - `src/telescreen/db/ownership.py`
  - `src/telescreen/db/migrate.py`
  - `src/telescreen/db/adopt.py`
  - `src/telescreen/db/schema_check.py`
  - `src/telescreen/db/migrations/versions/0010_service_split.py`
  - `src/telescreen/db/owned_migrations/baseline.py`
  - `src/telescreen/db/owned_migrations/notification_0001.postgresql.sql`
  - `src/telescreen/db/owned_migrations/notification_0001.sqlite.sql`
  - `src/telescreen/db/owned_migrations/notification_baseline_schema.json`
  - `src/telescreen/db/owned_migrations/versions/main/main_0002.py`
  - `src/telescreen/db/owned_migrations/versions/inventory/inventory_0002.py`
  - `src/telescreen/db/owned_migrations/versions/notification`
  - `tests/db/test_models.py`
  - `tests/db/test_nodetto_schema.py`
  - `tests/db/nodetto_schema.py`
  - `tests/db/test_owned_migrations.py`
  - `tests/db/test_owned_migrations_postgres.py`
  - `tests/db/test_owner_adoption.py`
  - `tests/db/test_migration.py`
  - `tests/db/test_frozen_legacy_migrations.py`
  - `tests/db/test_service_split_migration.py`
  - `tests/db/test_v14_migration.py`
  - `tests/db/test_v11_migration.py`
  - `tests/db/test_v12_migration.py`
  - `tests/db/test_v13_migration.py`
  - `tests/db/test_inventory_audit_migration.py`
  - `tests/db/test_inventory_counter_migration.py`
  - `tests/db/test_inventory_handoff_migration.py`
  - `tests/db/test_retention_handoff_migration.py`
  - `src/telescreen/db/transfer.py`
  - `src/telescreen/db/transfer_rows.py`
  - `src/telescreen/db/transfer_check.py`
  - `tests/db/test_inventory_transfer.py`
  - `tests/db/test_inventory_transfer_cli.py`
  - `tests/db/test_inventory_transfer_postgres.py`
  - `tests/db/transfer_support.py`
  - `tests/e2e/test_inventory_store_cutover.py`
  - `tests/inventory_service/test_config.py`
  - `tests/support/db.py`
  - `tests/support/owned_postgres.py`
  - `tests/inventory/core/test_import_boundaries.py`
  - `tests/inventory/core/test_master_authority.py`
  - `tests/e2e/test_inventory_database_config.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` and `db/schema/README.md` first.

**Continuation scope (manifest g4, run 4).** Deliverables 1-10 are implemented in
checkpoint `c3ed4bc`, including owner resolution 3 (see Progress Log). The only work left:
1. Re-run every Verification command on the unchanged tree.
2. Record SHA-256 hashes of the owned files that changed since `19074e9`.
3. Tick the Completion Criteria and set Status to Completed.

Do not edit source or test files unless a gate fails. If one fails, reproduce it, fix the
root cause inside these writePaths, and never pass a notification URL to make a test pass
(design 3, 12). Per conventions 7a, `verification` lists only final passing commands and
`priorVerification` stays empty. Shared tests owned here (g2 sweep): the legacy-chain
migration tests (`test_v14_migration.py`: 74 tables at head; never weaken a guard),
transfer v2 tests with `tests/e2e/test_inventory_store_cutover.py`, and
`tests/inventory_service/test_config.py` (the two C9 remote-mode tests are deleted).

g3/g4 sweep result: O1 changes no table count or ownership set, so no new shared test is
needed. `tests/e2e/test_inventory_database_config.py` already holds the owner's O1
expectation at `c3ed4bc` (main file: MAIN + NOTIFICATION tables and both version tables;
design 12). Keep it as is. Do not edit `src/telescreen/cli.py` or `tests/e2e/harness.py`.

## Intent and Context

Create the 12 new tables and declare three disjoint stores. Move four tables to
`Store.NOTIFICATION`. Add the legacy revision `0010_service_split` and the owner histories
`main_0002`, `inventory_0002` and a new packaged baseline `notification_0001`, with
adoption. The DDL order is mandatory: Nodetto JSONL edited directly, `mise run
ddl-arrange`, `mise run ddl-export`, models, then revisions.

Imitate these existing files:

- `src/telescreen/db/models/inventory_handoff.py` and `audit.py` (model shape, `Base,
  kw_only=True`);
- `src/telescreen/db/migrations/versions/0009_inventory_counter.py` and `0006_inventory_handoff.py`
  (idempotent create-if-missing, PostgreSQL trigger);
- `src/telescreen/db/owned_migrations/versions/main/main_0001.py` (frozen baseline plus seeds);
- `tests/db/test_owned_migrations.py`.

Read `completed/v14-p02-db.md` for the Nodetto record shapes.

## Non-goals

- No repositories or services. Do not touch `db/notification_transfer.py` or
  `cli_inventory_store.py` (svc-p04), or `cli.py` (svc-p06; it adds `--store notification`).
- Never edit `0001`-`0009`, `main_0001.py`, `inventory_0001.py`, the four existing
  `*_0001.*.sql` snapshots or `baseline_schema.json`.

## Deliverables and File-Level Changes

1. **Models.** Add new modules under `db/models/`, exported from `db/models/__init__.py`
   and added to `__all__`:
   - `notification_handoff.py`:
     - `NotificationOutboxRow` (`notification_outbox`: id String(36) PK, payload_json Text,
       recorded_at, delivered_at nullable);
     - `InventoryNotificationOutboxRow` (`inventory_notification_outbox`, same columns);
     - `NotificationInboxRow` (`notification_inbox`: id String(36) PK, payload_sha256
       String(64), applied_at);
     - `NotificationDeliveryStateRow` (`notification_delivery_state`: notification_id
       String(36) PK, attempt Integer, max_attempts Integer, available_at (indexed
       `ix_notification_delivery_state_available_at`), leased_until nullable).
   - `projections.py`:
     - `ProjectionChangeRow` (`projection_changes`: id String(36) PK, kind String(16),
       entity_ref String(64), recorded_at);
     - `InventoryDirectoryEntryRow` (`inventory_directory_entries`);
     - `NotificationDirectoryEntryRow` (`notification_directory_entries`).

     Both directory entry tables have kind String(32) and id String(36) as a composite PK,
     plus revision Integer, present Boolean, lookup_key String(128) nullable, payload_json
     Text and applied_at. Each has an index named `ix_<table>_lookup` on (kind, lookup_key).
   - `inventory_links.py`: `InventoryIssueLinkRow` (`inventory_issue_links`: issue_id
     String(36) PK, resource_key String(64) indexed `ix_inventory_issue_links_resource_key`,
     short_id String(32), status String(32), owner_ref String(256) nullable).
   - `service_audit.py`: `InventoryAuditRecordRow`/`InventoryAuditChainHeadRow` and
     `NotificationAuditRecordRow`/`NotificationAuditChainHeadRow`. Their columns, types and
     index names mirror `audit.py`, with table-prefixed index names.
   - Two aliases next to them:
     - `type AuditRecordModel = type[AuditRecordRow] | type[InventoryAuditRecordRow] | type[NotificationAuditRecordRow]`
     - `type AuditHeadModel = type[AuditChainHeadRow] | type[InventoryAuditChainHeadRow] | type[NotificationAuditChainHeadRow]`

     And in `projections.py`:
     `type DirectoryEntryModel = type[InventoryDirectoryEntryRow] | type[NotificationDirectoryEntryRow]`.

     These aliases are the typing contract used by svc-p07.
2. **Nodetto.** Add frames `f-projections`, `f-notification-handoff`,
   `f-inventory-links` and `f-service-audit` with these tables (exact columns and index
   names as above). Logical relations are drawn only within a store:
   - `notification_delivery_state.notification_id` -> `notifications.id`;
   - `inventory_issue_links.resource_key` -> `data_assets.resource_key`, only if that
     column is unique; check the existing JSONL and skip the relation if it is not.

   Then run `mise run ddl-arrange` and `mise run ddl-export`.
3. **Ownership.** `db/ownership.py`:
   - `Store.NOTIFICATION = "notification"` and `NOTIFICATION_TABLES` (9 tables, design 12).
   - Move `notifications`, `notification_templates`, `notification_settings` and
     `retention_notification_receipts` out of `MAIN_TABLES`, and add
     `notification_outbox` and `projection_changes`. MAIN has 50 tables.
   - INVENTORY gains its 5 new tables (15).
   - `tables_for` and `store_for` handle all three. The sets stay disjoint.
   - **Review F5.** Add `MAIN_RETAINED_TABLES: tuple[str, ...]` with the four moved tables
     and `main_database_tables() -> tuple[str, ...]` = `MAIN_TABLES + MAIN_RETAINED_TABLES`.
     The docstring states that `main_0001` keeps these four tables, and that a
     single-process main database hosts and uses them (design 3). In full split they are
     retained and ungranted.
   - `tables_for(Store.MAIN)` stays the 50 owner tables. Use `main_database_tables()`
     wherever a test or harness means "what a single-process main database contains".
4. **Legacy revision.** `0010_service_split.py` (down_revision `0009_inventory_counter`)
   is idempotent and creates the 12 new tables if missing. It seeds:
   - `counters('directory_revision', 0)`;
   - `inventory_audit_chain_head(1, 0, '0'*64)`;
   - `notification_audit_chain_head(1, 0, '0'*64)`.

   On PostgreSQL it installs the append-only triggers for `inventory_audit_records` and
   `notification_audit_records`. Use distinct function names
   (`telescreen_reject_inventory_audit_mutation`,
   `telescreen_reject_notification_audit_mutation`). Downgrade drops only what 0010 added.
5. **Owner histories.**
   - `main_0002.py` (down `main_0001`): `notification_outbox`, `projection_changes` and the
     counter seed.
   - `inventory_0002.py` (down `inventory_0001`): 5 tables, head seed and trigger.
   - `versions/notification/notification_0001.py`: `create_baseline("notification_0001")`,
     head seed and trigger. The snapshots `notification_0001.{postgresql,sqlite}.sql`
     contain the 9 NOTIFICATION tables (generate them from the models, then freeze; the
     header comment says frozen).
   - `baseline.py`: allow `notification_0001` in `_snapshot`. Extend `append_only` to the
     two new audit tables with their function names. Do not change behavior for existing
     names.
6. **Adoption and migrate.**
   - `schema_check.py`: `validate_owner_schema(connection, Store.NOTIFICATION)` uses
     `notification_baseline_schema.json`, a new frozen file in the same format as
     `baseline_schema.json` covering the 9 tables.
   - `adopt.py:validate_owner_state`: for NOTIFICATION, require the head row and the
     trigger (PostgreSQL); there is no counter.
   - `migrate.py`:
     - `owner_config` works for `notification`.
     - `_has_owner_history` also checks `alembic_version_notification`.
     - `upgrade_configured(..., notification_database_url: str | None = None)` handles
       `store is Store.NOTIFICATION` (URL = notification URL or main URL).
     - In the shared owner-history path, upgrade the notification history only if
       `alembic_version_notification` has a version; otherwise raise `ConfigError`
       "notification history requires adoption".
     - The legacy path (`upgrade`) is unchanged.
7. **Test support.**
   - `tests/support/db.py:make_sqlite_harness(store=...)`:
     - `Store.MAIN` creates `main_database_tables()` plus the seeds (F5). This keeps the
       single-process separate-inventory composition in `tests/e2e/harness.py:161-164` and
       `tests/e2e/test_inventory_split_runtime.py` working.
     - `Store.NOTIFICATION` creates only the 9 notification tables plus the head seed.
     - Keep the existing defaults.
   - `tests/support/owned_postgres.py` supports a third role if it enumerates stores.
   - In `tests/db/test_owned_migrations.py` and `test_owned_migrations_postgres.py`, the
     exact table-set assertion for a main owner store created by `main_0001`+`main_0002`
     is `set(main_database_tables())`, because the baseline keeps the retained tables.
8. **Inventory transfer v2 (design 13 step 4; moved from svc-p04).**
   - `transfer_check.py`: the source must be at legacy `0010_service_split` and the target
     owner at `inventory_0002`. Keep the exact error texts' style (static, value-free).
   - `transfer.py`/`transfer_rows.py`: copy the original 10 inventory tables plus
     `inventory_audit_records` and `inventory_audit_chain_head`. On a fresh shared source
     the audit pair holds only the genesis head. Merge/rollback requires the target audit
     rows to be a prefix-subset of the source (append-only lineage). Exclude
     `inventory_directory_entries` and `inventory_issue_links` (rebuilt by
     `projections resync`). Refuse (no writes) when `inventory_notification_outbox` has rows
     with `delivered_at IS NULL` on either side.
   - The report `schema_revision` becomes `inventory_0002`. Keep batch size 500, the locks,
     trigger handling and value-free manifests unchanged.
   - Export the row-copy helpers that svc-p04's `notification_transfer.py` will reuse
     without renaming existing public names.
9. **Inventory model import gate (`tests/inventory/core/test_import_boundaries.py`).**
   - `test_every_database_table_has_one_explicit_owner`: the union of the three stores
     equals `Base.metadata.tables`; the sets are pairwise disjoint; `store_for` maps every
     NOTIFICATION table to `Store.NOTIFICATION`; the inventory mapper-name set equals the
     10 existing names plus `InventoryDirectoryEntryRow`, `InventoryIssueLinkRow`,
     `InventoryAuditRecordRow`, `InventoryAuditChainHeadRow` and
     `InventoryNotificationOutboxRow`.
   - Keep the existing 10 names forbidden everywhere outside `inventory/`, `db/` and the
     two data routers, exactly as today.
   - The five new names may also be imported by `inventory_service/` (svc-p04),
     `replica/` (svc-p07) and `audit/chain.py` (svc-p07 audit model constants). Express
     this as a second frozenset with its own allow-list. Do not widen the rule for the
     original 10.
   - `test_inventory_depends_only_on_owned_models` (`telescreen/inventory/**`) keeps its
     `_ALLOWED` module prefixes. svc-p04 must keep `inventory/handoff/apply.py` within them.
10. **Co-located notification history (O1, design 12; run 3).** Root cause of the three
    full-suite failures: with distinct main and inventory URLs and no notification URL,
    `upgrade_configured` runs `notification_0001` on the main store, where `create_baseline`
    raises "owner baseline requires an empty store".
    - **Selection (configuration only).** In `migrate.py:upgrade_configured`, only the
      final branch (inventory URL distinct from main) with `notification_database_url` None
      or equal to `database_url` upgrades NOTIFICATION through a config from `owner_config`
      with `config.attributes["colocated_notification"] = True`, after MAIN and INVENTORY.
      Every other path is unchanged: the shared single-URL branch (including the
      "notification history requires adoption" `ConfigError`), `store=` calls, a distinct
      notification URL and the legacy `upgrade`. Never infer co-location from table
      presence.
    - **Revision.** `versions/notification/notification_0001.py` upgrade: if the attribute
      is not exactly `True`, keep today's behavior (`create_baseline`, head seed, trigger).
      Otherwise call one helper in `owned_migrations/baseline.py`, for example
      `create_colocated_notification() -> None`, that reads the attribute through
      `context.config.attributes` with an `is True` check (typelint: no `Any` leaks) and
      decides the case:
      - case (a) fresh `main_0001` main: the four co-located tables exist, none of the five
        notification-only tables exist, and there is no legacy `alembic_version` row
        `0010_service_split`. Order: subset-validate the four tables (no trigger check),
        then execute only the snapshot statements whose target table (`CREATE TABLE <t>` or
        `CREATE INDEX ... ON <t>`) is one of the five, seed the head `(1, 0, '0'*64)`, and
        last `append_only("notification_audit_records")`.
      - case (b) main adopted from legacy `0010_service_split` (legacy `alembic_version` is
        `0010_service_split`, all nine tables exist): run
        `validate_owner_schema(connection, Store.NOTIFICATION)` and
        `adopt.validate_owner_state(connection, Store.NOTIFICATION)`; create, seed and
        install nothing.
      - any other state (some of the five present, four missing, legacy at another
        revision): raise a static, value-free `RuntimeError`.
      Alembic stamps `notification_0001` only after `upgrade()` returns, in the same
      `env.py` transaction. Validate before any DDL so a failure leaves no writes on SQLite.
    - **Validation helper.** `schema_check.py` gains a table-subset form, for example
      `validate_owner_schema(connection, store, tables=...)`, defaulting to every table of
      the store, so existing callers are unchanged. The subset form checks only the named
      tables and never calls `_validate_trigger` (on PostgreSQL it would `CAST` the absent
      `notification_audit_records` and raise UndefinedTable). The full-store default, used by
      existing callers and case (b), keeps today's PostgreSQL trigger check. `adopt.py` may
      expose a helper for case (b), but adoption behavior stays the same.
    - **Downgrade.** If `alembic_version_main` exists in the same database, drop only the
      five notification-only tables (reverse snapshot order) and the append-only trigger.
      Otherwise keep `drop_baseline`.
    - Do not edit the `notification_0001.*.sql` snapshots, `notification_baseline_schema.json`,
      `main_0001`/`inventory_0001` or any legacy revision.

## Key Pitfalls

- `store_for` must still raise for unknown tables. Every model table must be declared;
  `tests/db/test_models.py` enforces this.
- (F5, done) `0010_service_split.py` uses explicit `op.create_table` DDL, never imports
  `telescreen.db.models`, and stays idempotent; the final legacy table count is 74.
- No SQLite trigger; audit index names are table-prefixed; no cross-store Nodetto relations.
- O1: never pass a notification URL from `cli.py` or the harness (design section 3).

## Tests

- `tests/db/test_models.py`: the exact 74-table set. Every table maps to exactly one store.
  MAIN has 50, INVENTORY 15 and NOTIFICATION 9 tables. `main_database_tables()` has 54
  entries and its retained part equals the four moved tables.
- `make_sqlite_harness(store=Store.MAIN)` -> `notifications` exists.
  `tests/e2e/test_inventory_split_runtime.py` (single-process, separate inventory store;
  not owned here, run only) passes.
- `tests/db/test_nodetto_schema.py`: green after export (indexes, relations).
- `tests/db/test_service_split_migration.py`:
  - Upgrading a 0009 SQLite database to head creates 12 tables and the seeds. Running it
    twice is a no-op.
  - Downgrade to 0009 removes them.
- `tests/db/test_owned_migrations.py`:
  - A fresh notification store from `notification_0001` has exactly 9 tables and a head row.
  - `main_0002` and `inventory_0002` apply on top of the existing baselines.
- `tests/db/test_owner_adoption.py`:
  - A legacy database at head adopts `--store notification` and stamps `notification_0001`.
  - A notification store missing `notification_inbox` -> adoption refused.
- `migrate.upgrade_configured` with adopted main/inventory but unadopted notification ->
  `ConfigError`.
- Inventory transfer v2: the existing copy/verify/rollback/retry/keyset tests pass against
  a `0010` source and an `inventory_0002` target; an undelivered
  `inventory_notification_outbox` row -> refused with no target writes; a source with
  replica rows -> copied target has no replica rows; the CLI test asserts
  `"schema_revision":"inventory_0002"` and no values in the output.
- `tests/e2e/test_inventory_store_cutover.py` (single-process cutover) is green on v2.
- `tests/inventory/core/test_import_boundaries.py` passes with deliverable 9's sets.
- The PostgreSQL variants stay marked `integration`.
- O1 regression tests in `tests/db/test_service_split_migration.py` (SQLite files,
  implemented at `c3ed4bc`): case (a) fresh main + separate inventory upgraded twice
  (nine tables, one head row, stamped, no-op second run); case (b) legacy-adopted main
  (stamp only); partial state and schema mismatch (raise, no stamp, nothing created); a
  distinct notification URL still refused; the co-located downgrade keeps the four tables.
  `test_default_shared_migrate_requires_notification_adoption` passes unchanged; the
  PostgreSQL case (a) variant stays `integration` (skipped offline, not verification).
- `tests/e2e/test_inventory_database_config.py`: main file = MAIN + NOTIFICATION tables
  plus both version tables; inventory file = INVENTORY tables (design 12, O1).

## Verification

Run 4 does not run `ddl-arrange`/`ddl-export`: the exports are final at `c3ed4bc`. Run
them only if `ddl-check` fails.

```bash
mise run ddl-check > /tmp/telescreen-svc-p02-db-ddlcheck.log 2>&1; echo "exit=$?"
uv run --frozen ruff format --check --no-cache src/telescreen/db tests/db tests/support tests/inventory/core tests/inventory_service/test_config.py tests/e2e/test_inventory_store_cutover.py tests/e2e/test_inventory_database_config.py
uv run --frozen ruff check --no-cache <same paths>
uv run --frozen ty check <same paths>
uv run --frozen pytest -p no:cacheprovider tests/db tests/inventory tests/inventory_service/test_config.py tests/e2e/test_inventory_database_config.py tests/e2e/test_inventory_split_runtime.py tests/e2e/test_inventory_store_cutover.py 2>&1 | tee /tmp/telescreen-svc-p02-db-pytest.log
uv run --frozen pytest -p no:cacheprovider tests/db/test_service_split_migration.py tests/db/test_owned_migrations.py tests/db/test_owner_adoption.py tests/e2e/test_inventory_database_config.py tests/e2e/test_inventory_split_runtime.py tests/e2e/test_inventory_store_cutover.py 2>&1 | tee /tmp/telescreen-svc-p02-db-o1.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p02-db-typelint.log 2>&1; grep -E '^(src/telescreen/db|tests/db|tests/support|tests/inventory/core|tests/inventory_service/test_config|tests/e2e/test_inventory_(store_cutover|database_config))' /tmp/telescreen-svc-p02-db-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p02-db-pytest-all.log 2>&1; echo "exit=$?"
git diff --quiet c3ed4bc -- src/telescreen/cli.py tests/e2e src/telescreen/db/owned_migrations/notification_0001.postgresql.sql src/telescreen/db/owned_migrations/notification_0001.sqlite.sql src/telescreen/db/owned_migrations/notification_baseline_schema.json; echo "exit=$?"
test "$(wc -l < impl-plans/active/svc-p02-db.md)" -lt 400; echo "exit=$?"
```

Each command must print `exit=0` (typelint: the grep prints nothing). The full suite log
ends with `passed` and no `failed`/`error` count.

## Completion Criteria

- [x] 12 tables exist in Nodetto, the exports, the models, `0010` and the owner histories.
      `ddl-check` exits 0.
- [x] `git diff --stat` shows no change to existing revisions, snapshots or `baseline_schema.json`.
- [x] `grep -n "0009_inventory_counter\|inventory_0001" src/telescreen/db/transfer_check.py`
      prints nothing. Transfer v2 tests and the cutover e2e pass.
- [x] `grep -n "test_remote_default_migration_creates_main_tables_only\|bureau_service_url" tests/inventory_service/test_config.py`
      prints nothing.
- [ ] O1 (deliverable 10): the O1 regression tests and the three e2e tests pass;
      `git diff --quiet c3ed4bc -- src/telescreen/cli.py tests/e2e` and the
      `notification_0001.*.sql` snapshots and `notification_baseline_schema.json` exits 0.
- [ ] All verification commands exit 0, including the full suite. The Progress Log has
      hashes under `tmp/telescreen-svc-20261015-g4/svc-p02-db/`.
- [ ] Status is `Completed`; this file stays under 400 lines.

## Progress Log

Earlier entries: [sessions 18-19](../logs/svc-p02-db-2026-10-15.md) and
[run 3 with owner resolution 3](../logs/svc-p02-db-2026-10-15-run3.md) (do not edit
either archive). Summary: deliverables 1-10 are in checkpoint `c3ed4bc`. The owner
updated `tests/e2e/test_inventory_database_config.py` to the O1 layout (design 12) and
fixed the `ty` error in svc-p04's `inventory/handoff/apply.py`. Owner gates on `c3ed4bc`:
ruff format/check, typelint (731 files), ty, `mise run ddl-check` and the full offline
suite (1,319 passed, 36 skipped) all exit 0. Run 4 appends one session entry here
(conventions 8, at most 10 lines) with the final gate results, log paths and the hash
file path.
