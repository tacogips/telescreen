# svc-p08-verification-deploy archive (2026-10-16, run 10)

These sections were moved unchanged from `impl-plans/active/svc-p08-verification-deploy.md`
in the run 11 planning commit (manifest g11), to keep the plan under 400 lines: the
"Run 10 Scope" section (R10.1-R10.4, all done at `66531bd`) and the owner resolution 3,
run 10 planning and run 10 implementation Progress Log entries. Owner resolution 4 stays
in the plan. The `/tmp` log paths below are run 10 evidence; run 11 re-runs the gates
with `g11` log paths.

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

## Progress Log (run 10)

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

### Run 10 implementation and final verification (2026-10-16)

R10.1 corrected the process fixture to use `NotificationStatus.QUEUED`, `member:alice`,
and `DlpVerdict.PASS`. The first corrected run with the runbook's original Notification-
before-resync order reproduced the documented race: Notification marked the row
`failed/recipient_unknown` while `notification_directory_entries` was empty; resync later
populated two entries. The process test now starts the empty Notification store, resyncs
both service replicas, waits for `projection_changes` to drain, stops all writers, copies
and verifies notification state, then restarts processes. It asserts the copied receipt
and queued row, one SMTP message, `sent` status after 5 seconds, and retention dedup with
no inbox row or additional `data_overdue` notification. The test reports status,
`error_code` and directory count on delivery timeout. The reproduced race is recorded in
`/tmp/telescreen-svc-p08-verification-deploy-g10-cutover-attempt2.log`; the fixed focused
cutover pass is recorded in
`/tmp/telescreen-svc-p08-verification-deploy-g10-cutover-attempt3.log` (1 passed, 0 skipped).

R10.2 removed the producer-redaction operation inspection and replaced it with a finding
for the ingested issue having a non-empty `masked_snippet` and a joined positive
`scan_jobs.findings_count`. All raw and JSON-escaped sink scans, mention notification,
audit assertions and missing-table failure check remain. Focused leak run:
`/tmp/telescreen-svc-p08-verification-deploy-g10-leak-attempt1.log` (2 passed, 0 skipped).
The first cutover attempt at
`/tmp/telescreen-svc-p08-verification-deploy-g10-cutover-attempt1.log` exposed an invalid
fixture verdict (`clear`) as a worker `ValueError`; the fixture was corrected to
`DlpVerdict.PASS` without a production change. The first targeted Ruff check also found
one E501 line; it was corrected before final checks. These resolved attempts are recorded
here, not in final verification or `priorVerification`.

Per-file SHA-256 before/after edits: `tests/split/test_cutover_process.py`
`7b627fdcc29580205a394122e9a16514cd0d539ac485ed6d66436864ef8e1944` ->
`0af905c571c2b65dc2cea5b6dbf3ae45d12e0d31a79e91f8045f3010ffb4bcc0`;
`tests/leak/test_leak_matrix_split.py`
`17ea9649b6d3802c67bca8b471e399cbeeeaa935a07476ce617909a98d526420` ->
`f75fe88922dbeeac35aec42eedac33887b5416a75e02bf3238c4c0a1357fdf6c`;
`design-docs/specs/design-service-split.md`
`6434354e2d8dac221dd255c004e3fcb81c1a603efd05e08424b24bbd52afb3ac` ->
`8fb18e874a27170a6526ca213064b01045e5141c33bcd2bfad99808475306dc8`;
`design-docs/specs/design-service-split-verification.md`
`ad85a66a3ddc4132be1d7907ca4d94278e9855ea7e35750b0a22d942fff02309` ->
`e2da80e79034d4f3a28b0fd80cca0e55bef25a55ec2d934732739ccad8740725`;
`design-docs/specs/command.md`
`76b54480643dd777086b64b5acb14889558bdfbfb8f2eac5d775690c49da9a12` ->
`73107a8aa0c5887e39456001dd3091e2501a0a102d4e6d34bea70d3abb860f84`.
The hashes and per-edit intents are also preserved under
`tmp/telescreen-svc-20261016-g10/svc-p08-verification-deploy/attempt-1/`.

Final verification (all exit 0; full logs under `/tmp`):

- `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — 72 files; `/tmp/telescreen-svc-p08-verification-deploy-g10-format.log`.
- `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — `/tmp/telescreen-svc-p08-verification-deploy-g10-ruff.log`.
- `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` — `/tmp/telescreen-svc-p08-verification-deploy-g10-ty.log`.
- `uv run --frozen python tools/typelint.py` with the required path filter — no diagnostics; `/tmp/telescreen-svc-p08-verification-deploy-g10-typelint.log`.
- Three sequential focused process runs: each 7 passed, 0 skipped; `/tmp/telescreen-svc-p08-verification-deploy-g10-process-1.log`, `...-process-2.log`, `...-process-3.log` (561.54s, 562.17s, 562.23s).
- `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'` — no service process survived; `/tmp/telescreen-svc-p08-verification-deploy-g10-pgrep-final.log`.
- `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e` — 180 passed, 4 skipped; `/tmp/telescreen-svc-p08-verification-deploy-g10-pytest.log`.
- `mise run ci` — 1402 passed, 35 skipped, 1 warning; `/tmp/telescreen-svc-p08-verification-deploy-g10-ci.log`.
- `mise run ddl-check` — Nodetto exports canonical, 10 tests passed; `/tmp/telescreen-svc-p08-verification-deploy-g10-ddl.log`.
- `mise run web-ci` — typecheck, 262 tests and build passed; `/tmp/telescreen-svc-p08-verification-deploy-g10-web.log`.
- `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g10-dist` — wheel and sdist built; `/tmp/telescreen-svc-p08-verification-deploy-g10-build.log`.
- `git diff --quiet HEAD -- src db`, forbidden assertion/skip greps, line budgets and final pgrep — all exit 0; logs `/tmp/telescreen-svc-p08-verification-deploy-g10-source-diff.log`, `/tmp/telescreen-svc-p08-verification-deploy-g10-leak-proxy.log`, `/tmp/telescreen-svc-p08-verification-deploy-g10-status.log`, `/tmp/telescreen-svc-p08-verification-deploy-g10-skips.log`, `/tmp/telescreen-svc-p08-verification-deploy-g10-line-limits.log`, `/tmp/telescreen-svc-p08-verification-deploy-g10-pgrep-final.log`.

The owner-run PostgreSQL restricted-login and live Mailpit/Keycloak checks remain outside
this offline gate. Archiving plans/manifests, README/index/status finalization, formal
review, exact-file staging, commit and push are owned by later workflow steps.
