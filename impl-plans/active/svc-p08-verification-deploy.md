# SVC-P08 Verification and Deployment: Multi-Process Tests, Restricted Logins, Leak Matrix, Compose, Docs

**Status**: In Progress (run 8: R8.1-R8.5 below are the only remaining work)
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

Deliverables 1-7 as planned in runs 1-7 are archived in full in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md).
Deliverables 2-7 are done. Run 8 replaces deliverable 1 with the "Run 8 Scope" section below.

## Key Pitfalls

- Never leave orphan processes. Use `try/finally` with terminate, then wait, then kill.
- Tests run offline and pick free ports. There are no fixed sleeps over 30 s; poll with a
  deadline.
- The PostgreSQL tests skip without `TELESCREEN_TEST_DATABASE_URL`. That is expected and
  not a risk.
- Sample secrets come only from `tests.support.samples`. gitleaks blocks literals.

## Run 8 Scope (manifest g8, base aaa2c4c) - authoritative

Deliverables 2-7 are done (see the log archive). Run 8 does only the owner-resolution
work (design section 17 "Run 8 acceptance flows" and "Leak matrix"). Deliverable 1 cases
outside that list (OIDC parity, DLP parity, settings proxy, collection) stay covered by
the existing component and in-process tests (`tests/notification_service/test_dlp_parity.py`,
the p07 replica-authenticator parity tests, `tests/e2e/test_wiring_split.py`). Run 8 adds
no new process cases for them.

### R8.1 Shared harness `tests/split/support.py` (new, under 400 lines)

- Move `_port`, `_run`, `_start` and `_ready` from `tests/inventory_service/test_process.py`
  into public helpers: `free_port() -> int`, `run_cli(command, values) -> str`,
  `start_service(command, values, log) -> subprocess.Popen[bytes]`,
  `wait_ready(http, url, process, service_token)`, and `stop_service(process)`
  (terminate, then wait 10 s, then kill and wait 5 s). `test_process.py` imports them, and
  its existing test keeps passing.
- `start_service` must open the log in **append** mode (`"ab"`). Today `_start` uses `"wb"`,
  which loses the pre-restart log on restart, and the leak scan needs both halves.
- `ServiceStack` (dataclass): the three SQLite paths, four ports, per-service env tuples
  (copy the shapes at test_process.py:94-134), the log paths and the live processes, plus
  `start(name)`, `stop(name)`, `restart(name, extra_env)` and `stop_all()`. Every test
  calls `stop_all()` in `finally`. The Inventory env adds `TELESCREEN_WORKER_POLL_SECONDS=0.1`
  and `TELESCREEN_DATA_OVERDUE_CRON` (`""` = disabled, `"* * * * *"` = enabled).
- `SmtpRecorder`: a stdlib `socketserver.ThreadingTCPServer` on `127.0.0.1:0` that speaks
  just enough SMTP (220 greeting; EHLO/HELO; MAIL; RCPT; DATA ending at `.`; QUIT) and
  stores each DATA text in a lock-guarded list. Notification env:
  `TELESCREEN_SMTP_HOST=127.0.0.1`, `TELESCREEN_SMTP_PORT=<port>`,
  `TELESCREEN_SMTP_FROM=telescreen@example.invalid`, `TELESCREEN_SMTP_STARTTLS=false`
  (keys from `core/config.py:354-359`; read `notifications/email.py` for anything else the
  sender needs). Shut it down in `finally`. Add no dependency.
- Read-only probes: `sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)`. Add
  `fetch_rows(path, table) -> tuple[tuple[str, ...], ...]` (every value via `str`; bytes
  decoded with `errors="replace"`) and `scalar_int(path, sql, params) -> int`. Never write
  to a store whose owning process is running.
- `wait_for(check, timeout, what)`: poll every 0.2 s up to `timeout` (at most 90 s), then
  raise `AssertionError(what)`. No fixed sleep longer than 1 s.
- Seeding over HTTP through the Telescreen API with the CLI admin token:
  `POST /api/members` (`handle`, `display_name`, `email` as `<handle>@example.invalid`,
  `slack_user_id: null`; see `api/routers/directory.py:226`), then `telescreen projections
  resync`, then `wait_for` until the member id is present in both
  `inventory_directory_entries` and `notification_directory_entries`. Create the issue via
  `POST /api/ingest/v1/scan` (as at test_process.py:217-245), then read its id from
  `GET /api/issues`.

### R8.2 `tests/split/test_replay_process.py` (R1R8 a)

