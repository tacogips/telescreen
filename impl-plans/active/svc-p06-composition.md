# SVC-P06 Telescreen Split Composition: Wiring, Proxies, Integrations, CLI, Weekly Move

**Status**: Implementation Complete; Test-Integrity Re-review Pending
**Design Reference**: design-docs/specs/design-service-split.md sections 3, 4.4, 5, 9 (public routes), 10, 14, 15, C5, C8
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

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
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p06-composition-typelint-final.log 2>&1; ! grep -E "^($(pwd)/)?(src/telescreen/(wiring|integrations|issues/weekly_report|cli|inventory_service/(wiring|bureau))|tests/(e2e/test_wiring_split|e2e/test_wiring_cli|e2e/test_inventory_client|integrations|issues/test_weekly_report))" /tmp/telescreen-svc-p06-composition-typelint-final.log
uv run --frozen pytest -p no:cacheprovider --continue-on-collection-errors > /tmp/telescreen-svc-p06-composition-pytest-all-final.log 2>&1; pytest_exit_code=$?; echo "pytest_exit_code=$pytest_exit_code"; exit "$pytest_exit_code"
```

Evidence: scoped commands exit 0; the corrected typelint grep accepts absolute or relative
repository paths, matches an absolute-path sanity sample, and finds no p06 diagnostics. Full-suite failures
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

- [x] `grep -n "^from telescreen.notifications" src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/wiring_v13.py`
      shows only `notifications.dlp`/`notifications.guard`.
- [x] The split import gate test passes. The single-process e2e suite is green.
- [x] `src/telescreen/notifications/weekly.py` is gone and `issues/weekly_report.py` exists.
- [x] `grep -rn "inventory_service.clients" src tests` prints nothing, and `clients.py` is
      deleted, or a Reconciliation request records why it is kept.
- [x] The scoped verification exits 0. The Progress Log has hashes.

## Progress Log

(append sessions per conventions section 8)

### Session 1 — implementation and scoped verification (2026-10-16)

- Implemented local and split notification composition, lazy local notification imports,
  notification proxy routes, inventory command client relocation, integration channel
  status delegation, weekly report move, and notification CLI commands. Added scoped
  regressions for split import/secret boundaries, proxy/client behavior, channel status,
  and CLI store/resync/test-send behavior.
- Current-source gates: Ruff format exit 0 (`/tmp/telescreen-svc-p06-composition-format-final.log`);
  Ruff check exit 0 (`/tmp/telescreen-svc-p06-composition-ruff-final.log`); ty exit 0
  (`/tmp/telescreen-svc-p06-composition-ty-final.log`); typelint target filter exit 0
  (`/tmp/telescreen-svc-p06-composition-typelint.log`); scoped pytest exit 0, 216 passed,
  0 failed (`/tmp/telescreen-svc-p06-composition-pytest-final.log`). The scoped run
  includes the unedited `tests/inventory_service/test_config.py`.
- Static gates exit 0: notification implementation import allowlist; weekly source move;
  no `inventory_service.clients` references; `wiring.py` below 1000 lines.
- Informational wave-B full-suite command `uv run --frozen pytest -p no:cacheprovider`
  exited 2 during collection at `tests/inventory_service/test_postgres.py`: the in-flight
  svc-p04 `tests/inventory_service/conftest.py` no longer exports `RemoteWorld`. This is
  an explicitly informational `tests/inventory_service/**` failure for this plan; the
  scoped config test passed. Full-suite log: `/tmp/telescreen-svc-p06-composition-pytest-all.log`.
- Earlier attempts were resolved on final source: focused pytest collection failed on the
  stale weekly test import (`/tmp/telescreen-svc-p06-initial-pytest.log`); the first focused
  behavioral run had 3 failures and 16 passes (`/tmp/telescreen-svc-p06-focused-pytest.log`)
  due to split Slack-ref resolution, a missing test import and router ordering; that suite
  then passed 19/19 (`/tmp/telescreen-svc-p06-focused-rerun.log`). Initial Ruff and ty
  findings were fixed and their final scoped gates pass as recorded above.
- Source SHA-256 values below compare the immutable fanout snapshot (`before`) with the
  final implementation source (`after`). `absent` denotes a new/deleted file.

| Path | Before | After |
| --- | --- | --- |
| `src/telescreen/cli.py` | `4edd5ca24324971c422a9b37d615037b3523f7dbb3f06e6e70d1d48eb8b7c358` | `69aad5c5ff090b20b73d5ebd23e7c23adae8db77197c67efd2c50b2aa1345500` |
| `src/telescreen/cli_v12.py` | `d143ea0cf2064303a364bfb1ced14a775b6736e7af9c3c635650e5a13314145d` | `64d7ba90e08301aabfa64bec65934136e9a07b69533afa40e4fbcb07804b5e5f` |
| `src/telescreen/integrations/status.py` | `b6b7d08940c1a623bc06cc63f49c302675c14e0b5bf86dbec6f54eace046f0b0` | `9e82f255f7c631f479b7b0e94ea2ab89dfa769463384abaee6b8d0d7aa5de276` |
| `src/telescreen/integrations/tests.py` | `bbb21bca5ce9bff3106875ee9f2145c6cc5a1f174ada95406d9b1cd2d9625823` | `34847e4bd3cabbea8f799547b2d53410b6da95dbe7f9a56a4ba227a7e4514bb4` |
| `src/telescreen/integrations/inventory_client.py` | absent | `ddf5d9e5ed08093500dddeb3b1e5d6982fec5a6eea23fe117efc705b98fb8910` |
| `src/telescreen/inventory_service/bureau.py` | `f302326ab995df4ebe3dc58ec00b5334cbfc5b4e3ae220980c3172fdbae063cb` | `da31cf127902f4e27e51f7c737d0519309617620deedb647b2d6e6d365480463` |
| `src/telescreen/inventory_service/clients.py` | `6f1ea6dc1b490ab543224a7e63cad195b5b2ad0cc3e0788af859cf3e6125d4a3` | absent |
| `src/telescreen/inventory_service/wiring.py` | `028d04419ea5546f61af19f9cc4e3b204e052743f6294773dc6646fc21cc4e86` | `e54db00a1f9dd94f88dc035d7c979b5f420e3620f0aeefc7e5f5f6850743c44c` |
| `src/telescreen/notifications/weekly.py` | `f2c5e49b7c7e85d014863f87492dfbc0b03a58237b038f06e502334abacfc678` | absent |
| `src/telescreen/issues/weekly_report.py` | absent | `f2c5e49b7c7e85d014863f87492dfbc0b03a58237b038f06e502334abacfc678` |
| `src/telescreen/wiring.py` | `63b9ea1264b221fbcec9492bb3ab64476996a7cebcd30dfb6215e7156d0642cd` | `77d70f622f0cac540b61ed560e5522c6bb4e89448a467f71582c8922c7eb2636` |
| `src/telescreen/wiring_inventory_parts.py` | `92fa024f78a5889288d46905aaa704443065b6b592b2a1f090c803d3361cd05a` | `862d9bc0c9b0b72c0dd27c1fd5164ff656cbe8b6123046807159b1c3d0420020` |
| `src/telescreen/wiring_notify_local.py` | absent | `73956eb4f68ca445d3bfdad4a6f1995d00b90c5bb57200e2486788e3049fa75a` |
| `src/telescreen/wiring_split.py` | absent | `a1c2946cac1bda3f5886d55d0552adedd7abfdfb9bd62c719b9cda3b637cdd89` |
| `src/telescreen/wiring_v12.py` | `a817c45e7f5c94a1d3786e1f9879a46bbc0b03fb71dc159161de30c4425499bf` | `7cce68d4d6f8ccace710ba4cccf3ba4bfaf251e28d7e53f19a9b072134f83f65` |
| `src/telescreen/wiring_v13.py` | `539a47bbe53bde20cd5509635874b50fef9bdd5acc49dc8f009e5fcbb3cd3297` | `f7066692e651fb68628df77ab84a3a51eb8ef062333af1d983bfbd8aa9af3f70` |
| `tests/e2e/test_inventory_client.py` | absent | `6fbee24551029d3ba32bb89a4c411caf317836be82c50a5c845eae7ecc544143` |
| `tests/e2e/test_wiring_split.py` | absent | `3730ff1e3ad6a9e947fc797e4591e934be1744551a73339f29b6489341dd87f0` |
| `tests/e2e/test_wiring_cli.py` | `b83c9f6800236f64b5b0af6e00cf718e3bc19207c0d445ad51399a787c2d3a95` | `545ae06c2438726fd813872876508c83b0b69e1192e1178e7e2649722af15b3f` |
| `tests/e2e/test_wiring_v13.py` | `adc054251e432b9c04d161edfd2194333a86b5e6790ceb6f8835d001a0004caf` | `c10ee7d5511eb3de098191462ff06cb7d83e8b6e6d3f4c5f2ae3b127ad1b52c9` |
| `tests/integrations/test_integrations.py` | `be05167ef605dd0c8bc718d06c374777ef72533489a5cafd60edff485dcc89c5` | `8399b9e2b8e974d25dc5602e31490d1c7862e6ce5abaae672aae157ed431f759` |
| `tests/issues/test_weekly_report.py` | absent | `6ef4bbd62ddd1e0b9ab4dcd4e8cc946a63a6d82047475721efe6c6a7c9311a84` |
| `tests/notifications/v13/test_weekly.py` | `bb390b60741c9c13268e8bce0efd9ad4f041407419b8b4086685fc1298784bd2` | absent |

### Session 2 — test-integrity repairs (2026-10-16)

- Addressed `p06-tic-F1`: declared `selected_url: str | None` in `src/telescreen/cli.py`;
  replaced the typelint grep with the absolute-or-relative path pattern shown above. The
  grep matched the absolute sample recorded in
  `tmp/telescreen-svc-20261016-g6/svc-p06-composition/typelint-grep-sanity.log`; the final
  typelint run reports `782 file(s) clean` and has no p06-path match
  (`/tmp/telescreen-svc-p06-composition-typelint-final.log`).
- Addressed `p06-tic-F2`: `tests/e2e/test_wiring_split.py` now checks all four proxy paths
  are Bureau proxy endpoints, the command-delivery/no-op/relay handlers are registered,
  an outbox row and idempotent engine task commit together with redacted content, a member
  display-name update writes projection markers, and both invalid topologies fail before
  primitives, env reads or HTTP use. `tests/integrations/test_integrations.py` now runs
  the actual `RemoteNotificationTester` email path through `MockTransport`.
- Addressed `p06-tic-F3`: `tests/e2e/test_wiring_cli.py` asserts split default migration
  creates exactly `main_database_tables()` plus `alembic_version_main`. The four legacy
  notification tables remain in that accepted main schema.
- Addressed `p06-tic-F4`: full suite with collection continuation exited 1, with 1377
  passed, 3 failed and 1 collection error. Every failure is attributed to in-flight p04:
  `tests/inventory/core/test_import_boundaries.py::test_other_business_modules_cannot_import_inventory_sql_models`
  (p04 `inventory_service/dispatch.py` imports inventory models);
  `tests/inventory_service/test_process.py::test_two_processes_use_only_their_owned_database`;
  `tests/inventory_service/test_split_inventory.py::test_inventory_cli_and_app_import_without_telescreen_composition`;
  collection error `tests/inventory_service/test_postgres.py` cannot import `RemoteWorld`
  from p04's changing `tests/inventory_service/conftest.py`. Full log:
  `/tmp/telescreen-svc-p06-composition-pytest-all-final.log`. `tests/tools/test_typelint.py`
  passed; no p06-owned failure remains.
- Regression sensitivity review: removing the notification proxy router fails the four
  path assertions; removing any required handler fails its instance assertion; removing
  the producer binding/outbox enqueue fails row/task assertions; removing the marker
  listener fails the update marker assertion; allowing resource construction before
  topology checks fails the patched primitive spy; bypassing RemoteNotificationTester
  fails the expected `/channels` and `/test` requests; changing default split migration
  ownership fails the exact table-set assertion.
- Resolved attempts retained: the first CLI rerun had 5 passes and 1 failure because the
  assertion omitted four intentionally retained tables (`/tmp/telescreen-svc-p06-wiring-cli-rerun.log`);
  the correction to `main_database_tables()` passes in the final CLI run. The first
  collection-continuing full-suite attempt completed with the same 3 p04 failures and 1
  p04 collection error, but its zsh wrapper used the reserved variable `status`; a second
  run with `pytest_exit_code` captured the actual exit and complete results
  (`/tmp/telescreen-svc-p06-composition-pytest-all-rerun.log`, then final log above).
- Final reruns: focused split and integration tests 13 passed
  (`/tmp/telescreen-svc-p06-split-tests-rerun.log`); CLI tests 6 passed
  (`/tmp/telescreen-svc-p06-wiring-cli-rerun-final.log`); complete assigned scoped suite
  220 passed (`/tmp/telescreen-svc-p06-composition-pytest-review-rerun.log`). Format,
  Ruff and ty gates exited 0 with logs `/tmp/telescreen-svc-p06-composition-format-review-rerun.log`,
  `/tmp/telescreen-svc-p06-composition-ruff-review-rerun.log` and
  `/tmp/telescreen-svc-p06-composition-ty-review-rerun.log`.
