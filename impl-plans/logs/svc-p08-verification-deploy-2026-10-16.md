# svc-p08-verification-deploy Progress Log archive (2026-10-16, run 7)

These entries were moved unchanged from `impl-plans/active/svc-p08-verification-deploy.md` in the
run 8 planning commit (manifest g8), to keep the plan under 400 lines. The owner resolution
entry is restated as the plan's "Run 8 Scope" section.

### Session 7 implementation (2026-10-16)

Status remains In Progress because full R1-R8 acceptance flows are not yet implemented.
No production code or DDL changed.

Implemented in this plan:

- Rewrote `tests/inventory_service/test_process.py` to migrate and start Telescreen API,
  Telescreen worker, Inventory and Notification against three SQLite files. It runs
  `projections resync`, creates an API token through the CLI, registers/reads an Inventory
  asset through the TCP proxy, checks a runtime sample is rejected without echo, scans all
  three database files, service logs and API responses, and terminates every process in
  `finally`.
- Added `tests/split/test_import_gates.py`, `tests/split/test_cutover.py`,
  `tests/leak/flows_split.py` and `tests/leak/test_leak_matrix_split.py`.
- Rewrote `tests/inventory_service/test_postgres.py` as an integration-marked three-role
  ownership/grant test. It was skipped offline because `TELESCREEN_TEST_DATABASE_URL` is
  not set; no live grant result is claimed.
- Added `examples/compose/docker-compose.services.yml` with three owner databases and
  three service processes, deleted `docker-compose.inventory.yml`, and updated the example
  validator, `.env.example` and README.
- Added `design-docs/specs/design-service-split-verification.md`; updated the service-split
  design status, token projection label and allowed DLP/guard imports; corrected the
  command and inventory deployment documents.

Source hash manifest: `tmp/telescreen-svc-20261016-g7/svc-p08-verification-deploy/attempt-1/source-hashes.final.sha256`.

Verification commands (final source unless otherwise noted):

- `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — exit 0, 68 files; `/tmp/telescreen-svc-p08-verification-deploy-g7-final-format.log`.
- `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-final-ruff.log`.
- `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-final-ty.log`.
- `uv run --frozen python tools/typelint.py` with the p08 path filter — exit 0, no p08 diagnostics; `/tmp/telescreen-svc-p08-verification-deploy-g7-final-typelint.log`.
- `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e` — exit 0, 175 passed, 4 skipped; `/tmp/telescreen-svc-p08-verification-deploy-g7-final-pytest.log`.
- `mise run ci` — exit 0, 1397 passed, 35 skipped, 1 warning; full log `/tmp/telescreen-svc-p08-verification-deploy-g7-final-ci.log`.
- `mise run ddl-check` — exit 0, canonical exports and 10 schema tests passed; `/tmp/telescreen-svc-p08-verification-deploy-g7-ddl.log`.
- `mise run web-ci` — exit 0, 49 test files and 262 tests passed; `/tmp/telescreen-svc-p08-verification-deploy-g7-web.log`.
- `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g7-dist` — exit 0; source distribution and wheel created; `/tmp/telescreen-svc-p08-verification-deploy-g7-build.log`.
- `uv run --frozen pytest -p no:cacheprovider -q tests/inventory_service/test_process.py tests/leak/test_leak_matrix_split.py` — exit 0, 2 passed; `/tmp/telescreen-svc-p08-attempt1-process-leak-final.log`.
- `uv run --frozen pytest -p no:cacheprovider -q tests/inventory_service/test_process.py tests/leak/test_leak_matrix_split.py` — review-repair source, exit 0, 2 passed; `/tmp/telescreen-svc-p08-g7-r3-process-leak.log`.
- `! grep -n 'pytest.mark.skip\|pytest.skip(' tests/inventory_service/test_process.py && test ! -e examples/compose/docker-compose.inventory.yml && test -f examples/compose/docker-compose.services.yml` — exit 0.

Earlier repair attempts in this session: the process harness first used an invalid fingerprint secret reference, then probed protected health routes without service headers; both were corrected and the final process tests pass. The raw locator sample was correctly rejected with 422 and the test now asserts the safe rejection. An early broad pytest shell used zsh's read-only `status` variable and produced an incomplete log; the final scoped pytest and `mise run ci` above were rerun with complete logs.

### Session 8 review repair (2026-10-16)

- Fixed P08-R1R8-MATRIX's dead Notification destination: Inventory now targets the allocated live Notification TCP port.
- Strengthened the process leak probe with a successful source-content ingest using a runtime sample; it asserts 200 and checks the returned body, three database files, process logs and API bodies for raw samples.
- Focused process/leak verification: 2 passed, exit 0; `/tmp/telescreen-svc-p08-g7-r3-process-leak.log`.
- Scoped final-source suite: 175 passed, 4 environment-gated integration skips, exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-pytest.log`.
- `ruff format --check`, `ruff check`, `ty check`, and `python tools/typelint.py`: exit 0; logs `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-{format,ruff,ty,typelint}.log`.
- `mise run ci`: exit 0, 1397 passed, 35 skipped, 1 warning; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ci.log`.
- `mise run ddl-check`: exit 0, 10 schema tests passed; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ddl.log`.
- `mise run web-ci`: exit 0, 262 tests passed and production build completed; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-web.log`.
- `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g7-r3-dist`: exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-build.log`.
- Full P08-R1R8-MATRIX and P08-LEAK-MATRIX remain incomplete: no cross-process producer delivery/replay, stopped-Telescreen overdue, full cutover delivery/dedup, or all-table/wire/audit-export scan is present. No production source or DDL was changed.
- Required remaining gate: implement assigned TCP flows and complete leak sinks, then rerun the full verification commands on final source. Status stays In Progress.

