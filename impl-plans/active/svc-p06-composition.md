# SVC-P06 Telescreen Split Composition: Wiring, Proxies, Integrations, CLI, Weekly Move

**Status**: Not Started
**Design Reference**: design-docs/specs/design-service-split.md sections 3, 4.4, 5, 9 (public routes), 10, 14, 15, C5, C8
**Created**: 2026-10-15
**Last Updated**: 2026-10-15

## Plan Metadata

- planId: `svc-p06-composition`
- planPath: `impl-plans/active/svc-p06-composition.md`
- wave: 4
- dependsOn: `svc-p01-foundation`, `svc-p02-db`, `svc-p03-publishers`
- writePaths:
  - `impl-plans/active/svc-p06-composition.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_builders.py`
  - `src/telescreen/wiring_v12.py`
  - `src/telescreen/wiring_v13.py`
  - `src/telescreen/wiring_inventory_parts.py`
  - `src/telescreen/wiring_notify_local.py`
  - `src/telescreen/wiring_split.py`
  - `src/telescreen/inventory_service/wiring.py`
  - `src/telescreen/inventory_service/bureau.py`
  - `src/telescreen/inventory_service/clients.py`
  - `tests/e2e/test_inventory_client.py`
  - `src/telescreen/integrations/inventory_client.py`
  - `src/telescreen/integrations/status.py`
  - `src/telescreen/integrations/tests.py`
  - `src/telescreen/issues/weekly_report.py`
  - `src/telescreen/notifications/weekly.py`
  - `src/telescreen/cli.py`
  - `src/telescreen/cli_v12.py`
  - `tests/e2e/test_wiring_split.py`
  - `tests/e2e/test_wiring_v12.py`
  - `tests/e2e/test_wiring_v13.py`
  - `tests/e2e/test_wiring_v14.py`
  - `tests/e2e/test_wiring_cli.py`
  - `tests/e2e/test_cli_v12.py`
  - `tests/e2e/test_cli_event_loop.py`
  - `tests/e2e/test_route_permissions.py`
  - `tests/e2e/test_api_routes.py`
  - `tests/integrations/test_integrations.py`
  - `tests/integrations/test_router.py`
  - `tests/notifications/v13/test_weekly.py`
  - `tests/issues/test_weekly_report.py`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

Compose Telescreen for the two topologies (design 3). Single process must behave exactly
as today. Split mode does the following:

- binds the svc-p03 outbox adapters to `NotificationSender`/`MemberNotifier` for every
  producer;
- installs the projection markers and relay;
- delivers notification and inventory commands;
- proxies `/api/data/*`, `/api/auth/me/notification-settings` and
  `/api/notification-templates[/...]`;
- uses Notification for the template catalog, test sends and channel status;
- holds no SMTP/Slack credentials;
- imports no inventory or notification implementation (R7).

Imitate:

- `wiring.py:349-361` (lazy `build_v14` import in local mode);
- `inventory_service/wiring.py:build_remote_inventory`;
- `inventory_service/bureau.py:build_proxy_router`.

## Non-goals

- No consumer-side code (svc-p04/p05).
- Do not edit `wiring_v14.py` (the single-process inventory path is unchanged).
- Do not edit `api/routers/notification_*.py`. `notification_settings.py` is owned by
  svc-p05, which replaces its `MemberRow` reads without changing
  `NotificationSettingsRouterDeps`.
- Do not edit `tests/inventory_service/*` (svc-p04 and svc-p08) or
  `tests/e2e/test_inventory_split_runtime.py` (a single-process separate-store test that
  must stay green, run in the scoped verification below).
- **Wave independence (F3).** svc-p04 and svc-p05 run concurrently. This plan's scoped
  tests use only svc-p01, svc-p02 and svc-p03 output and files this plan owns. Never
  import `telescreen.notification_service` or depend on svc-p04's
  `inventory_service/app.py` changes.

## Deliverables and File-Level Changes

1. **Topology.** `build_container` calls `core.topology.topology_of(settings)` first; a
   `ConfigError` stops startup before any resource opens.
