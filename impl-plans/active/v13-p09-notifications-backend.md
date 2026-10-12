# V13-P09 Notifications Backend: Per-User Settings, Member Delivery, Tester, Outbound Guard, Weekly Report

**Status**: Ready
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
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p09-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep prints nothing, and the whole
`tests/notifications` suite (v1, v12 and v13) passes. The full run is informational.

## Completion Criteria

- [ ] `NotificationService` satisfies both `NotificationSender` and `MemberNotifier`;
  `NotificationServiceTester` satisfies `NotificationTester`; `DlpOutboundGuard`
  satisfies `OutboundContentGuard` (ty check).
- [ ] The v1.2 behavior is unchanged for uncategorized requests (existing tests are
  green).
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
