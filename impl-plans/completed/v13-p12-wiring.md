# V13-P12 Wiring: Bind v1.3 Services, Ports, Providers and Routers

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 2 (ports, wiring), 15 (incl. "Run 3" notes, wave B); design-docs/specs/design-api-and-ui.md "v1.3 endpoints"; design-docs/specs/architecture.md section 4 "v1.3 additions"
**Created**: 2026-10-12
**Last Updated**: 2026-10-13 (run 3, manifest telescreen-v13-20261013-e4, wave B)

## Plan Metadata

- planId: `v13-p12-wiring`
- planPath: `impl-plans/active/v13-p12-wiring.md`
- wave: 4 (run 3 wave B; runs alone, after wave A = p07 re-verify)
- dependsOn: `v13-p04-issue-stream-backend`, `v13-p05-triage-lifecycle-backend`, `v13-p06-bulk-merge-backend`, `v13-p07-issue-detail-backend`, `v13-p08-alerts-backend`, `v13-p09-notifications-backend`, `v13-p10-dashboard-integrations-backend`, `v13-p11-directory-ownership-backend`
- writePaths:
  - `impl-plans/active/v13-p12-wiring.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_v13.py`
  - `src/telescreen/api/app.py`
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_route_permissions.py`
  - `tests/e2e/test_wiring_v13.py`
  - `tests/e2e/test_route_permissions_v13.py`
- sharedPaths: none
- artifactRoots: none
- read-only (must pass unchanged; a failure is a Reconciliation request, never an edit):
  `tests/api/v12_rbac`, `tests/db/test_models.py`, `tests/leak`, `tests/notifications`,
  `tests/examples`, `tests/e2e/harness.py`, every other `tests/e2e/test_*.py`, all feature
  code under `src/telescreen` other than the files above, `pyproject.toml`, `uv.lock`,
  `tools/typelint.py`.

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

The wave-2 and wave-3 plans left every new dependency defaulted to None or a no-op and
every new router unregistered. This plan binds the real implementations in
`build_container`, registers every v1.3 router, and proves the whole app with the route
walk. It is the only plan that edits `wiring.py`. All feature plans are accepted; do not
change their behavior.

Imitate:

- `src/telescreen/wiring_v12.py:build_v12`, `WiringV12Parts`, `WiringV12` (a separate
  module returning a parts dataclass; `wiring.py` only calls it and stores it on
  `Container.v12`);
- `src/telescreen/wiring.py:DeferredIssueEventSink` (late binding of the event target);
- `src/telescreen/api/routers/issues.py:build_issues_router` (composes sub-routers by
  copying `APIRoute` objects);
- `tests/e2e/test_wiring_v12.py`, `tests/e2e/test_route_permissions.py`,
  `tests/e2e/test_api_routes.py` (container build with fakes, then the walk).

## Components to bind (all exist at `ca818c3`; only `IssueEventFanout` is new)

| Source plan | Class (module) | Binding |
|---|---|---|
| p04 | `SavedViewService` (`issues/views.py`) | `build_issue_counts_router(IssueCountsRouterDeps)`, `build_issue_views_router(IssueViewsRouterDeps)` |
| p05 | `ArchiveMaintenanceProvider` (`issues/archive.py`) | schedule provider; `IssueService` fields `findings_observer`, `activity`, `archive_policy` (from `settings.escalating_min_findings`, `settings.escalating_factor`); `build_issue_triage_router(IssueTriageRouterDeps)` |
| p06 | `BulkService` (`issues/bulk.py`), `MergeService` (`issues/merge.py`) | `build_issue_bulk_router(IssueBulkRouterDeps)` |
| p07 | `SubscriptionActivityNotifier` (`issues/activity/notifier.py`), `CommentService` (`issues/activity/comments.py`), `GithubIssueCreator` (`issues/external/github.py`) | `IssueService.activity`; `IssuesRouterDeps.member_notifier`; `build_issue_events_router(IssuesRouterDeps)`, `build_issue_activity_router(IssuesRouterDeps)`, `build_issue_external_router(IssueExternalRouterDeps)` |
| p08 | `AlertRuleService` (`alerts/service.py`), `AlertEvaluator` (`alerts/evaluator.py`), `WorkflowRunStarter` (`workflow/triggers/starter.py`), `AlertMaintenanceProvider` (`alerts/maintenance.py`) | `IssueService.findings_observer`; event fan-out; schedule provider; `build_alerts_router(AlertsRouterDeps)` |
| p09 | notification service as `MemberNotifier`, `NotificationServiceTester` (`notifications/tester.py`), `DlpOutboundGuard` (`notifications/guard.py`), `WeeklyReportProvider`, `WeeklyReportHandler` (`notifications/weekly.py`) | schedule provider (`settings.weekly_report_cron`); task handler; `build_notification_settings_router(NotificationSettingsRouterDeps)` |
| p10 | `DashboardService`, `IssueReportSourceImpl` (`issues/dashboard.py`), `IntegrationStatusService` (`integrations/status.py`), `IntegrationTester` (`integrations/tests.py`) | `build_dashboard_router(DashboardRouterDeps)`, `build_integrations_router(IntegrationsRouterDeps)`; the weekly report uses `IssueReportSourceImpl` |
| p11 | `DirectoryActorDirectory` (`directory/actors.py`), `OwnershipAssigneeSuggester` (`issues/ownership.py`), `MemberAdminService` (`directory/members.py`) | `IssuesRouterDeps.actors`, `.suggester`; `build_ownership_router(OwnershipRouterDeps)`; new deps of the already-registered `directory` and `users` routers |

Read each class's constructor and each `<Area>RouterDeps` before wiring it. Pass the
existing primitives (`audit`, `clock`, `redactor`, `fingerprinter`, sessions, outbound
`http`) already built in `build_container`; never build a second instance of a primitive.

## Non-goals

- No feature logic. A defect in a feature plan becomes a Reconciliation request in this
  Progress Log naming the owning plan and file; do not fix it in place.
- No CLI changes, no new route, no new permission, no schema or migration change.
- No edit to router modules, services, `tests/api/v12_rbac/**` or any test outside
  writePaths.

## Invariants

- `build_container(settings, env, http, sessions=None, clock=None)` keeps its exact
  signature and return type; all 16 existing test call sites keep working.
- `Container` gains exactly one field `v13: WiringV13` (like `v12`); `wiring.py:718` is the
  only constructor call.
- Router order: every v1.3 router is placed BEFORE the issues router in
  `container.routers`, so `GET /api/issues/counts` and `POST /api/issues/bulk` are not
  captured by `/api/issues/{issue_id}` routes.
- Composition copies `APIRoute` objects (as `issues.py` does) or registers whole routers
  in `container.routers`. Never call `include_router` in wiring code: FastAPI 0.142 keeps
  an included router as a lazy `_IncludedRouter` the walk cannot see.
- Every route of every registered router declares exactly one `PermissionDependency` or one
  `ExternalAuthDeclaration`. The external allow-list is unchanged from v1.2.
- `wiring.py` stays under 1000 lines (756 today); new construction goes into
  `wiring_v13.py` (also under 1000).
- Event fan-out: `DeferredIssueEventSink.target` becomes
  `IssueEventFanout((workflow_triggers, alert_evaluator))`; it calls the sinks in that
  order in the same session; an exception in one propagates (do not catch). Widen the
  target type to `IssueEventSink | None`. Pin:
  `class IssueEventFanout(IssueEventSink): sinks: tuple[IssueEventSink, ...]` (frozen
  dataclass in `wiring_v13.py`).
- Offline: tests inject `httpx.MockTransport`; no network. GitHub issues unconfigured in
  the default settings.
- Strict typing (no Any/object/dict/**kwargs/ignores); every declaration typed.

## Deliverables

1. **`src/telescreen/wiring_v13.py`**: `IssueEventFanout`, `WiringV13Parts` (inputs from
   `build_container`), `WiringV13` (built services, providers, handlers and the ordered
   router tuple), and `build_v13(parts: WiringV13Parts) -> WiringV13` constructing the
   table above.
2. **`src/telescreen/wiring.py`**: call `build_v13`; pass `actors`, `suggester` and
   `member_notifier` into `IssuesRouterDeps`; set the `IssueService` fields; set
   `DeferredIssueEventSink.target`; append `ArchiveMaintenanceProvider`,
   `AlertMaintenanceProvider` and `WeeklyReportProvider` to the provider tuple and
   `WeeklyReportHandler` to the handler tuple; prepend the v13 routers to `routers`; give
   `directory`/`users` routers their new deps; store `v13` on `Container`.
3. **`src/telescreen/api/app.py`**: change only if registration requires it; record why.
4. **Tests**:
   - `tests/e2e/test_api_routes.py`: add to the admin-200 GET list `/api/issues/counts`,
     `/api/views`, `/api/alert-rules`, `/api/dashboard`, `/api/integrations`,
     `/api/ownership-rules`, `/api/auth/me/notification-settings`,
     `/api/external-issues/github/repos`. Routes that need a member principal use a second
     admin token bound to the created member (`TokenService.create(..., member_id)`).
     The GitHub repos route returns 200 `{configured: false, repos: []}` offline.
   - `tests/e2e/test_route_permissions.py`: no change unless the walk breaks; the legacy
     table stays.
   - `tests/e2e/test_route_permissions_v13.py`: a table of every v1.3 route (method, path,
     permission) taken from design-api-and-ui.md "v1.3 endpoints". It asserts that the
     built container declares exactly these permissions and that every route has exactly
     one marker. Walk `container.routers` with the same `APIRoute`/`_IncludedRouter`
     handling as `test_route_permissions.py`.
   - `tests/e2e/test_wiring_v13.py` cases:
     - container -> each v13 router present, every one before the issues router;
     - `GET /api/issues/counts` with an admin token -> 200 counts body, not 404;
     - `POST /api/issues/bulk` with an empty `ids` list -> not a 404/405 from the issues router;
     - schedule providers include archive, alert and weekly providers; handlers include
       the weekly handler;
     - an `issue_created` event -> reaches a spy workflow trigger and the alert
       evaluator, in that order; an exception in the first propagates;
     - `IssueService.findings_observer` is the `AlertEvaluator`;
       `IssueService.activity` is the `SubscriptionActivityNotifier`;
       `IssuesRouterDeps.actors` and `.suggester` are not None;
     - `grep include_router` in `wiring.py`/`wiring_v13.py` prints nothing (done criterion).

## Pitfalls

- If a v13 router is registered after the issues router, `/api/issues/counts` returns 404
  "issue not found". Test it.
- Do not construct a second `IssueService`, `AuditLog` or notification service; reuse the
  ones `build_container` already builds, or alert and activity paths diverge.
- `settings.weekly_report_cron` is `str | None` (`None` = disabled, from an empty
  `TELESCREEN_WEEKLY_REPORT_CRON`); with `None` the provider must enqueue nothing and not
  crash. Read `notifications/weekly.py:WeeklyReportProvider` for how it handles that.
- Do not edit `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`: it filters to its
  fixed `MODULES` tuple, so new modules do not enter it. If it fails, the cause is a
  changed v1.2 route; that is a defect to report.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v13.py src/telescreen/api/app.py tests/e2e 2>&1 | tee /tmp/telescreen-v13-p12-format.log
uv run --frozen ruff check --no-cache src/telescreen tests/e2e 2>&1 | tee /tmp/telescreen-v13-p12-ruff.log
uv run --frozen ty check src/telescreen tests/e2e 2>&1 | tee /tmp/telescreen-v13-p12-ty.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p12-typelint.log 2>&1
uv run --frozen pytest -p no:cacheprovider tests/e2e tests/api/v12_rbac 2>&1 | tee /tmp/telescreen-v13-p12-e2e.log
uv run --frozen ruff format --check . && uv run --frozen ruff check . && uv run --frozen ty check && uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p12-pytest-all.log 2>&1
uv build --out-dir /tmp/telescreen-v13-p12-dist > /tmp/telescreen-v13-p12-build.log 2>&1
! grep -n include_router src/telescreen/wiring.py src/telescreen/wiring_v13.py
test "$(wc -l < src/telescreen/wiring.py)" -lt 1000 && test "$(wc -l < src/telescreen/wiring_v13.py)" -lt 1000
test -z "$(git diff --name-only HEAD -- src/telescreen | grep -v -E '^src/telescreen/(wiring[.]py|wiring_v13[.]py|api/app[.]py)$')"
test -z "$(git diff --name-only HEAD -- tests | grep -v -E '^tests/e2e/(test_api_routes|test_route_permissions|test_wiring_v13|test_route_permissions_v13)[.]py$')"
test -z "$(git diff --name-only HEAD -- pyproject.toml uv.lock tools/typelint.py src/web)"
```

Expected: every command exits 0. The full pytest shows more than the 988 passed baseline
with 8 skipped and 0 failed; the typelint log reports no violation.

## Completion Criteria

- [x] Every v13 port and router is bound to its real implementation (asserted in
  `test_wiring_v13.py`).
- [x] The v13 route table test and the full route walk pass; `tests/api/v12_rbac` passes
  unchanged.
- [x] The full repository CI commands pass; `wiring.py` and `wiring_v13.py` are under 1000
  lines; no `include_router` in wiring code.
- [x] The Progress Log has per-edit pre/post SHA-256, every command with exit status and
  log path, and any Reconciliation request.

## Progress Log

### Session: 2026-10-13 04:41
**Tasks Completed**: Bound all assigned v1.3 services and routers through `build_v13`; wired ordered issue-event fan-out, archive policy, actor/suggester/member notifier deps, providers, weekly handler, directory member administration, and v1.3 routers before the issues router. Added v1.3 route permission and wiring tests; extended admin/member-bound API route smoke coverage. Corrected the plan scope guards to include its declared `wiring_v13.py` and new test paths.
**Tasks In Progress**: None for p12. Formal review and serial finalization are downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/wiring.py` before=1aa86c2e29ee9c8d9daea31f637ff9f1d44c0cf2a4339a6ab3fc1979bdd2bc83 after=d7ce4b2eb6edbcdc973af28057fbcd98ba22054f35463fb410b65db355bf5c35; `src/telescreen/wiring_v13.py` before=absent after=539a47bbe53bde20cd5509635874b50fef9bdd5acc49dc8f009e5fcbb3cd3297; `tests/e2e/test_api_routes.py` before=dbba51619427bd582eac78599b92dda932f8b99acef6e146465543fa9d64ada2 after=01a3178d7815b50b985bcd238844728323f423e11cc3e46cdc3f83b7b903284b; `tests/e2e/test_wiring_v13.py` before=absent after=adc054251e432b9c04d161edfd2194333a86b5e6790ceb6f8835d001a0004caf; `tests/e2e/test_route_permissions_v13.py` before=absent after=daf53b9195b5068d52d64a70170ca1dc5af0803c92e2fefaa34cda7d2af00b44; plan before=7a3bed30335047d53ba25d25921c30f1784c1cbf61498cb939cd70d5153c41a2 after recorded in `tmp/telescreen-v13-20261013-e4/p12/session1-final-hashes.txt`.
**Verification**: `uv run --frozen ruff format --check --no-cache .` -> exit 0, 529 files formatted (log: `/tmp/telescreen-v13-p12-format.log`); `uv run --frozen ruff check --no-cache .` -> exit 0 (log: `/tmp/telescreen-v13-p12-ruff.log`); `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v13-p12-ty.log`); `uv run --frozen python tools/typelint.py` -> exit 0, 528 files clean (log: `/tmp/telescreen-v13-p12-typelint.log`); `uv run --frozen pytest -p no:cacheprovider tests/e2e tests/api/v12_rbac` -> exit 0, 50 passed (log: `/tmp/telescreen-v13-p12-e2e.log`); `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 991 passed / 8 skipped / 0 failed (log: `/tmp/telescreen-v13-p12-pytest-all.log`); `uv build --out-dir /tmp/telescreen-v13-p12-dist` -> exit 0 (log: `/tmp/telescreen-v13-p12-build.log`); p12 route-order, file-size, no-include_router and scope guards -> exit 0 (log: `/tmp/telescreen-v13-p12-guards.log`).
**Notes**: The 8 skipped tests are offline database/Meilisearch integrations or live-service checks and did not run. `src/telescreen/api/app.py` required no change because it already registers each container router. Earlier focused attempts exposed and corrected the member role fixture and HTTP method filtering in the new table test; final scoped and full suites pass.