2. **`wiring_notify_local.py` (new).**
   - `build_local_notifications(...) -> NotificationParts` holds everything
     notification-specific that `wiring.py`, `wiring_v12.py` and `wiring_v13.py` build
     today: `NotificationService`, `NotificationDeliveryHandler`, the Slack/SMTP
     notifiers, `TemplateService`/`DbTemplateResolver`, `PreferenceService`,
     `NotificationServiceTester`, and the notification settings and templates routers.
   - `NotificationParts` (a frozen dataclass, defined in `wiring_inventory_parts.py` or a
     new small parts module inside your paths) has fields typed only by ports or
     framework types:
     - `sender: NotificationSender`, `members: MemberNotifier`;
     - `catalog: TemplateCatalog`, `tester: NotificationTester`;
     - `channel_status: ChannelStatusSource`;
     - `routers: tuple[APIRouter, ...]`, `handlers: tuple[TaskHandler, ...]`,
       `providers: tuple[ScheduleProvider, ...]`.
   - `wiring.py`/`v12`/`v13` import it lazily (inside the single-process branch).
   - Move the existing module-level `telescreen.notifications.*` and
     `api.routers.notification_*` imports out of `wiring.py`, `wiring_v12.py` and
     `wiring_v13.py`. Exception: `notifications.dlp`/`notifications.guard` stay for the
     GitHub outbound guard (`wiring_v13.py:206`). The constructed objects and argument
     values must be identical to today.
3. **`wiring_split.py` (new).** `build_split_notifications(...) -> NotificationParts`:
   - `OutboxNotificationSender`/`OutboxMemberNotifier` with the local
     `DirectoryRecipientResolver`, `DirectoryOwnerResolver`, `Redactor` and `SqlTaskQueue`;
   - `TemplateCatalogClient`, `RemoteNotificationTester` and `ChannelStatusClient`;
   - handlers: `NotificationCommandDeliveryHandler`, `NotificationDeliveryNoop` and
     `ProjectionRelayHandler`;
   - providers: `NotificationRecoveryProvider`, `ProjectionRelayProvider`;
   - `install_projection_markers(primitives.sessions, clock)`;
   - the notification proxy router (deliverable 5).

   The weekly report is wired from `issues/weekly_report.py` in both modes.
4. **Remote inventory (`inventory_service/wiring.py`) and `clients.py` (F3).**
   - Create `integrations/inventory_client.py:InventoryClient` with only
     `receive(command) -> InventoryReceipt` (posting to `/commands`), copied from
     `inventory_service/clients.py:InventoryClient.receive`. There is no `status` (C1).
   - Switch `inventory_service/wiring.py` to it. Then run
     `grep -rn "inventory_service.clients" src tests`, log the output, and delete
     `src/telescreen/inventory_service/clients.py` only when the grep shows no importer
     other than the file itself. svc-p04 stops importing it from `app.py` in parallel; if
     `app.py` still imports it when you verify, keep the file and leave a Reconciliation
     request.
   - `build_remote_inventory` keeps only the publisher, delivery, recovery and the
     `/api/data/*` proxy. `RemoteInventoryWiring` drops `retention_receiver`. The
     `BureauEndpoints` construction, `AuditCommandReceiver` and
     `RetentionNotificationReceiver` are removed.
   - Add `tests/e2e/test_inventory_client.py`: the `/commands` client posts the encoded
     command with the service header and returns a validated receipt. A malformed body ->
     `TelescreenError`. Use `httpx.MockTransport`.
5. **`inventory_service/bureau.py`.**
   - Delete `BureauEndpoints`, `build_bureau_router` and `decode_body` (unless used by the
     proxy).
   - Generalize to `build_proxy_router(transport, paths: tuple[str, ...] = INVENTORY_PROXY_PATHS)`,
     with module constant `INVENTORY_PROXY_PATHS = ("/api/data/{path:path}",)`. The
     default keeps the existing `tests/inventory_service/test_transport.py` call
     `build_proxy_router(ServiceHttp(...))` working (F3). The notification paths are
     `("/api/auth/me/notification-settings", "/api/notification-templates",
     "/api/notification-templates/{path:path}")`.
   - Keep the header filtering and streaming, and the GET/PUT/POST methods as today.
   - The notification proxy uses `ServiceHttp(prefix="")` with
     `notification_service_url` and the Telescreen notification token, so public paths
     are unchanged.
