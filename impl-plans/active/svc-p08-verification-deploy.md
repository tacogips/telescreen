# SVC-P08 Verification and Deployment: Multi-Process Tests, Restricted Logins, Leak Matrix, Compose, Docs

**Status**: Not Started
**Design Reference**: design-docs/specs/design-service-split.md sections 13-17; design-docs/specs/design-service-split-requirements.md section 7
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p08-verification-deploy`
- planPath: `impl-plans/active/svc-p08-verification-deploy.md`
- wave: 5
- dependsOn: `svc-p04-inventory`, `svc-p05-notification-service`, `svc-p06-composition`
- writePaths:
  - `impl-plans/active/svc-p08-verification-deploy.md`
  - `tests/split`
  - `tests/inventory_service/test_process.py`
  - `tests/inventory_service/test_postgres.py`
  - `tests/e2e/test_inventory_runtime_grants.py`
  - `tests/leak`
  - `tests/examples/test_examples.py`
  - `examples/compose/docker-compose.services.yml`
  - `examples/compose/docker-compose.inventory.yml`
  - `examples/compose/.env.example`
  - `examples/compose/README.md`
  - `design-docs/specs/design-service-split.md`
  - `design-docs/specs/design-service-split-verification.md`
  - `design-docs/specs/command.md`
  - `design-docs/specs/design-inventory-microservice.md`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

This plan proves R1-R8 end to end and ships the deployment artifacts:

- three real uvicorn processes over TCP against three SQLite files, with PostgreSQL
  variants marked `integration`;
- restricted-login PostgreSQL grants for three stores;
- the leak matrix over replicas, outboxes and wire bodies;
- the compose overlay, runbook docs and design corrections.

Imitate:

- `tests/inventory_service/test_process.py` (`_port`, `_start`, `_ready`, subprocess
  lifecycle with guaranteed termination);
- `tests/e2e/test_inventory_runtime_grants.py` (restricted roles);
- `tests/leak/flows_v14.py` and `sinks.py` (leak harness);
- `examples/compose/docker-compose.inventory.yml` (overlay shape).

## Non-goals

- No production code changes. If a test exposes a defect in another plan's file, write a
  "Reconciliation request" in the Progress Log and stop that task. Do not edit the file.
- No live-service tests in verification. The owner runs `examples/compose/dev-services.yml`
  checks.
- Do not archive plans or edit `impl-plans/README.md` or `README.md`. Archiving is serial
  finalization after the fanout.

## Deliverables

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

## Key Pitfalls

- Never leave orphan processes. Use `try/finally` with terminate, then wait, then kill.
- Tests run offline and pick free ports. There are no fixed sleeps over 30 s; poll with a
  deadline.
- The PostgreSQL tests skip without `TELESCREEN_TEST_DATABASE_URL`. That is expected and
  not a risk.
- Sample secrets come only from `tests.support.samples`. gitleaks blocks literals.

## Verification

```bash
uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples
uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples
uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples
uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e tests/e2e/test_inventory_split_runtime.py 2>&1 | tee /tmp/telescreen-svc-p08-verification-deploy-pytest.log
mise run ci > /tmp/telescreen-svc-p08-verification-deploy-ci.log 2>&1; echo "exit=$?"
mise run ddl-check > /tmp/telescreen-svc-p08-verification-deploy-ddl.log 2>&1; echo "exit=$?"
mise run web-ci > /tmp/telescreen-svc-p08-verification-deploy-web.log 2>&1; echo "exit=$?"
uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-dist > /tmp/telescreen-svc-p08-verification-deploy-build.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p08-verification-deploy-typelint.log 2>&1; echo "exit=$?"
```

If `mise run ci` runs `uv build` into the repository, record that and use the `--out-dir`
command as the build evidence. Evidence: every command exits 0, and the full suite (via
`mise run ci`) is green offline.

## Completion Criteria

- [ ] All R1-R8 acceptance flows in `tests/split` pass. The leak matrix passes. The import
      gates pass.
- [ ] `mise run ci`, `ddl-check`, `web-ci` and `uv build --out-dir` exit 0. Logs are recorded.
- [ ] Compose overlay test passes. `docker-compose.inventory.yml` is removed.
- [ ] `grep -n "pytest.mark.skip\|pytest.skip(" tests/inventory_service/test_process.py` prints
      nothing. The `test_postgres.py` module-level `pytest.skip(` placeholder is gone.
- [ ] The verification doc and design corrections are written. The Progress Log has hashes.

## Progress Log

(append sessions per conventions section 8)
