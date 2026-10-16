# SVC-P05 Notification Service: Process, Inbox, Replica, DLP Prior-Hit Fold, Delivery Pump, Routes, Audit

**Status**: Completed (run 7 implementation verification; independent reviews pending)
**Design Reference**: design-docs/specs/design-service-split.md sections 4.3, 4.4, 6, 7, 9, 10, 11, 14
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p05-notification-service`
- planPath: `impl-plans/active/svc-p05-notification-service.md`
- wave: 4
- dependsOn: `svc-p01-foundation`, `svc-p02-db`, `svc-p07-service-runtime`
- writePaths:
  - `impl-plans/active/svc-p05-notification-service.md`
  - `src/telescreen/notification_service`
  - `src/telescreen/api/routers/notification_settings.py`
  - `src/telescreen/notifications/sender.py`
  - `src/telescreen/notifications/dlp.py`
  - `src/telescreen/notifications/preferences.py`
  - `src/telescreen/notifications/member_send.py`
  - `src/telescreen/notifications/tester.py`
  - `src/telescreen/notifications/prior_hits.py`
  - `tests/notification_service`
  - `tests/notifications/v12`
  - `tests/notifications/v13/fakes.py`
  - `tests/notifications/v13/test_preferences.py`
  - `tests/notifications/v13/test_router.py`
  - `tests/notifications/v13/test_sender.py`
  - `tests/notifications/v13/test_tester_guard.py`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

R2/R4 and the DLP part of R3. This plan creates the `telescreen-notification` process. It
owns the notification store, the SMTP/Slack credentials, the inbox, the replica, the
delivery pump, the settings and templates routes, test send, template lookup, channel
status and its audit chain. It also makes the existing `notifications` modules
replica-capable without changing single-process behavior. The fold of producer redaction
hits into `DlpGate` is the DR-1 fix.

Imitate:

- `inventory_service/app.py`, `cli.py` and `__main__.py` (standalone composition,
  service-token middleware, lifespan worker, `migrate`);
- `wiring.py:272-443` and `wiring_v12.py`/`wiring_v13.py` (how `NotificationService`,
  `DlpGate`, `SlackNotifier`, `EmailNotifier`, `TemplateService`, `PreferenceService` and
  the two routers are built). Copy the construction, not the module imports of wiring;
- `notifications/retention.py:_claim` (dialect-specific `ON CONFLICT DO NOTHING
  RETURNING` receipt claim).

## Non-goals

- No Telescreen wiring or proxy (svc-p06). No change to
  `api/routers/notification_templates.py`; it is mounted as-is with a Notification
  `ApiContext`. `notification_settings.py` changes only as deliverable 7 says.
- **Wave independence.** svc-p04 and svc-p06 run concurrently. Do not depend on their
  in-flight files, and do not import `telescreen.wiring*` in this plan's tests.
- Do not edit `notifications/templates/*` (svc-p01 owns `render.py`/`validate.py`; the
  others need no change) or `notifications/weekly.py` (svc-p06 moves it).
- Single-process behavior is unchanged. Every new constructor parameter has a default
  equal to today's behavior.

## Deliverables and File-Level Changes

1. **DLP fold (`dlp.py`).** `DlpGate.inspect(message, prior_hits: int = 0)` and
   `inspect_redacted(message, prior_hits: int = 0)`.
   - `inspect_redacted`: `total = redactor hits + prior_hits`. Under BLOCK with
     `total > 0` -> blocked, `hit_count=total`. Otherwise run inspect; with `total == 0`
     return it unchanged. Else the verdict is BLOCKED if the inspected verdict is BLOCKED,
     else MASKED, and `hit_count = inspected + total`. These are lines 96-111 of the
     current code with `redacted_hits` replaced by `total`.
   - `inspect`: when `prior_hits > 0`, apply the same fold after the detector pass.
     - Under BLOCK -> blocked with `hit_count = detector hits + prior_hits`.
     - Otherwise PASS becomes MASKED and the counts are summed. The masked spans still
       come from the detector.
   - With `prior_hits == 0` the code path is byte-for-byte today's behavior.
2. **`prior_hits.py`.** `prior_hits_for(request, texts: TemplateTexts | None) -> int`:
   - None -> 0.
   - v1 event-less or inline -> `subject + body`.
   - Template path -> `sum(count * occurrences)` using `render.field_occurrences(texts,
     channel)` from svc-p01.
3. **`sender.py:NotificationService`.**
   - New fields with defaults: `preferences_repository` (default `PreferenceRepository()`)
     and `members: MemberReferences` (default the main-table implementation).
   - `send` uses `request.id_seed` when set: row id = `notification_row_id(id_seed, i)`.
   - `_render` passes `prior_hits_for(...)` to the gate.
   - `request.skip_unknown` records unknown refs as SKIPPED (`recipient_unknown`) instead
     of raising, even when `record_unreachable` is false. Reachability handling is
     otherwise unchanged.
   - `send_to_member` uses `self.members` and `self.preferences_repository`, and passes
     `id_seed`/`prior_hits` through.
4. **`preferences.py`.**
   - `PreferenceRepository.member` returns a `MemberContact(id, email: bool,
     slack_user_id: bool)` DTO instead of `MemberRow`.
   - Define `class PreferenceMembers(Protocol)` with that method. `PreferenceService`
     accepts any implementation.
   - `_default` uses the DTO. The main implementation still queries `MemberRow`.
5. **`member_send.py`.** Add a `MemberReferences` protocol
   (`async member_reference(session, member_id) -> str`, raising `recipient_unknown`) and
   keep `member_reference` as the main implementation. `tester.py` takes the protocol with
   the main default.
6. **`notification_service/` package.**
   - `__init__.py`, `__main__.py`.
   - `cli.py`: `serve [--host --port]`, `migrate [--adopt --writers-stopped]` (calls
     `upgrade_store`/`adopt_owner` with `Store.NOTIFICATION` and
     `settings.notification_database_url`), `send-test --template --channel --to`
     (mirrors `cli_v12` output), `audit verify|export` (`NOTIFICATION_AUDIT_MODELS`).
     Exit codes follow `telescreen.cli`.
   - `wiring.py:build_notification_runtime(settings, env, http, sessions=None, clock=None) -> NotificationRuntime`.
     - It requires `notification_database_url`, `notification_service_token_ref` and
       `notification_inventory_token_ref`; otherwise `ConfigError`.
     - It builds: `DirectoryReplica(NotificationDirectoryEntryRow)`,
       `ReplicaAuthenticator`, `ReplicaRecipientResolver`, replica `PreferenceMembers` and
       `MemberReferences`, the DLP gate from `settings.dlp_detectors`/`dlp_policy`,
       templates (`TemplateRepository`, `DbTemplateResolver`, `TemplateService`), a
       `NotificationService` with an `AuditLogService(models=NOTIFICATION_AUDIT_MODELS)`,
       a `TaskQueue` adapter over `notification_delivery_state`, the Slack/SMTP notifiers
       (credentials only here), `NotificationDeliveryHandler`, `NotificationServiceTester`
       and the inbox.
   - `inbox.py:NotificationInbox.receive(command, producer: Producer) -> NotificationCommandReceipt`.
     `Producer` is a `StrEnum` with values `telescreen` and `inventory`. The steps are:
     1. Reject a forbidden operation for the producer: an inventory command must contain
        only send or member-send operations with event `data_overdue`; anything else ->
        `forbidden`.
     2. If the event id is already in `notification_inbox`, or in
        `retention_notification_receipts` for inventory commands, return `duplicate`. A
        different digest -> `conflict` error.
     3. Pre-check recipients through the replica. On `recipient_unknown` with
        `now - occurred_at < grace` -> `deferred`, with no writes.
     4. In one transaction: claim the inbox row, then apply each operation in order:
        - send -> `NotificationService.send(NotificationRequest(..., id_seed=operation_seed(event_id, i), prior_hits=op.redaction, skip_unknown=True))`;
        - member-send -> `send_to_member(... id_seed, prior_hits)`; an unknown member after
          grace gives one SKIPPED row through
          `send(recipients=(f"member:{member_id}",), skip_unknown=True)`;
        - directory -> `DirectoryReplica.apply`.
     5. Return `applied`.
   - `worker.py:DeliveryPump.tick()`.
     - Claim due `notification_delivery_state` rows (`available_at <= now`, lease free).
       Use `with_for_update(skip_locked=True)` on PostgreSQL and the immediate transaction
       on SQLite.
     - Seed missing state rows for `notifications.status='queued'` rows (copied at cutover).
     - Call `NotificationDeliveryHandler.handle(ClaimedTask(...))` and apply the result:
       done deletes the state; retry updates attempt/available_at; dead deletes the state.
   - `app.py:create_notification_app(runtime)`:
     - The service middleware accepts either credential. Telescreen's credential allows
       every route; the inventory credential allows only `POST /internal/notification/v1/commands`.
       Otherwise 401/403, using the static envelope from `inventory_service/app.py`.
     - Internal routes: `/commands`, `/test`, `/templates/lookup`, `/channels` and `/health`
       (`directory_revision` watermark).
     - Public routes: `build_notification_settings_router` and
       `build_notification_templates_router` with the replica-authenticated `ApiContext`.
     - The lifespan runs the delivery pump.
7. **Settings router without `MemberRow` (review F1).**
   `src/telescreen/api/routers/notification_settings.py` calls
   `session.get(MemberRow, member_id)` at lines 96 (GET) and 124 (PUT). The notification
   store has no `members` table, so both fail in split mode. Changes:
   - Add `PreferenceService.member_contact(session, member_id) -> MemberContact | None`,
     delegating to its injected `PreferenceMembers`.
   - The router uses it in both places. When it returns None, raise exactly the existing
     error: `FORBIDDEN`, "member principal is unavailable"; copy the existing code and
     detail.
   - Remove the `MemberRow` import.
   - Keep the `NotificationSettingsRouterDeps` fields, paths, permissions, response shapes
     and error codes unchanged, so svc-p06 wiring needs no change.
   - The package `notifications/__init__.py` is made lazy by svc-p01. Do not edit it.
8. **Main-table defaults stay in `notifications`.** Replica adapters live in
   `notification_service`. Gate (R7):
   - `telescreen.notification_service` must not import `telescreen.wiring*`,
     `telescreen.issues`, `telescreen.workflow`, `telescreen.alerts`,
     `telescreen.collectors`, `telescreen.police`, `telescreen.search`,
     `telescreen.directory` or `telescreen.inventory*`.
   - Verify with an isolated subprocess, like `tests/inventory_service/test_config.py:98-108`.

## Key Pitfalls

- The grace check must not write anything. A deferred command must be applied completely
  later, never partially.
- Deterministic ids: an applied command retried after a lost reply returns `duplicate`.
  Rows are never duplicated.
- With prior hits, the BLOCK verdict body is exactly `[BLOCKED BY DLP]`, as today.
- Never log payloads, recipients or addresses. Log codes and ids only.
- The templates router must keep its permissions and responses. Mount the same builder.
- SMTP/Slack secrets are resolved only in `build_notification_runtime`.

## Tests

`tests/notification_service`:

- **DLP parity (DR-1).** For `DlpPolicy.MASK` and `BLOCK`, and for a runtime-assembled
  sample secret in (a) a context field used by the template, (b) an inline event body and
  (c) a v1 event-less body:
  - Path 1 (single process): `NotificationService.send` with the raw request.
  - Path 2 (split): svc-p01 codec plus a local redaction helper (reproduce the svc-p03
    adapter rule in the test), then `NotificationInbox.receive`.
  - Assert equal `dlp_verdict`, `hit_count > 0` in both, and the same audit action
    (`notification.queued`/`notification.blocked`).
  - A context field that the template does not render gives prior hits 0, as today.
- Inbox:
  - a duplicate event -> `duplicate`, with no new rows;
  - a retention receipt id -> `duplicate`;
  - an unknown recipient within grace -> `deferred` with no rows; after grace -> a
    SKIPPED row with `recipient_unknown`;
  - a directory operation from the inventory credential -> 403;
  - a team send creates rows with the deterministic ids.
- Delivery pump with fake notifiers: queued -> sent; a failure -> retry with backoff;
  a dead row records the failed status; a copied queued row without state is seeded and
  delivered.
- Routes:
  - settings GET/PUT through `create_notification_app` on
    `make_sqlite_harness(store=Store.NOTIFICATION)` (no `members` table) with a replica
    member -> 200 with the effective settings. An absent replica member -> 403 "member
    principal is unavailable" (F1);
  - templates list, validate and preview with role permissions identical to the existing
    router tests;
  - a missing service header -> 401.
- `/test` with configured fakes -> `TestOutcome.ok`. Unconfigured ->
  `integration_unconfigured`.
- `/channels` -> flags.
- Import gate subprocess: no forbidden module is in `sys.modules` after
  `build_notification_runtime`.

Updated `tests/notifications/v12`/`v13` tests: the defaults keep the existing assertions
passing. Adjust only the fakes and constructor calls.

g2 sweep: these unowned callers construct `NotificationService`, `PreferenceService`,
`PreferenceRepository` or `DlpGate` and must pass **unchanged**:
`tests/notifications/test_notifications.py`, `tests/notifications/v14`,
`tests/inventory/retention/conftest.py`, `tests/api/v12_routers/test_routers.py`,
`tests/notifications/v13/test_weekly.py` (moved by svc-p06),
`notifications/templates/service.py` and `notifications/guard.py`. Keep every existing
positional constructor order. New parameters go last with defaults. If one of them cannot
pass unchanged, stop and file a Reconciliation request.
g3 sweep: `tests/core/split/test_lazy_notifications.py` (accepted svc-p01, not owned) must
pass unedited, so `notifications/dlp.py` and `prior_hits.py` never import
`notifications.sender`, `email`, `slack` or `delivery`.

## Verification

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/notification_service src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notification_service tests/notifications
uv run --frozen ruff check --no-cache src/telescreen/notification_service src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notification_service tests/notifications
uv run --frozen ty check src/telescreen/notification_service src/telescreen/notifications src/telescreen/api/routers/notification_settings.py tests/notification_service tests/notifications
uv run --frozen pytest -p no:cacheprovider tests/notification_service tests/notifications tests/inventory/retention tests/api/v12_routers tests/e2e/test_flow_v12_notifications.py tests/core/split/test_lazy_notifications.py
uv run --frozen python tools/typelint.py; ! grep -E '^(src/telescreen/(notification_service|notifications|api/routers/notification_settings)|tests/(notification_service|notifications))' /tmp/telescreen-svc-p05-notification-service-g7-typelint.log
uv run --frozen python -m telescreen.notification_service --help
! grep -n MemberRow src/telescreen/api/routers/notification_settings.py
```

Run 7 final-source evidence (source hash manifest:
`tmp/telescreen-svc-20261016-g7/svc-p05-notification-service/source-hashes.sha256`,
34 entries, manifest SHA-256 `aed01dfd8e0398bad2fc1d6ddfabb6b857c9d3ec53497c0c2bb80a5f1d670025`):
format, ruff, ty, scoped pytest, assigned-path typelint scan, CLI help, MemberRow gate and
plan line-count gate all exited 0. Scoped pytest collected 192 tests: 191 passed, 0 failed,
and 1 offline-gated PostgreSQL integration test skipped. Complete logs are in
`tmp/telescreen-svc-20261016-g7/svc-p05-notification-service/attempt-1/` and at the
`/tmp/telescreen-svc-p05-notification-service-g7-*.log` paths. The full suite is
informational in wave 4 and is owned by the integration flow.

## Completion Criteria

- [x] `uv run --frozen python -m telescreen.notification_service --help` exits 0.
- [x] `grep -n MemberRow src/telescreen/api/routers/notification_settings.py` prints
      nothing. `tests/notifications/v13/test_router.py` stays green.
- [x] The DLP parity tests pass under both policies. `prior_hits=0` paths are unchanged
      (the existing notification tests are green).
- [x] The scoped verification exits 0. The Progress Log has hashes.
- [x] Delivery pump continues after an exception from one tick, logs only its type, and
      remains cancellable; the regression test and existing pump behavior tests pass.
- [x] Run 7 planning-commit gates all exit 0; scoped pytest reports 191 passed, 1 skipped,
      and 0 failed, with the g7 source hash manifest recorded below.

## Progress Log

### 2026-10-16 — Implementation

- Implemented the notification service process, runtime, inbox, delivery pump, API routes,
  replica-backed preferences/member references, settings router decoupling, and DLP
  prior-hit folding with compatibility defaults.
- Initial implementation checkpoint identity: branch `feat/service-split`, HEAD
  `f0a34727fb479c141a8e503ffa23318ffe7a8504`; SHA-256 file manifest at
  `tmp/telescreen-svc-20261016-g6/svc-p05-notification-service/source-hashes.sha256`
  (manifest digest `dbc626849843113be7cb1cd7253b2bfabed2e06b91ae6e7f5d69cfe8e046cee5`).
- Initial checkpoint scoped gates passed: Ruff format/check, ty, assigned-path typelint scan, CLI help,
  no-`MemberRow` check; pytest reported 190 passed and 0 failed. Full logs are
  recorded above under the plan-local evidence directory.
- Formal test-integrity/adversarial/integration reviews and review-dependent finalization
  remain downstream workflow steps.

### 2026-10-16 — Test-integrity repairs

- TI-P05-1: added service middleware 401/403 checks and producer authorization no-write
  cases, including Telescreen directory apply.
- TI-P05-2: added retention receipt duplicate coverage with no-write assertions and
  narrowed the changed-digest assertion to `ErrorCode.CONFLICT`.
- TI-P05-3: added runtime-backed settings, `/channels`, `/test`, replica member-reference,
  and delivery queue coverage through the Notification app/runtime.
- Added the plan's DLP audit-action parity assertion, multi-reference deterministic-ID test,
  and explicit `telescreen.inventory_service` import gate.
- Refreshed the 23-file source hash manifest and reran all scoped gates; pytest reported
  190 passed, 0 failed. Independent review of the repairs remains pending.

### 2026-10-16 — Adversarial repair ADV-P05-1

- `DeliveryPump.run` now catches `Exception` per tick, logs only `type(error).__name__`,
  and proceeds to the next poll. `CancelledError` remains uncaught and terminates the task.
- Added `test_delivery_pump_continues_after_tick_error_and_cancels`, which proves a
  raising first tick is followed by another tick, the log excludes exception details,
  and cancellation ends the pump. Existing sent/retry/dead/seeding tests remain intact.
- The scoped gate reported 191 passed, 1 offline integration skip, and 0 failures. The
  refreshed 23-entry manifest digest is
  `da8f4346d3b30aac755c0d36ac072f7bf23a42fb84de3fdf2ff0b7c4081b2a6a`; full logs are
  listed above. Independent adversarial re-review remains pending.

### Run 7 Scope (manifest g7, base a5392d9)

Re-verify only. Deliverables, the TI-P05-1..3 repairs and ADV-P05-1 are in the tree. Do
not re-implement them.

1. Re-run the Verification block on the planning commit. Write the logs and the typelint
   log under `tmp/telescreen-svc-20261016-g7/svc-p05-notification-service/`. Point the
   typelint grep at that new log, not at the g6 path. Every command must exit 0.
2. Go through the independent test-integrity, adversarial and integration review. Change
   source or tests only for a real gate failure or review finding, and fix it at its root
   cause inside `writePaths`. List only fixed findings in `addressedFeedback`.
3. Refresh the source hash manifest under the g7 evidence root. Set Status to Completed.
   Append one session entry of at most 12 lines. Keep `priorVerification` empty
   (conventions 7a).

### 2026-10-16 — Run 7 re-verification

- Re-verified the already implemented deliverables on planning commit
  `45078bd2ac959ebc9584fad785c59dea757593f0`; no source or test re-implementation was needed.
- All assigned gates exited 0: format, Ruff, ty, scoped pytest, assigned-path typelint,
  service CLI help, no-`MemberRow`, and plan line-count checks. Pytest: 191 passed,
  1 offline-gated integration skip, 0 failed.
- Refreshed the 34-file source manifest at
  `tmp/telescreen-svc-20261016-g7/svc-p05-notification-service/source-hashes.sha256`
  (SHA-256 `aed01dfd8e0398bad2fc1d6ddfabb6b857c9d3ec53497c0c2bb80a5f1d670025`).
- Independent test-integrity, adversarial and integration reviews remain downstream.
