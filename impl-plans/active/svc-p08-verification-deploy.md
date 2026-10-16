# SVC-P08 Verification and Deployment: Multi-Process Tests, Restricted Logins, Leak Matrix, Compose, Docs

**Status**: In Progress (run 9: R9.1-R9.4 below are the only remaining work)
**Design Reference**: design-docs/specs/design-service-split.md sections 13-17; design-docs/specs/design-service-split-requirements.md section 7
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p08-verification-deploy`
- planPath: `impl-plans/active/svc-p08-verification-deploy.md`
- wave: D (manifest `telescreen-svc-20261016-g9`, the only dispatched wave)
- dependsOn: none open; svc-p01 to svc-p07 are accepted dependencies at `f2c6707`
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
- the leak matrix over every store table, outbox, wire body, log and audit export;
- the compose overlay, runbook docs and design corrections.

Imitate:

- `tests/split/support.py` (`ServiceStack`, `SmtpRecorder`, `run_cli`, `wait_ready`,
  `wait_for`, `scalar_int`, `fetch_rows`) and
  `tests/split/test_stopped_telescreen_process.py` (seeding alice, waiting for both
  replicas, restarting a service and waiting for readiness);
- `tests/inventory_service/test_process.py:run_three_processes_with_leak_probe`
  (collector creation plus `POST /api/ingest/v1/scan`, the 422 locator probe);
- `tests/split/test_cutover.py` (row shapes for the cutover seed);
- `tests/leak/flows_v12.py:notification_set_flow` (workflow notify step, activate, run).

## Non-goals

- No production code, DDL, Alembic or owner-baseline change. If a test exposes a product
  defect, write a "Reconciliation request" in the Progress Log with a failing test id and
  stop that task. Do not edit `src/**` or `db/**`.
- No live-service tests in verification. The owner runs `examples/compose/dev-services.yml`
  checks.
- No new process cases for OIDC parity, DLP parity, the settings proxy or collection.
  Component and in-process tests cover them (`tests/notification_service/test_dlp_parity.py`,
  the p07 replica-authenticator parity tests, `tests/e2e/test_wiring_split.py`).
- Do not archive plans or edit `impl-plans/README.md`, `README.md`, any manifest or the
  design status line. Serial finalization does that.
- Do not change `tests/split/test_stopped_telescreen_process.py` (it passes; R8.3) or
  `tests/split/test_cutover.py` (in-process cutover; keep unchanged).

## Deliverables

Deliverables 1-7 of runs 1-7 are archived in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md).
Deliverables 2-7 are done. Run 8 finished R8.1 (`tests/split/support.py`), R8.3
(`tests/split/test_stopped_telescreen_process.py`, passes) and the R8.5 helpers in
`tests/leak/flows_split.py` (`assert_store_tables_clean`, `assert_texts_clean`). Run 9
does only the "Run 9 Scope" below.

## Key Pitfalls

- Never leave orphan processes. Every test wraps the stack in `try/finally` with
  `stack.stop_all()` and `smtp.close()`.
- Owner resolution 2 test rules (design section 17):
  - after every `stack.start`/`stack.restart`, `await wait_ready(...)` on that service's
    health URL before any HTTP call to it;
  - before stopping a producer, wait until its outboxes are drained (the replica waits
    do this for Telescreen, whose worker drives delivery);
  - concurrent directory-stream deliveries can arrive out of order. The inbox answers
    GAP (engine task `last_error_code='conflict'`) and the task retries about 5 s later.
    This is expected. Never assert on `conflict` and never treat it as a defect.
- Command delivery retries back off: notification `2**attempt` s, inventory
  `10*2**(attempt-1)` s (`workflow/queue/worker.py:113`). Restart the stopped receiver as
  soon as the first failed attempt is visible, so the replay arrives within the 90 s
  `wait_for` deadline.
- Do not call the proxy (`/api/data/*`, notification settings) while its receiver is
  stopped.
- The ingest text must contain a detectable runtime sample from `tests.support.samples`,
  or no issue is created.
- A mention of a member absent from Notification's replica is `DEFERRED`. Wait for both
  replicas before posting a comment.
- The overdue cron fires once per cron minute. Enable it only by restarting Inventory
  after the asset and the replicas exist.
- Sample secrets come only from `tests.support.samples`. gitleaks blocks literals.
- Never write to a SQLite store whose owning process is running. Use the read-only
  probes in `support.py`.

## Run 9 Scope (manifest g9, base f2c6707) - authoritative

### R9.1 Seeding helpers in `tests/split/support.py` (stays under 1000 lines)

Add only what R9.2-R9.4 share:

- `async def seed_member(stack, http, handle: str) -> str`: `POST /api/members` (shape as in
  `test_stopped_telescreen_process.py:29-40`), then `run_cli(("telescreen", "projections",
  "resync"), ...)` with the main runtime env, then `wait_for` (90 s) until the member id
  is present in both `inventory_directory_entries` and `notification_directory_entries`.
  Returns the member id.
- `async def seed_issue(stack, http, text: str) -> str`: create a `custom` collector and
  `POST /api/ingest/v1/scan` (as in `run_three_processes_with_leak_probe`), then read the
  new issue id from `GET /api/issues`. Returns the issue id.
- Both seed helpers take a `bodies: list[str]` parameter and append every response
  `.text` they receive, so the R9.4 leak test can scan them.
- A public `main_runtime()` accessor (or reuse of `_main_runtime`) so tests stop
  duplicating the service URL/token env.
- Do not change the behavior of existing helpers. `test_stopped_telescreen_process.py`
  and `test_process.py` must keep passing unchanged.

### R9.2 `tests/split/test_replay_process.py` (R1R8 a)

- `test_notification_outage_replays_once`:
  1. Start the stack and `SmtpRecorder`; `seed_member(..., "alice")`; `seed_issue(...)`.
  2. `stack.stop("notification")`. `POST /api/issues/{id}/comments` with a body that
     mentions `@alice` (producer `issues/activity/mentions.py`).
  3. `wait_for` a `notification_outbox` row with `delivered_at IS NULL` and an
     `engine_tasks` row of kind `notification_command_delivery` with `attempts >= 1`.
  4. `stack.restart("notification")`, `wait_ready` on
     `/internal/notification/v1/health`, then `wait_for` every `notification_outbox` row
     to have `delivered_at IS NOT NULL`.
  5. Assert: every outbox `id` has exactly one `notification_inbox` row with the same id;
     exactly one `notifications` row with `recipient_ref = 'member:<alice id>'` and the
     mention `event_kind` (read the kind from the mentions producer, do not guess).
  6. Re-POST one outbox row's stored `payload_json` to
     `http://127.0.0.1:<notification>/internal/notification/v1/commands`
     (`notification_service/app.py:292`) with `x-telescreen-service-token:
     notification-process-token`. Expect 200 and receipt `duplicate`; the inbox and
     notification row counts are unchanged.
- `test_inventory_outage_replays_once`:
  1. Start the stack; `seed_member`; `seed_issue`; `wait_for` an `inventory_issue_links`
     row for the issue (initial link delivered), so the producer outbox is drained.
  2. `stack.stop("inventory")`. `PATCH /api/issues/{id}` with a valid new status from
     `core.vocabulary` (read `IssuePatchBody` in `api/routers/issues_actions.py:186`).
  3. `wait_for` an undelivered `inventory_outbox` row whose `payload_json` names the
     issue-link operation (`ports/inventory_commands.py:IssueLinkState`) and an
     `engine_tasks` row of kind `inventory_delivery` with `attempts >= 1`.
  4. `stack.restart("inventory")`, `wait_ready` on `/internal/inventory/v1/health`, then
     `wait_for` that outbox row to be delivered.
  5. Assert: `inventory_issue_links` has exactly one row for the issue, with the new
     status. For the row's `stream_id`, `inventory_inbox_streams.last_sequence` in
     `inventory.db` equals the highest `inventory_outbox.sequence` of that stream in
     `main.db`. Each delivered outbox id has exactly one `inventory_inbox` row.

### R9.3 `tests/split/test_cutover_process.py` (R1R8 c)

The flow is inventory-only split to full split (design section 13), so the inventory
store is already separate:

1. `telescreen migrate --store main` on `main.db` and `telescreen.inventory_service
   migrate` on `inventory.db`, with no notification URL configured.
2. Writers are stopped. Seed with the test's own async engine (then dispose it):
   - `main.db`: `MemberRow` alice with a fixed id, a `pending` `NotificationRow` for
     `member:<alice id>` with a unique subject (shape from `test_cutover.py`), and a
     `RetentionNotificationReceiptRow` with id `X = retention_event_id(asset_id,
     field_id, revision_id)` (`ports/retention.py:32`);
   - `inventory.db`: the `deletion_due` and `owner` fields, the asset and its values with
     fixed ids, a past due date and owner alice (imitate
     `tests/inventory/retention/helpers.py:_field` and `_asset`). The revision id used
     for `X` must be the asset's current due-date revision.
3. `telescreen.notification_service migrate`, then `telescreen notification-store copy
   --writers-stopped` and `telescreen notification-store verify --writers-stopped` via
   `run_cli`. Both exit 0.
4. Start Notification (with SMTP), Inventory (cron disabled), and the Telescreen API and
   worker in split mode, waiting for readiness of each. Run resync and wait for the
   replicas.
5. Assert the copied row is delivered exactly once: its status becomes sent, and the
   recorder holds exactly one message with its subject. Re-check after 5 more seconds:
   still one.
6. `stack.restart("inventory", (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),))`,
   `wait_ready`, then `wait_for` (90 s) the `inventory_notification_outbox` row `X` to be
   delivered. Notification answered `DUPLICATE` through the receipt check at
   `notification_service/inbox.py:62-74`. Assert: no `notification_inbox` row `X`; the
   `data_overdue` notification count equals its value before step 6; the SMTP count is
   unchanged.
7. Not vacuous: row `X` exists in `inventory_notification_outbox` and is delivered.

If `ServiceStack.start_all` cannot be reused because it migrates every store itself,
add a narrow `start_processes(http)` helper in `support.py` that starts and waits
without migrating. Do not duplicate the env shapes.

### R9.4 Leak matrix: rewrite `tests/leak/test_leak_matrix_split.py`

- Replace the call to `run_three_processes_with_leak_probe` with one `ServiceStack` flow.
  Keep `test_split_leak_matrix_fails_when_owned_table_is_missing` unchanged.
- Distinct runtime samples from `tests.support.samples` travel through:
  - the ingest text, which becomes the issue title (`seed_issue`);
  - a comment mentioning `@alice` plus a second sample;
  - a workflow notify step with an inline body containing a sample (imitate
    `flows_v12.py:notification_set_flow`; create, activate and run it on the issue);
  - the rejected asset locator (expect 422);
  - an asset context field value if the API accepts one; otherwise omit this carrier.
- Append every HTTP response body the test receives to a local `list[str]`.
- After every outbox is drained (`notification_outbox`, `inventory_outbox`,
  `projection_changes` relay, `inventory_notification_outbox` where present):
  - run `("telescreen.inventory_service", "audit", "export")` and
    `("telescreen.notification_service", "audit", "export")` via `run_cli` with each
    service's env. Each output is non-empty;
  - `assert_store_tables_clean(samples, ((Store.MAIN, main), (Store.INVENTORY, inv),
    (Store.NOTIFICATION, notif)))`;
  - `assert_split_sinks_clean(samples, db_paths, log_paths, bodies)` with the four
    `stack.log_dir` logs;
  - `assert_texts_clean(samples, smtp.messages + (inventory_export, notification_export))`.
- Not vacuous:
  - at least one `notification_outbox.payload_json`, decoded with
    `telescreen.ports.notification_commands.decode_notification_command`, has an
    operation whose `ProducerRedactionHits` count is positive;
  - the mention notification row for alice exists;
  - the main-store `audit_records` table has rows. Telescreen has no audit export
    command; its chain is covered by the table scan (design section 17).

### R9.5 Documentation

Update `design-docs/specs/design-service-split-verification.md`: map rows R1, R3, R5 and
R7 to the new test ids and final logs, and remove "pending" wording only where a new test
now covers it. Change the "continuation run 8" wording to run 9.

## Invariants

- `git diff --quiet HEAD -- src db` exits 0 at the end.
- Every file under `tests/split` and `tests/leak` stays under 1000 lines; this plan stays
  under 400.
- No `pytest.skip`, `skipif` or `pytest.mark.skip` in the new process modules.
- Typelint rules apply: no `Any`, `object`, dict types, `**kwargs` or type-ignore;
  dataclasses for records. JSON decoding of `payload_json` goes through the existing
  `telescreen.ports` codecs, not raw `json.loads` into dicts.
- Per-edit discipline: read each file fresh before editing; record pre/post SHA-256 and
  an intent snapshot under the evidence root; on unexpected drift write a Reconciliation
  request and do not overwrite.

## Verification (run 9; logs `/tmp/telescreen-svc-p08-verification-deploy-g9-*.log`)

Use `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1` for every command. Each must exit 0:

1. `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
2. `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
3. `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
4. `uv run --frozen python tools/typelint.py > <log> 2>&1`, with no line matching `tests/(split|leak|inventory_service)`.
5. `for i in 1 2 3; do uv run --frozen pytest -p no:cacheprovider -q tests/split/test_replay_process.py tests/split/test_stopped_telescreen_process.py tests/split/test_cutover_process.py tests/leak/test_leak_matrix_split.py tests/inventory_service/test_process.py > <log-$i> 2>&1 || exit 1; done`. Each run passes with 0 skipped; this checks for flakiness.
6. `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'` (no orphans).
7. `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e`
8. `mise run ci`. Record the passed and skipped counts; passed must be at least 1397 plus the new tests.
9. `mise run ddl-check`; `mise run web-ci`; `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g9-dist`.
10. `git diff --quiet HEAD -- src db`; `test "$(wc -l < impl-plans/active/svc-p08-verification-deploy.md)" -lt 400`; every `tests/split/*.py` and `tests/leak/*.py` is under 1000 lines.

Record source hashes under `tmp/telescreen-svc-20261016-g9/svc-p08-verification-deploy/attempt-<n>/`.

## Completion Criteria

- [ ] R9.1-R9.4 are implemented, and command 5 passes three times in a row offline.
- [ ] The leak matrix enumerates `tables_for` for all three stores and scans logs, API bodies, SMTP DATA and both audit exports, with the non-vacuous checks.
- [ ] Verification commands 1-10 exit 0 on the final source, with logs and hashes recorded.
- [ ] R9.5 is done. Status is Completed, `priorVerification` is empty, and only final passing commands are listed (conventions 7a).
- [x] Compose topology, the rewritten `test_process.py`/`test_postgres.py`, the docs and the design corrections are done (run 7).
- [x] Shared harness `tests/split/support.py` and the stopped-Telescreen flow are done (run 8).

## Progress Log

The run 7 entries are archived in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md).
Append run 9 sessions below, following conventions section 8.

### Run 8 implementation session 1 (2026-10-16)

- Added `tests/split/support.py` (free port, CLI, append-log process, readiness, shutdown,
  read-only SQLite probes, bounded polling, `ServiceStack`, threaded SMTP recorder);
  `tests/inventory_service/test_process.py` imports the shared helpers.
- Added the Inventory-while-Telescreen-stopped case and extended `flows_split.py` with the
  `tables_for` scan, the missing-table assertion and `assert_texts_clean`.
- The stopped-Telescreen case failed waiting for the member projection and a
  Reconciliation request was filed; owner resolution 2 below closed it without a
  production change.

### Owner resolution 2: 2026-10-16 (after session 25)

The requested production fix is not needed. Directory stream deliveries run
concurrently, so sequence 5 can reach Inventory before 4; the inbox correctly answers GAP
(recorded as `conflict`) and the task retries 5 seconds later. Delivery is driven by the
Telescreen worker, so a test must wait for the producer outboxes to drain (the replica
waits do this) before stopping Telescreen. The test also called Inventory right after
`stack.restart("inventory")`; the owner added a `wait_ready` there.
`tests/split/test_stopped_telescreen_process.py` now passes (1 passed, 78 s). Remaining
scope: the replay and cutover flows and the complete leak matrix, then the full
verification list (now Run 9 Scope).

### Run 9 planning (2026-10-16)

Manifest g9 supersedes g8. Metadata wave/dependsOn corrected (review comm-001093 low
finding). Run 8 Scope R8.2, R8.4 and R8.5 carried into R9.2-R9.4 with pinned facts:
`engine_tasks.attempts` (not `attempt`), task kinds `notification_command_delivery` and
`inventory_delivery`, `inventory_issue_links` has no sequence column (compare
`inventory_inbox_streams.last_sequence` with the producer `inventory_outbox.sequence`),
and issues need a collector plus a detectable ingest sample.