- `test_notification_outage_replays_once`: start the stack and the SMTP recorder, seed member
  `alice` and an issue, then stop Notification. Post `POST /api/issues/{id}/comments` with
  a body mentioning `@alice` (producer `issues/activity/mentions.py`). `wait_for` a
  `notification_outbox` row that is still undelivered and whose delivery engine task has
  attempt >= 1 (it failed while Notification was down). Restart Notification on the same
  port, then `wait_for` every `notification_outbox` row to be delivered. Assert:
  - each outbox event id has exactly one `notification_inbox` row;
  - exactly one `notifications` row for alice has the mention event kind;
  - re-POSTing that outbox row's stored payload to `NOTIFICATION_PREFIX + "/commands"`
    (`notification_service/app.py:292`) with `x-telescreen-service-token` set to the
    Telescreen notification token (the producer is chosen by token, app.py:295-302)
    returns `"duplicate"`, and the inbox and notification row counts do not change.
- `test_inventory_outage_replays_once`: seed as above, then stop Inventory and change the
  issue status (`PATCH /api/issues/{id}`, using a valid status from `core.vocabulary`).
  `wait_for` a pending `IssueLinkState` row in `inventory_outbox` with a failed delivery
  attempt. Restart Inventory, then `wait_for` delivery. Assert that `inventory_issue_links`
  holds exactly one row for the issue, with the new status and the highest published
  stream sequence, and that each delivered event id has exactly one `inventory_inbox` row.
- Pitfalls. Command delivery retries back off: notification `2**attempt` s, inventory
  `10*2**(attempt-1)` s (`workflow/queue/worker.py:113`). Restart the receiver as soon as
  the first failure is visible, so the replay arrives within the 90 s deadline. Do not
  call the proxy (`/api/data/*`, notification settings) while the receiver is stopped.

### R8.3 `tests/split/test_stopped_telescreen_process.py` (R1R8 b)

- Start the stack with the overdue cron disabled. Seed `alice`. Through the Telescreen
  proxy, create the `deletion_due` (DATE, role `deletion_due`) and `owner` (REFERENCE, role
  `owner`) fields, then an asset whose `deletion_due` is `(date.today() - 7 days)` (the
  processes use the real clock) and whose `owner` is `member:<alice id>` (imitate
  `tests/inventory_service/test_runtime.py:_seed_overdue_asset`).
- Stop the Telescreen API and worker, and assert `poll()` is not None for both. Restart
  Inventory with cron `"* * * * *"`: `publish_due` fires on its first tick, but only once
  per cron minute, so the cron must not be enabled before the asset exists.
- Assert, with no Telescreen process alive:
  - direct `GET http://127.0.0.1:<inventory>/api/data/assets` and `/api/data/assets/{id}`,
    with `Authorization: Bearer <admin token>` and `x-telescreen-service-token`, return 200
    and include the asset;
  - the `inventory_notification_outbox` row is delivered;
  - Notification has one `notifications` row of event kind `data_overdue` for alice with
    status sent;
  - the SMTP recorder holds exactly one message to `alice@example.invalid`;
  - `inventory_audit_records` contains `data_asset.overdue_notified`.

### R8.4 `tests/split/test_cutover_process.py` (R1R8 c; keep `test_cutover.py` unchanged)

The flow is inventory-only split to full split (runbook section 13), so the inventory
store is already separate:

1. `telescreen migrate` on the shared `main.db` (single process; no notification URL);
   `telescreen-inventory migrate` on `inventory.db`.
2. Writers are stopped. Seed with the test's own async engine:
   - `main.db` gets a `MemberRow` alice with a fixed id, a `pending` `NotificationRow` for
     `member:<alice id>` (shape from `test_cutover.py`), and a
     `RetentionNotificationReceiptRow` with id `X = retention_event_id(asset_id, field_id,
     revision_id)` (`ports/retention.py:32`);
   - `inventory.db` gets the `deletion_due` and `owner` fields, the asset and its values,
     with fixed ids, the past date and owner alice (imitate
     `tests/inventory/retention/helpers.py:_field` and `_asset`).
3. `telescreen-notification migrate`, then `telescreen notification-store copy
   --writers-stopped` and `verify --writers-stopped`; both exit 0.
4. Start Notification (with SMTP), Inventory (cron disabled), and the Telescreen API and
   worker in split mode. Run resync and wait for the replicas.
5. Assert that the copied row is delivered exactly once: its status is sent, and the
   recorder holds exactly one message with its subject. That count is unchanged after
   5 further pump intervals.
