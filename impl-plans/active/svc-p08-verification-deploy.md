# SVC-P08 Verification and Deployment: Multi-Process Tests, Restricted Logins, Leak Matrix, Compose, Docs

**Status**: In Progress (run 10: R10.1-R10.4 below are the only remaining work)
**Design Reference**: design-docs/specs/design-service-split.md sections 6, 13-17; design-docs/specs/design-service-split-requirements.md section 7
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p08-verification-deploy`
- planPath: `impl-plans/active/svc-p08-verification-deploy.md`
- wave: E (manifest `telescreen-svc-20261016-g10`, the only dispatched wave)
- dependsOn: none open; svc-p01 to svc-p07 are accepted dependencies at `3190903`
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
- artifactRoots: none (build outputs go to `/tmp`)

## Intent and Context

This plan proves R1-R8 end to end and ships the deployment artifacts: three uvicorn
processes over TCP on three SQLite files, restricted-login grants, the leak matrix, the
compose overlay and the runbook docs. Runs 1-9 delivered everything except two test-design
errors found by owner resolution 3 (Progress Log below):

1. `tests/split/test_cutover_process.py` seeds a `pending` notification (there is no such
   `NotificationStatus`) with `member:<id>` (senders store the handle-based `member:alice`).
2. `tests/leak/test_leak_matrix_split.py` requires a positive `ProducerRedactionHits`
   count. Zero is correct, because content is redacted before a producer builds its
   command.

Run 10 fixes both tests, updates the verification doc and runs the full gate list. The
replay flow (`tests/split/test_replay_process.py`, flow a) and the stopped-Telescreen
flow (`tests/split/test_stopped_telescreen_process.py`, flow b) already pass and must stay
unchanged and green.

Imitate:

- `tests/split/support.py` (`ServiceStack`, `SmtpRecorder`, `run_cli`, `wait_ready`,
  `wait_for`, `scalar_int`, `seed_member`, `seed_issue`);
- `tests/split/test_stopped_telescreen_process.py` (wait for both replicas, restart a
  service, then `wait_ready`);
- `tests/split/test_cutover.py` (in-process cutover row shapes, `queued` status).

## Non-goals

- No production code, DDL, Alembic or owner-baseline change. Do not edit `src/**` or
  `db/**`. A product defect is a "Reconciliation request" in the Progress Log with a
  failing test id, the log path and the observed row state.
- No live-service tests in verification (owner-run `examples/compose/dev-services.yml`).
- No new process cases (OIDC parity, DLP parity, settings proxy, collection).
- Do not change `tests/split/test_replay_process.py`,
  `tests/split/test_stopped_telescreen_process.py`, `tests/split/test_cutover.py` or
  `tests/leak/flows_split.py`, unless a reproduced defect in them blocks a gate.
- Do not archive plans, write manifests or edit `impl-plans/README.md`, `README.md`,
  `impl-plans/logs/**` or the design status line. Serial finalization does that.

## Deliverables

Deliverables of runs 1-9 are archived in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md)
(runs 1-7) and
[`../logs/svc-p08-verification-deploy-2026-10-16-run9.md`](../logs/svc-p08-verification-deploy-2026-10-16-run9.md)
(Run 9 Scope and the run 8-9 Progress Log). Run 10 delivers only R10.1-R10.4.

## Key Pitfalls

- Never leave orphan processes: `try/finally` with `stack.stop_all()` and `smtp.close()`.
- After every `stack.start`/`stack.restart`, `await wait_ready(...)` on that service's
  health URL before any HTTP call to it. Before stopping a producer, wait until its
  outboxes are drained.
- Directory-stream deliveries can arrive out of order. The inbox answers GAP (engine task
  `last_error_code='conflict'`) and the task retries about 5 s later. This is expected:
  never assert on `conflict` and never treat it as a defect.
- Never write to a SQLite store while its owning process runs. Seed only while every
  writer is stopped; read through the read-only probes in `support.py`.
- Notification's `DeliveryPump` (`notification_service/worker.py`) starts with the serve
  process and polls every second. `NotificationDeliveryHandler.handle`
  (`notifications/delivery.py:55-67`) marks a row `failed` with `recipient_unknown`,
  terminally, when `ReplicaRecipientResolver` (`replica/directory.py:52-61`) cannot find
  the member in Notification's directory replica. A copied `queued` row for
  `member:alice` therefore fails if the pump sees it before resync has applied alice.
  See R10.1 step 4.
- Sample secrets come only from `tests.support.samples` (gitleaks blocks literals).
- Typelint: no `Any`, `object`, dict types, `**kwargs` or type-ignore.

## Run 10 Scope (manifest g10, base 3190903) - authoritative

### R10.1 Cutover seed in `tests/split/test_cutover_process.py` (flow c)

Keep the existing structure (migrate main and inventory, seed with stopped writers,
`notification-store copy/verify --writers-stopped`, start, resync, assert, restart
Inventory with `TELESCREEN_DATA_OVERDUE_CRON="* * * * *"`, assert dedup). Change:

1. In the `NotificationRow` seed, use `status=NotificationStatus.QUEUED.value` (import from
   `telescreen.core.vocabulary`) and `recipient_ref="member:alice"`. Rename the id and
   subject from "pending" to "queued", keeping the id at 36 characters or fewer. Update the
   docstring and failure messages to match.
2. Keep `event_id = retention_event_id(asset_id, due_field_id, due_revision_id)`, where
   `due_revision_id` is the `revision_id` of the `deletion_due` `DataFieldValueRow`. This is
   what `inventory/retention.py:173` computes, so the receipt id matches the obligation.
   Do not change this alignment.
3. After `notification-store copy`, assert through `scalar_int` on the notification store
   that `retention_notification_receipts` holds id `event_id` (count 1) and that the
   notifications row holds `status='queued'` (count 1). Both are carriers that make the
   later assertions non-vacuous.
4. Delivery ordering. First run the runbook order as written (design section 13, step 5:
   start Notification, then resync). If the copied row ends `failed` with `error_code
   'recipient_unknown'` (print status, error_code and the
   `notification_directory_entries` count in the failure message), the pump raced the
   replica. This is the reproduced defect. Record it in the Progress Log (test id, log
   path, row state), then apply the replica-first cutover order. The order needs no
   production change, because `notification-store copy` replaces only `notifications`,
   `notification_templates`, `notification_settings` and
   `retention_notification_receipts` (`db/notification_transfer.py:22-27`) and keeps
   `notification_directory_entries`:
   1. Seed main and inventory with every writer stopped.
   2. Run `telescreen.notification_service migrate`, start Notification, Inventory and the
      Telescreen worker (and the API if `seed`/resync needs it), `wait_ready` each, run
      `telescreen projections resync`, and `wait_for` alice in both
      `inventory_directory_entries` and `notification_directory_entries`. The Notification
      store has no queued rows yet, so nothing races.
   3. Wait until `projection_changes` is empty, then `stack.stop_all()`. All writers are
      now stopped.
   4. `notification-store copy --writers-stopped` and `verify --writers-stopped`. Start
      the stack again and `wait_ready` every process.
   Then update design-service-split.md section 13 (steps 4-6: seed the Notification
   replica by resync before the stopped-writer copy, and say why) and the section 17
   flow (c) text, plus the cutover runbook in `design-docs/specs/command.md` if it
   lists the steps. Do not edit any other design section.
5. Exactly once: the row reaches `status='sent'`, and `smtp.messages` holds exactly one
   message containing the subject. After `asyncio.sleep(5)` there is still exactly one,
   and the row is still `sent`.
6. Dedup (unchanged): after the Inventory restart, `inventory_notification_outbox` row
   `event_id` is delivered, `notification_inbox` has no row `event_id`, the
   `data_overdue` notification count equals its value before the restart, and the SMTP
   count is unchanged. Notification answered `DUPLICATE` at
   `notification_service/inbox.py:62-74`.

Tests (input -> expected):

- copied `queued` member row plus split start -> exactly one SMTP message and row `sent`;
- copied receipt `X` plus overdue cron -> outbox row `X` delivered, no inbox row `X`, no
  new `data_overdue` row.

### R10.2 Leak matrix non-vacuity in `tests/leak/test_leak_matrix_split.py`

1. Delete the `any(isinstance(operation, MemberSendOperation) ... > 0 ...)` assertion, the
   `_notification_outboxes` helper and the imports only it used
   (`MemberSendOperation`, `decode_notification_command`). Keep every sink scan unchanged:
   `assert_store_tables_clean` over `tables_for` of all three stores,
   `assert_split_sinks_clean` (stores, four logs, API bodies), and `assert_texts_clean`
   (SMTP DATA plus both audit exports). Keep
   `test_split_leak_matrix_fails_when_owned_table_is_missing` unchanged.
2. Add non-vacuity assertions after the scans:
   - the ingest carrying `samples[0]` returned 200 (already asserted in
     `support.py:seed_issue`; do not weaken that assertion);
   - `findings` has at least one row for `issue_id` with a non-empty `masked_snippet`
     (`SELECT count(*) FROM findings WHERE issue_id=? AND masked_snippet <> ''` >= 1). The
     table scan already proves the raw and JSON-escaped sample appear nowhere, so this
     row holds only the masked form;
   - the scan job that produced it kept a positive count:
     `SELECT max(s.findings_count) FROM scan_jobs s JOIN findings f ON f.scan_job_id =
     s.id WHERE f.issue_id=?` > 0 (`issues/service.py:288` sets `scan_job_id`);
   - keep the mention notification row for alice (`ISSUE_MENTIONED`), and keep
     `audit_records` > 0 in main.
3. Do not require positive `ProducerRedactionHits` or `dlp_hit_count`. The comment and
   workflow carriers are redacted upstream, so zero is correct for them.
4. If `findings_count` is 0 while findings exist, that is a production counting defect:
   write a Reconciliation request. Do not delete the assertion.

Tests (input -> expected):

- runtime samples through ingest, comment, workflow notify, rejected locator and context
  field -> no raw or escaped sample in any sink; findings row with masked snippet;
  `findings_count` > 0;
- empty SQLite store -> `AssertionError` "store is missing declared table" (unchanged).

### R10.3 Verification doc

In `design-docs/specs/design-service-split-verification.md`: change "continuation run 9"
to run 10. In rows R3, R5 and R7, replace the failure and Reconciliation wording with the
final passing test ids and the g10 log paths. If R10.1 step 4 applied the replica-first
order, say so in row R5. Add the 3x process gate result.

### R10.4 Final verification

Run the Verification list below on the final source, in order, in the foreground. Record
exit codes, passed/skipped counts and full log paths in the Progress Log.

## Invariants

- `git diff --quiet HEAD -- src db` exits 0 at the end.
- Every file under `tests/split` and `tests/leak` stays under 1000 lines; this plan stays
  under 400.
- No `pytest.skip`, `skipif` or `pytest.mark.skip` in the process modules.
- Raw samples never enter any table, log, outbox, replica, wire body, notification or
  export (the leak matrix proves it).
- Per-edit discipline: read each file fresh before editing. Record pre/post SHA-256 and
  an intent snapshot under `tmp/telescreen-svc-20261016-g10/svc-p08-verification-deploy/attempt-<n>/`.
  On unexpected drift, write a Reconciliation request and do not overwrite.

## Verification (run 10; logs `/tmp/telescreen-svc-p08-verification-deploy-g10-*.log`)

Use `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1` for every command. Each must exit 0:

1. `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
2. `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
3. `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples`
4. `uv run --frozen python tools/typelint.py > <log> 2>&1`, with no line matching `tests/(split|leak|inventory_service)`.
5. `for i in 1 2 3; do uv run --frozen pytest -p no:cacheprovider -q tests/split/test_replay_process.py tests/split/test_stopped_telescreen_process.py tests/split/test_cutover_process.py tests/leak/test_leak_matrix_split.py tests/inventory_service/test_process.py > <log-$i> 2>&1 || exit 1; done`. Each of the three runs passes with 0 skipped (flakiness gate).
6. `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'` (no orphans).
7. `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e`
8. `mise run ci`. Passed must be at least 1397 (baseline at `3190903`).
9. `mise run ddl-check`; `mise run web-ci`; `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g10-dist`.
10. `git diff --quiet HEAD -- src db`; `test "$(wc -l < impl-plans/active/svc-p08-verification-deploy.md)" -lt 400`; every `tests/split/*.py` and `tests/leak/*.py` is under 1000 lines.

## Completion Criteria

- [ ] R10.1: the copied `queued` `member:alice` row is delivered exactly once after cutover, and the copied receipt deduplicates the retention obligation.
- [ ] R10.2: the leak matrix proves non-vacuity by ingest 200, a masked-only finding and `findings_count` > 0, and no longer requires producer redaction hits.
- [ ] R10.3: the verification doc is updated (and design section 13/17 too, if R10.1 step 4 applied).
- [ ] Verification commands 1-10 exit 0 on the final source, with logs and hashes recorded. Status is Completed, `priorVerification` is empty, and only final passing commands are listed (conventions 7a).
- [x] Compose topology, the rewritten `test_process.py`/`test_postgres.py`, docs and design corrections (run 7); shared harness and stopped-Telescreen flow (run 8); seed helpers and replay flow (run 9).

## Progress Log

Earlier entries are archived in the two log files named under Deliverables. Append run 10
sessions below, following conventions section 8.

### Owner resolution 3: 2026-10-16 (after session 26)

Neither remaining failure needs a production change; both are test-design errors.

1. Cutover: `NotificationStatus` has no `pending` value (queued, sent, failed, blocked,
   skipped). Unsent rows are `queued` in the monolith, and the Notification worker
   (`notification_service/worker.py`) schedules copied `queued` rows. Seed the copied
   pending notification with `status='queued'`, and use the real handle-based member
   reference (`member:alice`), which is what senders store. Then assert it is delivered
   once after cutover and that the copied `retention_notification_receipts` deduplicate
   the retention obligation.
2. Leak matrix non-vacuity: a zero `ProducerRedactionHits` count is correct, because
   content is redacted before the producer builds its command. Do not require positive
   producer hits. Prove non-vacuity instead by showing the raw sample entered the system
   and was handled: the ingest/scan request with the runtime sample returns 200, a
   finding/issue exists with only the masked representation, and (where a path keeps
   counts) detector or DLP hit counts are positive. Then scan every table of the three
   stores, wire bodies, process logs and audit exports for the raw and JSON-escaped
   sample, as already implemented.

Then run the 3x process suite and the full verification list on final source.
For build-output commands, write to /tmp paths (no artifactRoots needed) or list them
as artifactRoots in the plan. Conventions 7a.

### Run 10 planning (2026-10-16)

Manifest g10 supersedes g9 with the same writePaths. The Run 9 Scope and the run 8-9
entries moved to the `-run9` log. Owner resolution 3 is restated as R10.1 and R10.2. A
code reading found one hazard to settle by reproduction (R10.1 step 4): Notification's
pump fails a copied member row with `recipient_unknown` if it runs before resync seeds
the replica (`notifications/delivery.py:55-67`, `replica/directory.py:52-61`). Planning
could not execute pytest in its sandbox, so the hazard is unverified. The fallback is a
test and runbook ordering change, not a production change.
