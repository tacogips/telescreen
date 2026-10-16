# svc-p04-inventory Progress Log archive (2026-10-16, run 6)

These entries were moved unchanged from `impl-plans/active/svc-p04-inventory.md` in the run 7
planning commit (manifest g7), to keep the plan under 400 lines. The owner fixed both
blockers recorded here; see "Owner resolution" in the plan. Root `notification-store`
dispatch is a svc-p06 deliverable in run 7.

### Session: 2026-10-16 14:30 Asia/Tokyo

**Tasks Completed**: Replaced the Inventory runtime's Bureau callbacks with replica authentication and local lookups; added issue-link/directory sinks, local audit drain, Notification outbox delivery, retention dispatch, audit CLI commands, notification transfer primitives, and inventory-only test fixtures. Added copy/verify and missing-receipt transfer tests.

**Tasks In Progress**: R5 dispatch behavioral coverage; import-boundary repairs; notification-store root CLI routing; svc-p08-owned import-gate and Postgres fixture rewrites.

**Blockers**: The accepted plan `writePaths` omits three required repair paths. (1) `src/telescreen/api/routers/data_assets.py` imports `telescreen.directory.actors`, so importing the inventory app loads the forbidden directory package; the import gate fails. (2) `src/telescreen/inventory_service/dispatch.py` imports the original Inventory ORM models and fails `tests/inventory/core/test_import_boundaries.py`; an in-session eligibility/owner lookup must be exposed through `src/telescreen/inventory/retention_outbox.py` to preserve the one-transaction R5 contract without those imports. (3) `telescreen notification-store` is parsed in `src/telescreen/cli_inventory_store.py` but `src/telescreen/cli.py` does not dispatch it; this command path is outside the committed writePaths. Resume after a checkpoint amends writePaths for those files (or assigns the specific fixes to their owners). The required scoped pytest currently has those two classified implementation failures.

**Reconciliation requests**: Add the three exact paths above to svc-p04 ownership or assign their fixes to the corresponding owners. `ty check` also reports svc-p08-owned `tests/inventory_service/test_postgres.py` references to the removed `RemoteWorld` fixture and three removed runtime tests; svc-p08 must rewrite that suite before the package-wide type check can pass.

**Hashes**:
- `src/telescreen/inventory/handoff/apply.py` before=`643ce2c644aa288f347321fa91eecdee61628612d2e6ba48d8d27bd8b62045e9` after=`02a29e3df8529810cfd96e622cbe5de57650f2e891a611bd2f59531dc5dbf55f`
- `src/telescreen/inventory_service/app.py` before=`77b5907beb307919d635589efe6099de7bf07a4c8785d93d78a2b23b945992a5` after=`742b9454154156ddb97fb0c0735211b6fa5a6c21693266536431b8698f4e3122`
- `src/telescreen/inventory_service/cli.py` before=`255aefb17c341018b8f1d294e3081ca5dc592527bf58cc5b018dcdfc5c774085` after=`54e1c430f596ed82b2cafc69a810289a0b51184fed666bb50d96efe99c9d8624`
- `src/telescreen/inventory_service/worker.py` before=`53c75cf10ae8388aea682ed3355e31c4c943aec042c5b3931f52a041a3a1c4ec` after=`7b5859daa28183c33e7baaf1eec4c00c846a888738adc216b2a43aa63f47971`
- New source files `src/telescreen/inventory_service/{lookups,links,dispatch,notify_outbox,audit_drain}.py` and `src/telescreen/db/notification_transfer.py`: before=absent; after hashes are recorded in the plan-local Riela evidence directory.
- `src/telescreen/cli_inventory_store.py` before=`fa8b207b04395b0b68d4ee6b7a25529afd72fc548a57f473ba2e202dc2bc2868` after=`3b7dde6e09b95bfb1bba1cd73c29e44c946f6bccf57a5324374758f06f714de6`
- `tests/inventory_service/conftest.py` before=`14343b3aa7145a031c3b7dc72478e56d2b20c17e69a63f8f798067177a9f1c98` after=`989143ab7409823b290f9c645cc9b389b24ff1a6210ef50b76e6df2ec1b5ccf5`; `test_runtime.py` before=`e4f2fb427418209631794faf3c8598027f92a0d52f38864e4826563a4e7f4724` after=`2a4dede35828257c1f5c13a777fe25d071cab34d40ff8534e6df438edc15fd7f`; `test_transport.py` before=`5517c9e0b61a75635c24a44cdc744defbdb394fe3b5860498c77d42171eaf8a0` after=`0bd33c24f78b55760ae1211f075beaa47f09da515b3c29233118acdf08171803`.
- New test files `tests/inventory_service/test_split_inventory.py`, `tests/inventory/handoff/test_split_operations.py`, and `tests/db/test_notification_transfer.py`: before=absent; final hashes are recorded in the plan-local Riela evidence directory.

