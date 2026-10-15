# SVC-P02 Database: Nodetto, Exports, Models, Three-Store Ownership, Revisions and Owner Histories

**Status**: In Progress
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
  - `tests/support/db.py`
  - `tests/support/owned_postgres.py`
  - `tests/inventory/core/test_import_boundaries.py`
  - `tests/inventory/core/test_master_authority.py`
  - `tests/e2e/test_inventory_database_config.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` and `db/schema/README.md` first.

## Intent and Context

Create the 12 new tables and declare three disjoint stores. Move four tables to
`Store.NOTIFICATION`. Add the legacy revision `0010_service_split` and the owner histories
`main_0002`, `inventory_0002` and a new packaged baseline `notification_0001`, with
adoption. The DDL order is mandatory:

1. Nodetto JSONL edited directly;
2. `mise run ddl-arrange`;
3. `mise run ddl-export`;
4. models;
5. revisions.

Imitate these existing files:

- `src/telescreen/db/models/inventory_handoff.py` and `audit.py` (model shape, `Base,
  kw_only=True`);
- `src/telescreen/db/migrations/versions/0009_inventory_counter.py` and `0006_inventory_handoff.py`
  (idempotent create-if-missing, PostgreSQL trigger);
- `src/telescreen/db/owned_migrations/versions/main/main_0001.py` (frozen baseline plus seeds);
- `tests/db/test_owned_migrations.py`.

Read `completed/v14-p02-db.md` for the Nodetto record shapes.

## Non-goals

- No repositories or services. Do not touch `db/transfer*` (svc-p04) or `cli.py`
  (svc-p06; it adds `--store notification`).
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

## Key Pitfalls

- `store_for` must still raise for unknown tables. Every model table must be declared;
  `tests/db/test_models.py` enforces this.
- **Corrected (F5).** Legacy revisions use frozen metadata: `0001_initial.py:14` uses
  `metadata_for(1)`. `test_frozen_legacy_migrations.py::test_legacy_migrations_do_not_import_live_models`
  forbids importing `telescreen.db.models` in any legacy revision. Therefore:
  - `0010_service_split.py` declares its 12 tables with explicit `op.create_table` and `sa.*`
    column DDL, matching the models exactly. It never imports `telescreen.db.models`.
  - It stays idempotent: create only if missing, insert seeds only if missing.
  - In `tests/db/test_frozen_legacy_migrations.py::test_historical_targets`, the final
    legacy table count becomes 74 (it was 62). Add a row for `0009_inventory_counter`
    with 62 tables if the test lists per-revision counts.
- The SQLite trigger is not installed (consistent with existing practice).
- The audit models must not share index names with `audit_records`.
- Do not create relations across stores in Nodetto.

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
- The PostgreSQL variants stay marked `integration`.

## Verification

```bash
mise run ddl-arrange > /tmp/telescreen-svc-p02-db-arrange.log 2>&1; echo "exit=$?"
mise run ddl-export > /tmp/telescreen-svc-p02-db-export.log 2>&1; echo "exit=$?"
mise run ddl-check > /tmp/telescreen-svc-p02-db-ddlcheck.log 2>&1; echo "exit=$?"
uv run --frozen ruff format --check --no-cache src/telescreen/db tests/db tests/support
uv run --frozen ruff check --no-cache src/telescreen/db tests/db tests/support
uv run --frozen ty check src/telescreen/db tests/db tests/support
uv run --frozen pytest -p no:cacheprovider tests/db tests/inventory tests/e2e/test_inventory_database_config.py tests/e2e/test_inventory_split_runtime.py tests/e2e/test_inventory_store_cutover.py 2>&1 | tee /tmp/telescreen-svc-p02-db-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p02-db-typelint.log 2>&1; grep -E '^(src/telescreen/db|tests/db|tests/support)' /tmp/telescreen-svc-p02-db-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p02-db-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: every command exits 0, the typelint grep prints nothing, and the full suite
passes (the four tables move stores but stay in the shared database).

## Completion Criteria

- [ ] 12 tables exist in Nodetto, the exports, the models, `0010` and the owner histories.
      `ddl-check` exits 0.
- [ ] `git diff --stat` shows no change to existing revisions, snapshots or `baseline_schema.json`.
- [ ] All verification commands exit 0. The Progress Log has hashes.

## Progress Log

### Session: 2026-10-15 16:57 JST
**Tasks Completed**: Added the 12 Nodetto table definitions in four frames and two
same-store logical relations; generated both 74-table DDL exports; added the new ORM
models, three-store ownership declarations, legacy revision 0010, main_0002 and
inventory_0002 revisions, notification frozen SQL/schema snapshots, and notification
branches in migration/adoption schema validation.
**Tasks In Progress**: Notification owner revision; model, history, adoption, migration,
harness and exact-count tests; required scoped/full verification.
**Blockers**: `tests/db/test_v14_migration.py:50` asserts 62 tables after upgrading to
head, which becomes 74 after 0010. This required shared exact-count test is outside this
plan's `writePaths`; request a checkpoint/manifest amendment to authorize it before
continuing.
**Reconciliation requests**: Add `tests/db/test_v14_migration.py` to the authorized
writePaths for this branch. Resume after the dispatch manifest is amended.
**Hashes**: See `tmp/telescreen-svc-20261015-g1/svc-p02-db/step6/current-hashes-corrected.txt` for
the per-file pre-edit and current hashes; DDL correction hashes are recorded in
`tmp/telescreen-svc-20261015-g1/svc-p02-db/step6/nodetto-hashes.txt`.
**Verification**: `mise run ddl-arrange` -> exit 2 on the first pass (log:
`/tmp/telescreen-svc-p02-db-arrange-attempt1.log`), corrected Nodetto index references and reran
-> exit 0 (log: `/tmp/telescreen-svc-p02-db-arrange.log`); `mise run ddl-export` -> exit 0 (log:
`/tmp/telescreen-svc-p02-db-export.log`). `ddl-check` and Python gates were not run.
**Notes**: The 62-table assertion is in a repository test not listed by this plan even
though the user contract requires updating shared exact-count tests. Per the ownership
rules, no out-of-scope edit or success-shaped verification was attempted. An initial
generator invocation used system Python without SQLAlchemy (exit 1) before producing
files; the generation was rerun under `uv run --frozen` and partially wrote the listed
owner artifacts before finding the missing notification versions directory. That
directory/revision remains unfinished. The original revisions, owner baselines and
existing snapshots were not intentionally edited.

### Owner resolution: 2026-10-15 (after session 18)

The owner updated `tests/db/test_v14_migration.py:50` from 62 to 74 tables at head
(62 existing plus the 12 tables of `0010_service_split`, matching the 74-table Nodetto
exports). The continuation manifest also adds this file to this plan's `writePaths`, so
adjust it again only if the final table count changes. Resume from the checkpointed
partial work: finish the notification owner revision, the model, history, adoption,
migration, harness and exact-count tests, then run `mise run ddl-check` and the full
Python gates. Follow conventions section 7a: report only final passing verification
commands, keep `priorVerification` empty, and do not list fixed findings as risks.
`tests/e2e/test_inventory_runtime_grants.py` (integration marker, 52 main tables)
stays owned by svc-p08 and is skipped offline.
