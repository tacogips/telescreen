# SVC-P05 Notification Service: Process, Inbox, Replica, DLP Prior-Hit Fold, Delivery Pump, Routes, Audit

**Status**: Not Started
**Design Reference**: design-docs/specs/design-service-split.md sections 4.3, 4.4, 6, 7, 9, 10, 11, 14
**Created**: 2026-10-15
**Last Updated**: 2026-10-15

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
uv run --frozen pytest -p no:cacheprovider tests/notification_service tests/notifications tests/inventory/retention tests/api/v12_routers tests/e2e/test_flow_v12_notifications.py tests/core/split/test_lazy_notifications.py 2>&1 | tee /tmp/telescreen-svc-p05-notification-service-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p05-notification-service-typelint.log 2>&1; grep -E '^(src/telescreen/(notification_service|notifications|api/routers/notification_settings)|tests/(notification_service|notifications))' /tmp/telescreen-svc-p05-notification-service-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p05-notification-service-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: the scoped commands exit 0 and the typelint grep is empty. The full suite is
informational in wave 4 (other plans in progress). Record failing ids, if any.

## Completion Criteria

- [ ] `uv run --frozen python -m telescreen.notification_service --help` exits 0.
- [ ] `grep -n MemberRow src/telescreen/api/routers/notification_settings.py` prints
      nothing. `tests/notifications/v13/test_router.py` stays green.
- [ ] The DLP parity tests pass under both policies. `prior_hits=0` paths are unchanged
      (the existing notification tests are green).
- [ ] The scoped verification exits 0. The Progress Log has hashes.

## Progress Log

(append sessions per conventions section 8)