6. Restart Inventory with the cron enabled. `wait_for` the `inventory_notification_outbox`
   row `X` to be delivered. Notification answered `DUPLICATE` through the receipt check at
   `notification_service/inbox.py:62-74`. Assert:
   - no `notification_inbox` row `X`;
   - the `data_overdue` notification count is unchanged;
   - the SMTP count is unchanged.
7. Not vacuous: assert that row `X` exists in the outbox, which proves the command was
   actually sent.

### R8.5 Leak matrix: `tests/leak/flows_split.py` and `test_leak_matrix_split.py`

- Add `assert_store_tables_clean(samples, stores: tuple[tuple[Store, Path], ...])`:
  - for each `(Store.MAIN, main.db)`, `(Store.INVENTORY, inventory.db)` and
    `(Store.NOTIFICATION, notification.db)` pair, take the names from
    `db/ownership.py:tables_for(store)`, which is never a hardcoded list;
  - assert that every name exists in `sqlite_master`, so a new table that the flow cannot
    reach still fails;
  - scan every value of every row for `sample.value` and its JSON-escaped form
    (`json.dumps(value)[1:-1]`).
- Add `assert_texts_clean(samples, texts)`, using the same two forms, for logs, API bodies,
  SMTP DATA and audit exports. Keep the existing `assert_split_sinks_clean` raw-byte scan.
- `test_leak_matrix_split.py`: one stack, distinct runtime samples from
  `tests.support.samples`, carried by:
  - the ingest text, which becomes the issue title;
  - a comment mentioning `@alice`;
  - a workflow notify step with an inline body (imitate
    `tests/leak/flows_v12.py:notification_set_flow`; create, activate and run it on the
    issue);
  - the rejected asset locator (422).
- After every outbox is delivered:
  - run `python -m telescreen.inventory_service audit export` and
    `python -m telescreen.notification_service audit export` via `run_cli`; each output
    must be non-empty;
  - scan all tables, the four process logs, every API body the test received, the SMTP
    DATA and both exports.
- Not vacuous:
  - at least one `notification_outbox` payload, decoded with the
    `telescreen.ports.notification_commands` codec, has an operation whose `redaction`
    (`ProducerRedactionHits`) count is positive;
  - the mention notification row exists.

### Invariants and non-goals for run 8

- No production code, DDL, Alembic or owner-baseline edit. Run `git diff --quiet HEAD --
  src db` at the end; it must exit 0. A product defect is a Reconciliation request,
  reproduced with a failing test id.
- Do not edit `design-service-split.md` status lines, `impl-plans/README.md`, `README.md`
  or any manifest. Serial finalization does that.
- Every file under `tests/split` and `tests/leak` stays under 1000 lines, and this plan
  stays under 400. No `pytest.skip` or `skipif` in the new process modules. Typelint rules
  apply (no `Any`, `object` or dict types; dataclasses for records).
- Update `design-docs/specs/design-service-split-verification.md`: map rows R1, R3, R5
  and R7 to the new test ids and final logs, and remove the "pending" wording only where
  a new test now covers it.

## Verification (run 8; logs `/tmp/telescreen-svc-p08-verification-deploy-g8-*.log`)

Use `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1` for every command. Each must exit 0:

1. `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
2. `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
3. `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
4. `uv run --frozen python tools/typelint.py > <log> 2>&1`, with no line matching `tests/(split|leak|inventory_service)`.
5. `for i in 1 2 3; do uv run --frozen pytest -p no:cacheprovider -q tests/split/test_replay_process.py tests/split/test_stopped_telescreen_process.py tests/split/test_cutover_process.py tests/leak/test_leak_matrix_split.py tests/inventory_service/test_process.py || exit 1; done`. Each run passes with 0 skipped; this checks for flakiness.
6. `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'`, which proves there are no orphan processes.
7. `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e`
8. `mise run ci`. Record the passed and skipped counts; passed must be at least 1397 plus the new tests.
9. `mise run ddl-check`; `mise run web-ci`; `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g8-dist`.
10. `git diff --quiet HEAD -- src db`; `test "$(wc -l < impl-plans/active/svc-p08-verification-deploy.md)" -lt 400`; `wc -l tests/split/*.py tests/leak/*.py`, where every file is under 1000 lines.

Record source hashes under `tmp/telescreen-svc-20261016-g8/svc-p08-verification-deploy/attempt-<n>/`.

## Completion Criteria

