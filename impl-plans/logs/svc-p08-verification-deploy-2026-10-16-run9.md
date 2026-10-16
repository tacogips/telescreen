# svc-p08-verification-deploy archive (2026-10-16, runs 8-9)

These sections were moved unchanged from `impl-plans/active/svc-p08-verification-deploy.md`
in the run 10 planning commit (manifest g10), to keep the plan under 400 lines: the
"Run 9 Scope" section (R9.1-R9.5; R9.1, R9.2 and R9.5 are done, R9.3 and R9.4 are
corrected by owner resolution 3 and restated as the plan's "Run 10 Scope") and the run 8
and run 9 Progress Log entries. Owner resolution 3 stays in the plan.

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

### Run 9 implementation session 1 (2026-10-16)

- Added `seed_member`, `seed_issue`, and public `ServiceStack.main_runtime()` to
  `tests/split/support.py`. Added Notification and Inventory outage/replay cases in
  `tests/split/test_replay_process.py`, the process cutover case in
  `tests/split/test_cutover_process.py`, and replaced the old leak-test wrapper with a
  three-store `ServiceStack` flow in `tests/leak/test_leak_matrix_split.py`.
- Updated `design-docs/specs/design-service-split-verification.md` to map R1/R3/R5/R7 to
  the run 9 tests and evidence. No production, DDL, or migration files changed.
- `tests/split/test_replay_process.py` passed on the final test source (2 passed, 0
  skipped) in `/tmp/telescreen-svc-p08-verification-deploy-g9-replay-final-source.log`.
  It checks the member id in the command, the handle-based `member:alice` notification
  row, one inbox receipt, duplicate delivery, and the Inventory stream sequence. The
  earlier incorrect outbox payload offset remains documented in the failed rerun log.
- `tests/leak/test_leak_matrix_split.py::test_split_leak_matrix_fails_when_owned_table_is_missing`
  passed. The process matrix ran through sink scanning, then failed the required positive
  `ProducerRedactionHits` assertion: API workflow inline text is redacted before the
  producer creates its command, and all observed counts are zero. Raw inline source text
  is rejected by the workflow source scanner. Reconciliation request: identify a safe,
  in-scope process carrier that yields positive producer redaction metadata without
  persisting raw or escaped sample content. Evidence:
  `/tmp/telescreen-svc-p08-verification-deploy-g9-replay-leak-rerun.log`.
- `tests/split/test_cutover_process.py::test_notification_cutover_process_delivers_once_and_deduplicates_retention`
  failed its required delivery of the copied pending notification. The Notification
  delivery pump schedules only `queued` rows, while the specified cutover seed and copy
  preserve `status='pending'`. The test also confirms that member notification references
  are handle based (`member:alice`), while the requested seed shape used `member:<alice
  id>`. Reconciliation request: define or implement the approved migration mapping and
  recipient reference semantics in the owning production plan; svc-p08 cannot edit those
  paths. Evidence: `/tmp/telescreen-svc-p08-verification-deploy-g9-focused-initial.log`.
- The required three-consecutive process suite and final full verification list remain
  incomplete because the cutover and leak-matrix non-vacuity gates fail. The first combined
  run reported 4 failed and 1 passed in 521.11 s; the corrected replay/leak rerun reported
  2 failed and 2 passed in 395.86 s. These are retained as failed run evidence, not passing
  verification. The corrected Inventory replay passed individually (1 passed in 201.56 s).
- Final-source reruns: replay passed 2 tests in 284.40 s;
  `test_cutover_process.py` plus `test_leak_matrix_split.py` reported 2 failed and 1 passed
  in 226.27 s, reproducing the copied-pending delivery and zero-redaction-carrier blockers.
  The completed process check found no surviving Inventory or Notification service
  process. `git diff --quiet HEAD -- src db`, the no-skip scan, and plan/Python line bounds
  exited 0. Final source and verification log hashes are recorded in
  `tmp/telescreen-svc-20261016-g9/svc-p08-verification-deploy/attempt-1/source-and-verification.sha256`.
- Final scoped format, Ruff, `ty`, and typelint commands all exited 0; logs are
  `/tmp/telescreen-svc-p08-verification-deploy-g9-{format,ruff,ty,typelint}.log`.
  The no-service-process check, source/DDL diff check, skip-marker check and line limits
  also exited 0. The required 3x process gate, scoped/full pytest, `mise run ci`, DDL,
  web CI and build gates are not certified: cutover and leak matrix still fail, and
  `artifactRoots` is empty for the plan's build-output commands. Current status remains
  In Progress; this implementation does not claim acceptance or workflow finalization.

