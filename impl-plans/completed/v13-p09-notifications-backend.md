# V13-P09 Notifications Backend: Per-User Settings, Member Delivery, Tester, Outbound Guard, Weekly Report

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D22-D24), 2, 10, 12 (tests), 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p09-notifications-backend`
- planPath: `impl-plans/active/v13-p09-notifications-backend.md`
- wave: 2
- dependsOn: `v13-p01-foundation`, `v13-p02-db`
- writePaths:
  - `impl-plans/active/v13-p09-notifications-backend.md`
  - `src/telescreen/notifications/sender.py`
  - `src/telescreen/notifications/repository.py`
  - `src/telescreen/notifications/member_send.py`
  - `src/telescreen/notifications/preferences.py`
  - `src/telescreen/notifications/tester.py`
  - `src/telescreen/notifications/guard.py`
  - `src/telescreen/notifications/weekly.py`
  - `src/telescreen/api/routers/notification_settings.py`
  - `tests/notifications/v13`
  - `tests/notifications/test_notifications.py`
  - `tests/notifications/v12`
  - `tests/e2e/test_flow_v12_notifications.py`
  - `tests/e2e/test_wiring_v12.py`
  - `tests/leak/flows_v12.py`
  - `tests/leak/test_leak_matrix_v12.py`
  - `tests/leak/sinks.py`
- sharedPaths:
  - `src/telescreen/notifications/templates/builtins.py`
  - `src/telescreen/notifications/templates/variables.py`
  - `src/telescreen/notifications/templates/synthetic.py`
- sharedPathNotes:
  - `src/telescreen/notifications/templates/builtins.py`: created and extended by v13-p01
    (wave 1). Edit only to fix a v1.3 built-in found wrong by this plan's tests.
  - `src/telescreen/notifications/templates/variables.py`: from v13-p01. Same rule.
  - `src/telescreen/notifications/templates/synthetic.py`: from v13-p01. Same rule.
- artifactRoots: none
- Shared-test ownership (run-1 manifest e2; design section 15): the existing tests listed
  after `tests/notifications/v13` read `NotificationRow` or build `NotificationService`.
  Uncategorized v1.2 requests must stay byte-for-byte unchanged, so they are expected to
  pass unchanged. Edit one only for an assertion that a v1.3 design fact breaks (for
  example a row now carrying `category`), and record the reason in the Progress Log.
  Never weaken a leak or DLP assertion. `tests/notifications/v12/test_template_validation.py`
  already expects 30 variables (owner fix at `d8073b1`); keep it at 30 unless this plan
  adds a v1.3 variable path. `notification_settings.py` is not registered until v13-p12,
  so the route walk does not change. Read-only: `tests/leak/harness.py` and every
  route-walk test.

Read `impl-plans/conventions.md` first.

## Intent and Context

Personal notification control and new delivery helpers:

- per-member settings per category;
- `MemberNotifier.send_to_member`, which picks the channel from the settings;
- a member filter for alert and workflow-task notifications;
- `NotificationTester`, used for the integration test buttons;
- `OutboundContentGuard`, which exposes the DLP gate for GitHub create;
- the weekly report (cron provider plus task handler).

Imitate:

- `notifications/sender.py:NotificationService.send` (one transaction: render, gate,
  row, audit, enqueue);
- `issues/maintenance.py:ExceptionMaintenanceProvider.tick` (cron fire with `croniter`,
  idempotency key) and `issues/digest.py:OwnerDigestHandler` (a `TaskHandler`);
- `cli_v12.py:notify_test` (queue, then deliver inline with
  `NotificationDeliveryHandler`). Reimplement it in the package; do not import `cli_v12`.

## Non-goals

- No change to `NotificationSender.send` semantics for requests where `category` is None
  and the event is not `human_task_assigned` or `owner_review_assigned`. Those keep the
  v1.2 behavior byte for byte.
- `sla_escalated` is never filtered.
- No wiring (v13-p12). No issue queries: the weekly report reads through the
  `IssueReportSource` port.

## Invariants

- Default channel for the direct categories (assigned, mentions, subscribed,
  weekly_report) when no row exists: `email` if the member has an email, else `slack` if
  the member has a `slack_user_id`, else `off`.
- For the filter categories (alerts, and workflow_tasks, which `human_task_assigned` and
  `owner_review_assigned` imply), no row means "as configured".
- An opted-out member gets a `skipped` row with `error_code = recipient_opted_out`, and the
  audit `notification.skipped`. There is no delivery task.
- Every v1.3 row stores `category`.
- Rendered text still passes `Redactor`, then the DLP gate.
- The weekly report is enqueued at most once per fire (idempotency key
  `weekly:<fire_minute>`). Each member's send is independent; one member's failure is
  recorded and does not abort the others.

## Deliverables

1. **`notifications/preferences.py`**: `PreferenceRepository` and `PreferenceService`:
   - `settings(session, member_id) -> tuple[CategorySetting(category, stored:
     NotificationPreference | None, effective: NotificationPreference), ...]`;
   - `update(session, member_id, items)`, where a null `stored` deletes the row;
   - `channel_for(session, member_id, category) -> NotificationPreference`.

   Read `members.email` and `members.slack_user_id` through the ORM, read-only.
2. **`notifications/sender.py`**:
   - `send()`: when `category` is ALERTS, or the event implies WORKFLOW_TASKS, resolve
     each `member:` recipient's member id. With a stored `off`, or a stored channel
     different from `request.channel`, that member gets a skipped row; other recipients
     are unchanged.
   - New `send_to_member(session, MemberNotification) -> NotificationReceipt`, which
     implements the `MemberNotifier` port: choose the channel, then call `send()` with
     `recipients=("member:<handle>",)`, `event`, `context`, `category` and
     `record_unreachable=True`.
   - Keep `sender.py` under 1000 lines; move helpers to `notifications/member_send.py` if
     needed.
3. **`notifications/tester.py`**: `NotificationServiceTester(sessions, service, delivery:
   NotificationDeliveryHandler, members)` implements `NotificationTester.send_test(channel,
   member_id)`. It queues one `INTEGRATION_TEST` notification to the member (context
   `health_target="telescreen"`), then delivers inline. `TestOutcome.ok` is true only when
   the row ends `sent`; otherwise it carries the row's `error_code`.
4. **`notifications/guard.py`**: `DlpOutboundGuard(gate: DlpGate)` implements
   `OutboundContentGuard.inspect(text)` through the gate's existing inspection with a
   synthetic `OutboundMessage`. The verdict is allowed unless the gate blocks; it returns
   only `hit_count`.
5. **`notifications/weekly.py`**:
   - `WeeklyReportProvider(ScheduleProvider)` with `cron: str | None` and `queue`; it
     ticks like `ExceptionMaintenanceProvider` (fire within the last hour, key
     `weekly:<int(fire.timestamp()) // 60>`).
   - `WeeklyReportHandler(TaskHandler)` with kind `WEEKLY_REPORT` and fields `sessions`,
     `source: IssueReportSource`, `notifier: MemberNotifier` and `clock`.
     - It computes the summary for the window from the previous fire to this fire once.
     - It builds `report_summary` as lines of counts, MTTR in hours, and the top issues as
       `TELE-n masked-title (count)`.
     - For each active member it calls `send_to_member(WEEKLY_REPORT, ...)` with
       `report_assigned_open` from `source.assigned_open_count`.
