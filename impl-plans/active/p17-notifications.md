# P17 Notifications and Outbound DLP Gate Implementation Plan

**Status**: In Progress (run A resume, session 338)
**Design Reference**: design-docs/specs/design-masking.md (DLP gate), design-docs/specs/design-data-model.md (notifications), design-docs/specs/design-workflow-engine.md (Recipient resolution)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

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

## Run A Resume (2026-10-09, session 338)

State: interrupted at s319; most deliverables exist in tree. Dispatch:
`impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A1. Resume from the
in-tree code; do not rewrite working modules. Static gap review (s338 planning) found:

- [ ] R0 Baseline: run the Verification block on the untouched tree; log exit codes to
  `/tmp/telescreen-p17-notifications-baseline-*.log` before any edit.
- [ ] R1 `delivery.py:NotificationDeliveryHandler.handle` resolves recipients outside the
  failure handling, so `recipient_unknown` at delivery time escapes as an exception. A
  recipient that no longer resolves -> status `failed`, audit `notification.failed`,
  `TaskResult.dead(...)` with a static code; never raise out of the handler. Test it.
- [ ] R2 The same handler keeps the DB transaction open across the network send. Split
  it: transaction 1 loads and checks the row; the send happens with no open transaction;
  transaction 2 records the outcome (re-checking status is still `queued`). Keep
  idempotency: a row already `sent` is skipped. Test: the sent-row skip still passes, and
  a fake notifier sees no open session (for example, it asserts a flag the test sets).
- [ ] R3 Accepted plan deviation (no code change): `NotificationService` and
  `NotificationDeliveryHandler` take an extra `repository: NotificationRepository` field.
  Record it in the Progress Log so assembly (run B, p28) wires it.
- [ ] R4 Strengthen tests to match the Tests section: the residual-hit case uses a
  redactor fake whose masked output still matches; "repeated 500 until max attempts" drives
  the handler attempt by attempt to `failed` and dead; the email test asserts the masked
  body is present AND there is no leak.
- [ ] R5 Re-run the full Verification block; every command exits 0; log paths recorded.

## Completion Criteria

- [x] DlpGate, CensoredMessage guard, NotificationService, delivery handler, Slack and email notifiers delivered with the exact names
- [ ] DLP mask, block and residual tests and delivery idempotency tests pass (after R1-R4)
- [ ] Run A Resume items R0-R5 checked with evidence
- [ ] Scoped verification passes on the final tree; logs recorded
- [ ] Progress Log updated (run A session)

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 07:30
**Tasks Completed**: Implemented the sealed DLP gate, transactional notification recording and idempotent task enqueue, retryable Slack/email delivery, and SQLite-backed behavioral tests. Clean, mask, block, residual, seal, recipient validation, delivery replay, retry-after, exponential retry/dead-letter, Slack and SMTP paths are covered.
**Tasks In Progress**: None for p17 implementation. Formal review and combined-tree integration review remain downstream workflow steps.
**Blockers**: None in p17 scope.
**Notes**:
- Scoped verification passed: ruff format/check, ty, pytest (11 passed), and typelint on `src/telescreen/notifications` plus `tests/notifications`. Complete logs: `/tmp/telescreen-p17-notifications-format-final.log`, `/tmp/telescreen-p17-notifications-ruff-final.log`, `/tmp/telescreen-p17-notifications-ty.log`, `/tmp/telescreen-p17-notifications-pytest-final.log`, `/tmp/telescreen-p17-notifications-typelint-scoped.log`.
- Full-tree `uv run --frozen python tools/typelint.py` exited 1 with 119 diagnostics outside p17; `/tmp/telescreen-p17-notifications-typelint-full-final.log`. The p17 path filter found no diagnostics; `/tmp/telescreen-p17-notifications-typelint-filter-final.log`. This is not a passing full-tree gate.
- Final source/test SHA-256 inventory: `/tmp/telescreen-p17-notifications-evidence/final-hashes.txt`. Hashes: `__init__.py` 1f25d6c00bfb056b3131c510777bf8ebfa0f8f906ed3622c105fab6d007fdac8; `dlp.py` 8dec399a7f312a1e295cad25b04bfaf8350851089460ccf4126fcf02e71ffb41; `repository.py` fea524018a56e23941c5cf792f465b814a591d283fcd248982a64491f8b762e1; `sender.py` 6cf362a79385ec0369dd403d01fd8e64fe55c9734b7c246707b9dfa5c6dd17f4; `delivery.py` 7d228dbefb7a7e175b76b3e81ceac174cb66692aa0b253937a8806165dadafeb; `slack.py` 93ec1baeee3197e77347ea4b58a60639f5b624a2b428c5cc5d0684b60e92bc60; `email.py` 29bfb3f473d1a8be20f2b6c578177da9a4ddea6c0b05b0d11fa04dba49f7c9dc; `test_notifications.py` cdfe3e11f2f918f27ed50022814f2f39c84b06d00024e37d2989850e4cc7d2ee.
- Earlier verification failures and their corrections are preserved in `/tmp/telescreen-p17-notifications-evidence/edit-intentions.md` and the original attempt logs; the final source-matched pytest run passed all 11 tests.