### Owner resolution: 2026-10-16 (after session 24)

Not an external blocker: the remaining work is inside this plan's own scope and paths.
Everything else on the checkpoint is green (`mise run ci` 1397 passed / 35 skipped,
ddl-check, web-ci, uv build; process/leak tests pass; the skipped legacy tests are
rewritten). Finish exactly these, using the existing TCP process harness in
`tests/inventory_service/test_process.py` and `tests/split`:
1. P08-R1R8-MATRIX: (a) cross-process producer delivery and replay (Telescreen
   outbox -> Inventory and -> Notification; stop the receiver, write, restart, assert
   exactly-once application via receipts); (b) Inventory publishes an overdue
   notification and serves `/api/data/*` while the Telescreen process is stopped;
   (c) cutover delivery/dedup: copied pending obligations deliver once after cutover.
2. P08-LEAK-MATRIX: scan every table of all three stores, captured wire bodies
   (outbox/inbox payloads), process logs and audit exports for the raw samples.
Keep each test file under 1000 lines (split by topic). Then rerun the full
verification list on final source, record hashes, mark Completed. Conventions 7a:
only final passing commands, `priorVerification` empty, no fixed findings as risks.

### Deliverables 1-7 as planned in runs 1-7

Moved unchanged from the plan's Deliverables section in the run 8 planning commit.
Deliverables 2-7 were done in run 7. Run 8 replaces deliverable 1 with the plan's
"Run 8 Scope".

1. **`tests/split/` multi-process suite.** `support.py` starts `telescreen serve` (API
   plus an engine loop, or `telescreen worker` as a second process if that is the existing
   pattern), `telescreen-inventory serve` and `telescreen-notification serve`.
   - Each process gets free ports and its own env: three database URLs, tokens via `env:`
     refs, OIDC test issuer settings from `tests.support.oidc`, and fingerprint/identity
     keys.
   - Notification uses a fake SMTP server: a minimal line-based SMTP recorder thread in
     `support.py`, because the stdlib `smtpd` was removed in Python 3.12 and no
     dependency may be added. Point `TELESCREEN_SMTP_HOST`/`PORT` at it and set
     `TELESCREEN_SMTP_STARTTLS=false`.
   - `SlackNotifier.api_base` has no setting and none may be added. Verify Slack delivery
     in `tests/split/test_slack_inprocess.py`: build `build_notification_runtime(...,
     http=httpx.AsyncClient(transport=MockTransport))`, apply an overdue command for a
     team, run one delivery-pump tick, and assert a masked Slack post.
   - Every process is terminated in `finally`. Logs go to `/tmp/telescreen-svc-p08-*`.
   - Migrate each store with the CLIs first.
   - Mark the module `@pytest.mark.slow` only if that marker already exists; otherwise
     leave it unmarked.

   Test cases (`test_split_flows.py`):

   - **Auth per service.** The same OIDC bearer is accepted by all three services after
     `projections resync`. A revoked API token is rejected by Inventory and Notification
     after the relay runs.
   - **Collection -> inventory.** Push ingest gives an asset through the proxy.
   - **Issue link push.** Asset detail open issues follow an issue status change.
   - **Directory replication.** The display name changes in an Inventory actor display.
   - **Overdue.** With Telescreen **stopped**, inventory overdue publication reaches
     Notification and the fake SMTP/Slack records a masked message (R1/R5).
   - **Producers.** Issue mention, workflow notify and alert notifications produce
     deterministic ids on Notification rows.
   - **Settings and templates through the Telescreen proxy.** Same status codes and
     permissions as single process for viewer and responder.
   - **Outage and replay.**
     - Notification stopped while producers commit; restart -> the commands are applied
       once.
     - Inventory stopped while links queue; restart -> they are applied in order.
     - Telescreen stopped -> Inventory and Notification keep serving.
   - **DLP parity, multi-process.** With `TELESCREEN_DLP_POLICY=block`, an alert whose
     issue title carries a sample secret -> the Notification row is `blocked` and no fake
     delivery happens. The single-process equivalent is also `blocked`.