6. **`notifications/templates/**`**: adjust only if the p01 built-ins need fixes found by
   this plan's tests. The v12 parity tests must stay green.
7. **`api/routers/notification_settings.py`**:
   `build_notification_settings_router(deps)` with `GET` and `PUT
   /api/auth/me/notification-settings` (`PROFILE_SELF`; a member principal is required,
   otherwise 403).
   - The GET response items are `{category, stored, effective, available: {email: bool,
     slack: bool}}`.
   - PUT accepts the same list. Storing `stored: null` for alerts and workflow_tasks means
     "as configured". `stored: "slack"` without a Slack id -> 422 `recipient_unreachable`.

## Tests (`tests/notifications/v13/`; SQLite, fake transports, FakeClock)

- Defaults: a member with an email -> effective email; only a Slack id -> slack; neither ->
  off.
- `send_to_member` with mentions=off -> a `skipped` row with `recipient_opted_out` and no
  task. With slack -> the row channel is slack and one task.
- `send` with category ALERTS to `[member:alice, team:secops]` and alerts=email for alice,
  on the slack channel request -> alice skipped and the team queued. On the email request
  -> alice queued.
- `human_task_assigned` with workflow_tasks=off -> skipped. `sla_escalated` with every
  category off -> still queued.
- Requests with `category=None` produce rows identical to v1.2 (an existing v12 test
  still passes).
- Tester: the Slack MockTransport returns ok -> `ok=True`; Slack returns an error ->
  `ok=False` with `delivery_failed`; a member without a Slack id ->
  `recipient_unreachable`.
- Guard: text containing a sample secret -> `allowed=False` and `hit_count >= 1`; clean
  text -> allowed.
- Weekly:
  - a tick at the fire minute enqueues one task; a second tick in the same hour adds no
    duplicate (idempotency);
  - the handler with a fake source sends to 2 active members and skips the inactive one;
  - weekly_report=off -> a skipped row;
  - the summary text contains short ids and counts only (no raw sample).
- Router: GET and PUT round trip; an unbound token -> 403; slack without an id -> 422.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notifications/v13
uv run --frozen ruff check --no-cache src/telescreen/notifications src/telescreen/api/routers tests/notifications
uv run --frozen ty check src/telescreen/notifications src/telescreen/api/routers tests/notifications
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p09-typelint.log 2>&1; grep -E '^(src/telescreen/notifications|src/telescreen/api/routers/notification_settings|tests/notifications/v13)' /tmp/telescreen-v13-p09-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/notifications 2>&1 | tee /tmp/telescreen-v13-p09-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_flow_v12_notifications.py tests/e2e/test_wiring_v12.py tests/leak/test_leak_matrix_v12.py 2>&1 | tee /tmp/telescreen-v13-p09-pytest-shared.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p09-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep prints nothing, and the whole
`tests/notifications` suite (v1, v12 and v13) passes. The full run is informational.

