# P17 Notifications and Outbound DLP Gate Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-masking.md (DLP gate), design-docs/specs/design-data-model.md (notifications), design-docs/specs/design-workflow-engine.md (Recipient resolution)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p17-notifications`
- planPath: `impl-plans/active/p17-notifications.md`
- wave: 4
- dependsOn: `p02-db-schema`, `p04-ports`, `p08-masking`
- writePaths:
  - `impl-plans/active/p17-notifications.md`
  - `src/telescreen/notifications`
  - `tests/notifications`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Acceptance: "Outbound notifications pass a DLP gate that masks or blocks raw values".
`NotificationSender.send` runs in the caller's transaction with NO network I/O. It
gates, records, audits and enqueues. A separate `notification_delivery` task sends
through Slack or SMTP later (at-least-once, never unrecorded).

## Non-goals

- No workflow logic (p24 and p25 call the port). No new dependencies: Slack uses httpx,
  email uses stdlib `smtplib` and `email`.

## Deliverables (`src/telescreen/notifications/`)

- `dlp.py`:
  - `OutboundMessage(subject: str, body: str)`.
  - `CensoredMessage(subject: str, body: str, verdict: DlpVerdict, hit_count: int, seal: _Seal)`.
    `__post_init__` raises `TypeError` unless `seal is _SEAL` (a module-private
    singleton). Only this package can build one.
  - `DlpGate(detector: Detector, redactor: Redactor, policy: DlpPolicy)` with
    `async def inspect(self, message: OutboundMessage) -> CensoredMessage`:
    1. Run the detector on subject and body (two `ScanItem`s).
    2. No hits -> `pass`.
    3. Policy `block` with hits -> `blocked` (subject and body replaced by
       `[BLOCKED BY DLP]`).
    4. Policy `mask` -> mask each span (right to left) with `mask_value`, then run
       `redactor.has_hits` on the result. A residual hit -> `blocked`; otherwise
       `masked`.
  - `rehydrate(row: NotificationRow) -> CensoredMessage` (module function used only by
    delivery).
- `repository.py`: insert and load `NotificationRow`; update status, attempts and
  `sent_at`.
- `sender.py`: `NotificationService(gate: DlpGate, resolver: RecipientResolver, queue: TaskQueue, audit: AuditLog, clock: Clock)`
  implements `NotificationSender`. `send(session, request)`:
  1. Resolve every recipient ref; this only validates (an unknown ref ->
     `recipient_unknown` propagates). Resolved addresses are never stored.
  2. Run the gate once.
  3. Insert one `notifications` row per recipient ref (status `queued`, or `blocked`)
     with the masked subject and body, the verdict and the hit count.
  4. Append audit `notification.queued` or `notification.blocked` per row (details: ids,
     channel, verdict).
  5. For queued rows, enqueue `TaskSpec(kind=notification_delivery, ref_id=<id>, payload=json_null(), available_at=now, idempotency_key=f"notify:{id}", max_attempts=5)`.
  6. Return `NotificationReceipt`.
- `delivery.py`: `NotificationDeliveryHandler(sessions, resolver, slack: SlackNotifier, email: EmailNotifier, audit: AuditLog, clock)`
  implements `TaskHandler` (`kind = EngineTaskKind.NOTIFICATION_DELIVERY`):
  1. Load the row; skip (done) unless the status is `queued`.
  2. Resolve the recipients again and rehydrate.
  3. Send to every target of the channel; `delivery_attempts += 1`.
  4. Success -> `sent`, `sent_at`, audit `notification.sent`, `TaskResult.done()`.
  5. `RateLimited` -> retry at `now + retry_after`.
  6. Another failure -> retry with backoff `30 s * 2^(attempt-1)`. On the last attempt
     (`attempt >= max_attempts`) -> status `failed`, audit `notification.failed`,
     `TaskResult.dead(delivery_failed)`.
- `slack.py`: `SlackNotifier(http: httpx.AsyncClient, bot_token: SecretValue | None, api_base: str = "https://slack.com/api")`
  with `send(target: str, message: CensoredMessage) -> None`. It POSTs
  `chat.postMessage` (`channel=target`, `text=subject + "\n" + body`) with a bearer
  token. A 429 raises `RateLimited(Retry-After)`; `ok: false` or a non-2xx status raises
  `TelescreenError(delivery_failed)`. A missing token -> `config_error`.
- `email.py`: `class SmtpTransport(Protocol): def send(self, message: EmailMessage) -> None`;
  `SmtplibTransport(host, port, username, password: SecretValue | None, starttls: bool)`;
  `EmailNotifier(transport: SmtpTransport, sender: str)` with
  `async send(to: str, message: CensoredMessage) -> None`, which builds `EmailMessage`
  and calls `transport.send` via `asyncio.to_thread`.

## Key Pitfalls

- `send` must not perform network calls. A rollback of the caller's transaction must
  remove the row, the audit record and the task together (all through the same session).
- Delivery is at-least-once, keyed by notification id. Never send from `send`.
- Never log the subject or body; log the notification id, status and code.
- Blocked messages are stored with the placeholder text, never the original.

## Tests (`tests/notifications/`)

SQLite harness, fake `TaskQueue` and `AuditLog` (or recording fakes that insert
`EngineTaskRow` and audit rows directly), a fake resolver, the real `RuleDetector`,
Redactor and `FakeClock`.

- A clean message -> verdict `pass`, a row per recipient, a task per row with key
  `notify:<id>`.
- A message with a sample token, policy mask -> `masked`; the stored body has no leak
  (`assert_no_leaks`).
- Policy block -> `blocked`, no task enqueued, audit `notification.blocked`.
- A fake detector reporting a span whose masked form still matches (simulate with a
  redactor fake) -> `blocked`.
- Building a `CensoredMessage` outside the module -> `TypeError`.
- Delivery: queued row -> Slack MockTransport receives `chat.postMessage` with the
  masked text and the row becomes `sent`; 429 -> retry at `Retry-After`; repeated 500
  until max attempts -> `failed` and dead; a row already `sent` -> skipped (idempotent).
- Email: a fake transport receives the message with the masked body.
- An unknown recipient -> `recipient_unknown`, and nothing is inserted.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/notifications tests/notifications
uv run --frozen ruff check --no-cache src/telescreen/notifications tests/notifications
uv run --frozen ty check src/telescreen/notifications tests/notifications
uv run --frozen pytest -p no:cacheprovider tests/notifications 2>&1 | tee /tmp/telescreen-p17-notifications-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p17-notifications-typelint.log 2>&1; grep -E '^(src/telescreen/notifications|tests/notifications)' /tmp/telescreen-p17-notifications-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] DlpGate, CensoredMessage guard, NotificationService, delivery handler, Slack and email notifiers delivered with the exact names
- [ ] DLP mask, block and residual tests and delivery idempotency tests pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