2. **Rewrite `tests/inventory_service/test_process.py` and `test_postgres.py`** for full
   split mode (Notification required); they must be green.
   - At `a5392d9` both files are owner placeholders. `test_process.py` has a module-level
     `pytest.mark.skip`, and `test_postgres.py` calls `pytest.skip(...)` at import time.
     Remove both skips. `test_process.py` must then run and pass offline, so record its
     passed count. `test_postgres.py` keeps only the `integration` marker plus the
     `TELESCREEN_TEST_DATABASE_URL` skip.
   - The cutover below needs `telescreen notification-store`, which svc-p06 routes to
     `cli_inventory_store.run_notification` in run 7. If it still exits 2, write a
     Reconciliation request.
   - `tests/e2e/test_inventory_split_runtime.py` is a single-process separate-store test.
     It is not owned here and must stay green; run it in verification.
   - The single-process inventory cutover e2e (`tests/e2e/test_inventory_store_cutover.py`)
     is owned by svc-p02 (inventory transfer v2, manifest g2). Run it here; do not edit it.
   - Add `tests/split/test_cutover.py` for the full-split cutover in design 13 order:
     1. a shared database with a pending queued notification, a retention receipt and a
        pending retention obligation;
     2. `migrate`, then `notification-store copy`/`verify`, then inventory copy;
     3. start the services, run `projections resync`;
     4. assert that the queued notification is delivered once and that the obligation with
        a copied receipt is a `duplicate` (no second send).
3. **Restricted logins (R6, F5).** Extend `tests/e2e/test_inventory_runtime_grants.py`
   (or add `tests/split/test_store_grants.py`) with these PostgreSQL non-owner roles:
   - **single process:** the main role is granted DML on `main_database_tables()` (the
     main owner tables plus the four retained notification tables the single-process main
     database hosts), and the inventory role on `tables_for(INVENTORY)`. The
     single-process API, worker, retention and notification flows work;
   - **full split:** three roles, each granted DML only on `tables_for(store)`.

   For each role, assert:
   - each role denies cross-store SELECT/INSERT;
   - each role denies `alembic_version_*` and trigger DDL;
   - the API, inventory and notification flows work with their role.

   Marked `integration`.
4. **Leak matrix.** `tests/leak/flows_split.py` and `test_leak_matrix_split.py`. Embed
   runtime-assembled sample secrets in an issue title, workflow inline text, a comment and
   an asset locator. Run the split flows, then scan:
   - every row of `notification_outbox`, `inventory_notification_outbox`,
     `projection_changes`, `inventory_directory_entries`, `notification_directory_entries`,
     `inventory_issue_links`, `notification_inbox`, both new audit tables, the
     `inventory_outbox` payloads and `notifications`;
   - captured HTTP request/response bodies (`httpx` event hooks or the fake servers);
   - process logs;
   - audit exports.

   No raw sample may appear. Only masked forms and hit counts.
5. **Import gates.** `tests/split/test_import_gates.py` uses isolated subprocesses:
   - `python -m telescreen.inventory_service --help` and `build_inventory_runtime` import
     none of `telescreen.wiring`, `telescreen.issues`, `telescreen.collectors`,
     `telescreen.workflow`, `telescreen.alerts`, `telescreen.directory` or
     `telescreen.notifications`;
   - the same checks for `telescreen.notification_service` (plus `telescreen.inventory`);
   - the Police SDK imports no `telescreen.`.
6. **Compose (R8).** Add `examples/compose/docker-compose.services.yml`: three
   `postgres` services, three migrate one-shots (`telescreen migrate --store main`,
   `telescreen-inventory migrate`, `telescreen-notification migrate`), `inventory`,
   `notification`, and an override of the Telescreen app/worker environment.
   - Only the Telescreen app publishes `ports:`.
   - Secrets use `secrets:` files and `env:`/`file:` refs, with placeholders only.
   - SMTP/Slack settings appear only on `notification`.
   - Delete `docker-compose.inventory.yml`. Update `.env.example` (new password
     placeholders) and `examples/compose/README.md`.
   - `tests/examples/test_examples.py` parses the YAML and asserts the three databases and
     three processes, that only Telescreen has `ports`, that no SMTP/Slack variables
     appear outside `notification`, and that no literal secrets appear.
7. **Docs.**
   - `design-docs/specs/design-service-split-verification.md`: a requirement-by-requirement
     evidence table with commands and results.
   - `design-service-split.md`:
     - status IMPLEMENTED;
     - 4.1 the `api_token` payload includes `label` (needed by the token actor display);
     - 15 Telescreen split mode allows `notifications.dlp`/`notifications.guard` (GitHub
       outbound guard);
     - 12 (O1, including its observable single-process table layout, and the run 7
       Nodetto layout rule) and 17 are already corrected (sessions 20-24, manifest g7).
       Change them only if the implemented ownership or migration behavior differs. The
       Nodetto layout fix itself belongs to serial finalization, not to this plan;
     - 13 runbook commands as implemented.
   - `command.md`: "TELESCREEN_BUREAU_SERVICE_URL is no longer read". Add the final
     command list.
   - `design-inventory-microservice.md`: compose file name.