## Completion Criteria

- [x] `NotificationService` satisfies both `NotificationSender` and `MemberNotifier`;
  `NotificationServiceTester` satisfies `NotificationTester`; `DlpOutboundGuard`
  satisfies `OutboundContentGuard` (ty check).
- [x] The v1.2 behavior is unchanged for uncategorized requests (existing tests are
  green).
- [x] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-12 14:26
**Tasks Completed**: Implemented category preferences and member delivery filtering, the notification settings API, notification tester, outbound DLP guard, and weekly report provider/handler with v1.3 regression tests. Existing v1.2 notification behavior remains covered by the green notification suite.
**Tasks In Progress**: Formal integrity/adversarial review and combined-tree integration review are downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: Per-edit pre/post hashes and edit intentions are recorded in `tmp/telescreen-v13-20261012-e2/p09/hashes.log` and `tmp/telescreen-v13-20261012-e2/p09/edit-intents/`. Final source hashes: `src/telescreen/notifications/preferences.py` 318df37d552588252ac46a3d0e27940b848ef436d231b20dfc24c6a5a917dc92; `member_send.py` 85bb17becab46a4933cfe5b0cf40c398bffce65f91a28ca093e387ceaaa06a54; `sender.py` 0885a26e83d88c8002d3d14ec20bbdf4953cea04ea743a8b906b81fe10d9ba53; `tester.py` 000c4204860c2b13978bd90cd1afa0098916a028e0207f7443c1fe23a8da5006; `guard.py` af2c49e616be3559acf58dfc9bec361dd6b1cbcf6f0c1d1fae628d1fb868fe3a; `weekly.py` f2c5e49b7c7e85d014863f87492dfbc0b03a58237b038f06e502334abacfc678; `notification_settings.py` 8da007deafae06b94d249b3890e20822bdca524ab48fdab7c8fca1a689e3eabd; v13 tests: `fakes.py` 208fba16879c763f673e50a10d3d156f9ff551635559976084527ee40484d3e2, `test_preferences.py` e5e8db43b7263f6f140a2c0df1f3dce83c9ed9bf97b2e0d25750c0c0f3b4ccab, `test_sender.py` 58b23dabc43a71ab83dd39197140c33ea05c31acf54c33f9714c919cd526a5d8, `test_weekly.py` bb390b60741c9c13268e8bce0efd9ad4f041407419b8b4086685fc1298784bd2, `test_tester_guard.py` 27bf73d58ed705aeba0dc36610f4270ba49860747986f139299f28cf72c3681b, `test_router.py` 9bca1d72ec96ecc7fba665fca111f6e7f755247ca0d2b8f0582044f352183dc0.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notifications` -> exit 0 (log: `/tmp/telescreen-v13-p09-format-final2.log`); `uv run --frozen ruff check --no-cache src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notifications` -> exit 0 (log: `/tmp/telescreen-v13-p09-ruff-final2.log`); `uv run --frozen ty check src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notifications` -> exit 0 (log: `/tmp/telescreen-v13-p09-ty-final2.log`); `uv run --frozen python tools/typelint.py` plus p09-path filter -> whole-repository exit 1 for 11 sibling issue-stream diagnostics, p09-path filter exit 0 with 0 owned diagnostics (logs: `/tmp/telescreen-v13-p09-typelint-final2.log`, `/tmp/telescreen-v13-p09-typelint-owned-final2.log`); `uv run --frozen pytest -p no:cacheprovider tests/notifications` -> exit 0, 127 passed (log: `/tmp/telescreen-v13-p09-pytest-final2.log`); `uv run --frozen pytest -p no:cacheprovider tests/e2e/test_flow_v12_notifications.py tests/e2e/test_wiring_v12.py tests/leak/test_leak_matrix_v12.py` -> exit 0, 5 passed (log: `/tmp/telescreen-v13-p09-pytest-shared-final2.log`); `wc -l src/telescreen/notifications/*.py src/telescreen/api/routers/notification_settings.py` with per-file `<1000` gate -> exit 0, largest file `sender.py` 317 lines (log: `/tmp/telescreen-v13-p09-wc-final3.log`).
**Notes**: All edits stayed within p09 `writePaths`; no shared template or v1.2 tests required changes. Router registration remains owned by v13-p12. Plan SHA-256 before this progress edit: `e235e2b11d558847ed0d3ab76e272162ddc2aa4adc14b2cb8b49db27ef9c30cc` (evidence: `tmp/telescreen-v13-20261012-e2/p09/plan-before-progress.sha256`).