6. **Integrations.**
   - `integrations/status.py`: `IntegrationStatusService` gains
     `channels: ChannelStatusSource | None = None`. When set, email/slack `configured`
     comes from it; an unreachable service counts as not configured.
   - `integrations/tests.py`: `_configured` for email/slack uses the same source when set.
     The tester is the injected `NotificationTester` (remote in split mode).
   - Persisting the check results and the audit stay in Telescreen.
7. **Weekly move (C5).** Move `notifications/weekly.py` to `issues/weekly_report.py`
   unchanged (classes `WeeklyReportProvider` and `WeeklyReportHandler`). Delete the old
   file and update the `wiring_v13.py` import. Move `tests/notifications/v13/test_weekly.py`
   to `tests/issues/test_weekly_report.py`.
8. **CLI.**
   - `cli.py`:
     - `migrate --store` choices become `main`, `inventory` and `notification`; notification
       uses `settings.notification_database_url` (otherwise `ConfigError`, exit 3).
     - Add `projections resync`, which calls `projections.resync.resync` and prints only the
       count; in single-process mode it exits 2 with "projections require split mode".
     - The `notify test` command in split mode exits 2 with "use telescreen-notification
       send-test".
     - `notification-store` subcommands are already registered by svc-p04 through
       `cli_inventory_store.add_commands`; do not duplicate them.
   - `cli_v12.py`: move its `telescreen.notifications.*` imports inside the function.
9. **Static gate (R7) in `tests/e2e/test_wiring_split.py`.** A subprocess builds a split
   container (SQLite main, fake URLs, an `httpx.MockTransport`) and asserts that no module
   in `sys.modules` starts with:
   - `telescreen.inventory.` or `telescreen.wiring_v14`;
   - `telescreen.notifications.sender`, `.delivery`, `.email`, `.slack`, `.tester`,
     `.retention`, `.preferences`, `.repository`, `.member_send` or `.templates`;
   - `telescreen.api.routers.notification_`.

   `telescreen.notifications.dlp` and `.guard` are allowed (design correction recorded by
   svc-p08).

## Key Pitfalls

- Single process must build byte-identical behavior. Run the whole existing e2e and
  wiring suite.
- In split mode, never resolve `slack_bot_token_ref` or `smtp_password_ref`; assert this
  with an env source that raises on those names.
- The public paths and permission behavior of the proxied routes are enforced by
  Notification. Telescreen adds no permission dependency to proxied paths, matching the
  inventory proxy. Update `tests/e2e/test_route_permissions.py` only to classify the
  split proxy routes the same way the `/api/data` proxy is classified.
- `wiring.py` must stay under 1000 lines. It shrinks.
- The weekly report in split mode uses the outbox `MemberNotifier`.
- **Compatibility pins from the g2 sweep.**
  - Tests read `container.v12.notifications` (calling `.send(session, NotificationRequest)`)
    and `container.v12.templates` (`tests/leak/flows_v12.py` is owned by svc-p08;
    `tests/e2e/test_wiring_v12.py`; and `tests/live/*`, which are unowned and skipped
    offline). Keep both attribute names. In single process they hold the same objects as
    today. You may widen the field types to the ports (`NotificationSender`,
    `TemplateCatalog`) only if every one of those call sites still type-checks. Keep
    `container.workflow.templates is container.v12.templates` true.
  - `tests/e2e/test_cli_event_loop.py` patches `cli._build_cli_container`,
    `cli._run_async_command` and `cli.close_container`. Keep these module-level names
    and signatures.
  - `tests/e2e/test_cli_v12.py` runs single-process `notify test` through
    `_run_async_command`. Its output and exit codes are unchanged.

## Tests

- `tests/e2e/test_wiring_split.py`:
  - split container routes include the three notification proxy paths and the
    `/api/data` proxy, and exclude the local notification routers;
  - the handlers include command delivery, the no-op delivery and the relay;
  - an alert producer through `container` writes `notification_outbox` and an engine task;
  - a member display-name change writes a `projection_changes` marker;
  - the import gate (deliverable 9);
  - only the inventory URL set -> `ConfigError`;
  - (moved from `tests/inventory_service/test_config.py`, F3) split settings without
    `TELESCREEN_NOTIFICATION_SERVICE_TOKEN` -> `ConfigError` before any engine or HTTP
    resource opens. Use an env source and engine factory that record opens.