- [ ] R8.1-R8.5 are implemented, and command 5 passes three times in a row offline.
- [ ] The leak matrix enumerates `tables_for` for all three stores and scans logs, API bodies, SMTP DATA and both audit exports.
- [ ] Verification commands 1-10 exit 0 on the final source, with logs and hashes recorded.
- [ ] The verification document has been updated. Status is Completed, `priorVerification` is empty, and only final passing commands are listed (conventions 7a).
- [x] Compose topology, the rewritten `test_process.py`/`test_postgres.py`, the docs and the design corrections are done (run 7).

## Progress Log

The run 7 entries (sessions 7 and 8 and the owner resolution) are archived in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md).
Append run 8 sessions below, following conventions section 8.

### Run 8 implementation session 1 (2026-10-16)

- Added `tests/split/support.py` with free-port, CLI, append-log process, readiness,
  shutdown, SQLite read-only probes, bounded polling, `ServiceStack`, and threaded SMTP
  recorder. `tests/inventory_service/test_process.py` now imports the shared process helpers.
- Added the Inventory-while-Telescreen-stopped case. Its current-source process run reached
  the member projection wait and failed after 90 s. Main `inventory_outbox` contained
  directory stream sequences 2-5 (API token, ownership rules, member, identity), while the
  Inventory inbox cursor remained at 3; the member and identity rows were absent. The HTTP
  receiver returned 200, but the corresponding delivery tasks remained retryable with
  `last_error_code='conflict'`. This blocks the rest of the flow before overdue delivery.
- **Reconciliation request:** diagnose and fix the Inventory directory stream delivery gap
  in the owning production relay/delivery code, outside svc-p08 writePaths. Reproduce with
  `tests/split/test_stopped_telescreen_process.py::test_inventory_serves_and_delivers_with_telescreen_stopped`.
  Resume svc-p08 after that test's member and identity projections reach Inventory and
  Notification. Do not change production files in this plan.
- Extended `flows_split.py` to scan every `tables_for(Store)` row for raw and JSON-escaped
  samples and added a missing-table assertion. The prior process/leak focused run passes:
  `/tmp/p08-leak-helper-test.log` (3 passed).
- Failed exploratory process logs (kept because the test was corrected between attempts):
  `/tmp/p08-stopped-process-attempt1.log` (422 invalid field value),
  `/tmp/p08-stopped-process-attempt2.log` (same), and
  `/tmp/p08-stopped-process-attempt3.log` (member projection absent after 30 s).
  The final-source reproduction is `/tmp/p08-stopped-process-attempt4.log` (member
  projection absent after 90 s). Focused lint/type gates passed for current split/leak
  files: format `/tmp/telescreen-svc-p08-verification-deploy-g8-format.log`, Ruff
  `/tmp/telescreen-svc-p08-verification-deploy-g8-ruff.log`, ty
  `/tmp/telescreen-svc-p08-verification-deploy-g8-ty.log`, typelint
  `/tmp/telescreen-svc-p08-verification-deploy-g8-typelint.log`. The ownership-table
  behavioral run is `/tmp/p08-leak-helper-test.log` (3 passed). `pgrep` found no surviving
  service process, `git diff --quiet HEAD -- src db` exited 0, and the plan and all
  split/leak Python files are below their line limits. Source hashes and complete logs
  are under `tmp/telescreen-svc-20261016-g8/svc-p08-verification-deploy/attempt-1/`.
  Full required verification is deferred until reconciliation.

### Owner resolution 2: 2026-10-16 (after session 25)

The requested production fix is not needed. The owner reproduced the run with kept
databases: directory stream deliveries run concurrently, so sequence 5 can reach
Inventory before 4; the inbox correctly answers GAP (recorded as `conflict`) and the
task retries 5 seconds later (logs show applied retries at :44, :49, :54). Delivery is
driven by the Telescreen worker, so a test must wait for the producer outboxes to drain
(the replica waits do this) before stopping Telescreen. The other failure was the test
calling Inventory right after `stack.restart("inventory")`; the owner added a
`wait_ready` on the Inventory health endpoint there.
`tests/split/test_stopped_telescreen_process.py` now passes (1 passed, 78 s): Inventory
serves `/api/data/*` and delivers an overdue notification through Notification to SMTP
while Telescreen's API and worker are stopped. Remaining scope: the other two
P08-R1R8-MATRIX flows (cross-process delivery/replay with a stopped receiver;
cutover delivery/dedup) and the complete P08-LEAK-MATRIX, then the full verification
list. Always wait for readiness after (re)starting a process and for outboxes to
drain before stopping a producer. Conventions 7a.