**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 0 (`/tmp/telescreen-svc-p04-inventory-format-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 0 (`/tmp/telescreen-svc-p04-inventory-ruff-final.log`).
- `uv run --frozen ty check src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 1; five diagnostics in svc-p08-owned `tests/inventory_service/test_postgres.py` reference the removed fixture/runtime tests (`/tmp/telescreen-svc-p04-inventory-ty-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/inventory_service/test_config.py tests/inventory_service/test_runtime.py tests/inventory_service/test_transport.py tests/inventory_service/test_split_inventory.py tests/inventory tests/db` -> exit 1; 207 passed, 2 failed, 22 skipped. Failing IDs: `test_inventory_cli_and_app_import_without_telescreen_composition`, `test_other_business_modules_cannot_import_inventory_sql_models` (`/tmp/telescreen-svc-p04-inventory-pytest-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/inventory_service/test_split_inventory.py` -> exit 1; 5 passed, 1 failed on the final test-source snapshot (`/tmp/telescreen-svc-p04-inventory-import-gate-final.log`).
- `uv run --frozen python tools/typelint.py` -> exit 1 for one concurrent svc-p06 diagnostic in `src/telescreen/cli.py`; the svc-p04 path filter is empty (`/tmp/telescreen-svc-p04-inventory-typelint-final2.log`).
- The three grep checks above -> exit 0 after quoting the import-gate forbidden module string.
- `uv run --frozen ruff format --check --no-cache tests/inventory_service/test_split_inventory.py` -> exit 0; `uv run --frozen ruff check --no-cache tests/inventory_service/test_split_inventory.py` -> exit 0.
- `git diff --check -- <svc-p04 changed paths>` -> exit 0.

**Notes**: No GitHub issue. Issue reference remains the workflow input and accepted design in the plan metadata. Wave B full-suite verification was not run; scoped failures are not accepted as passing evidence.

### Session: 2026-10-16 Step 6 rerun

**Tasks Completed**: Reproduced the two remaining svc-p04 boundary failures on the current shared tree. Confirmed that the notification-store parser and runner exist in `src/telescreen/cli_inventory_store.py`; root command dispatch remains svc-p06-owned in `src/telescreen/cli.py` and is downstream integration work.

**Tasks In Progress**: Import-gate compliance and same-transaction retention eligibility remain unverified because the required repair paths are outside this plan's committed `writePaths`. The assigned tests currently contain no retention-dispatch behavior cases, so R5 behavioral coverage is also incomplete.

**Blockers**: `tests/inventory_service/test_split_inventory.py::test_inventory_cli_and_app_import_without_telescreen_composition` fails because `src/telescreen/api/routers/data_assets.py` imports `telescreen.directory.actors`; that router is outside svc-p04 `writePaths`. `tests/inventory/core/test_import_boundaries.py::test_other_business_modules_cannot_import_inventory_sql_models` fails because `src/telescreen/inventory_service/dispatch.py` imports `DataAssetRow`, `DataFieldRow`, and `DataFieldValueRow` directly. Preserving the required single inventory transaction while moving this access behind the Inventory domain boundary requires an owner-approved repair in `src/telescreen/inventory/retention_outbox.py`, also outside `writePaths`. Resume after ownership/write-path amendment or explicit assignment of those two repairs.

**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 0 (`tmp/telescreen-svc-20261016-g6/svc-p04-inventory/step6-final-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 0 (`tmp/telescreen-svc-20261016-g6/svc-p04-inventory/step6-final-ruff.log`).
- `uv run --frozen ty check src/telescreen/inventory_service src/telescreen/inventory/handoff src/telescreen/db/notification_transfer.py src/telescreen/cli_inventory_store.py tests/inventory_service tests/inventory/handoff tests/db/test_notification_transfer.py` -> exit 1; five diagnostics are confined to svc-p08-owned `tests/inventory_service/test_postgres.py` (`tmp/telescreen-svc-20261016-g6/svc-p04-inventory/step6-final-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/inventory_service/test_config.py tests/inventory_service/test_runtime.py tests/inventory_service/test_transport.py tests/inventory_service/test_split_inventory.py tests/inventory tests/db` -> exit 1; 207 passed, 2 failed, 22 skipped. Failing IDs are `test_inventory_cli_and_app_import_without_telescreen_composition` and `test_other_business_modules_cannot_import_inventory_sql_models` (`tmp/telescreen-svc-20261016-g6/svc-p04-inventory/step6-final-pytest.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0 (`tmp/telescreen-svc-20261016-g6/svc-p04-inventory/step6-final-typelint.log`).
- `git diff --check -- impl-plans/active/svc-p04-inventory.md src/telescreen/inventory_service/app.py` -> exit 0.
- Root notification-store dispatch remains downstream svc-p06 work; do not count it as a p04 completion or failure until the serial combined-tree review checks `src/telescreen/cli.py` dispatch.