- A proxy test with `httpx.MockTransport` checks that PUT notification-settings forwards
  the bearer, drops caller service headers and streams the upstream status and body.
- `tests/integrations/*`: in split mode, the email test goes through `RemoteNotificationTester`;
  an unreachable notification service gives `configured=false`.
- `tests/e2e/test_wiring_cli.py`:
  - `migrate --store notification` without a URL exits 3;
  - `projections resync` in single process exits 2;
  - `notify test` in split mode exits 2;
  - (moved from `tests/inventory_service/test_config.py`, F3) the split-mode default
    `telescreen migrate` creates main owner tables only: a SQLite main file gets
    `main_database_tables()` and no inventory or notification-store-only tables.
- The existing `tests/e2e/test_wiring_v12/v13/v14`, `test_api_routes` and
  `test_route_permissions` pass in single process.
- `tests/issues/test_weekly_report.py` is green.

## Verification

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_builders.py src/telescreen/wiring_v12.py src/telescreen/wiring_v13.py src/telescreen/wiring_inventory_parts.py src/telescreen/wiring_notify_local.py src/telescreen/wiring_split.py src/telescreen/inventory_service/wiring.py src/telescreen/inventory_service/bureau.py src/telescreen/integrations/inventory_client.py src/telescreen/integrations/status.py src/telescreen/integrations/tests.py src/telescreen/issues/weekly_report.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e tests/integrations tests/issues/test_weekly_report.py
uv run --frozen ruff check --no-cache <same paths>
uv run --frozen ty check <same paths> tests/leak/flows_v12.py
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_wiring_split.py tests/e2e/test_wiring_v12.py tests/e2e/test_wiring_v13.py tests/e2e/test_wiring_v14.py tests/e2e/test_wiring_cli.py tests/e2e/test_cli_v12.py tests/e2e/test_cli_event_loop.py tests/e2e/test_route_permissions.py tests/e2e/test_route_permissions_v13.py tests/e2e/test_route_permissions_v14.py tests/e2e/test_api_routes.py tests/e2e/test_inventory_client.py tests/e2e/test_inventory_split_runtime.py tests/e2e/test_inventory_store_cutover.py tests/inventory_service/test_config.py tests/integrations tests/issues tests/test_package.py 2>&1 | tee /tmp/telescreen-svc-p06-composition-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p06-composition-typelint.log 2>&1; grep -E '^(src/telescreen/(wiring|integrations|issues/weekly_report|cli|inventory_service/(wiring|bureau))|tests/(e2e/test_wiring_split|integrations|issues/test_weekly_report))' /tmp/telescreen-svc-p06-composition-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p06-composition-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: the scoped commands exit 0 and the typelint grep is empty. Full-suite failures
are allowed only in `tests/inventory_service/**` (old inventory-only remote mode, rewritten
by svc-p04 and svc-p08) or in files owned by an in-flight wave-4 plan (svc-p04/svc-p05).
List every failing id in the Progress Log. A failure in any other file is this plan's to
fix, or a Reconciliation request if the file is outside `writePaths`.
`tests/e2e/test_inventory_store_cutover.py` (svc-p02) must be green in the scoped run.
So must the unowned `tests/inventory_service/test_config.py` (svc-p02, g6 sweep; do not
edit it): importing `telescreen.wiring` must load no `telescreen.inventory` or
`telescreen.wiring_v14` module (keep split and inventory imports lazy), and the real
`cli.main(["migrate", "--store", "inventory"])` still creates only inventory tables.

## Completion Criteria

- [ ] `grep -n "^from telescreen.notifications" src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/wiring_v13.py`
      shows only `notifications.dlp`/`notifications.guard`.
- [ ] The split import gate test passes. The single-process e2e suite is green.
- [ ] `src/telescreen/notifications/weekly.py` is gone and `issues/weekly_report.py` exists.
- [ ] `grep -rn "inventory_service.clients" src tests` prints nothing, and `clients.py` is
      deleted, or a Reconciliation request records why it is kept.
- [ ] The scoped verification exits 0. The Progress Log has hashes.

## Progress Log

(append sessions per conventions section 8)
