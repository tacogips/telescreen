# V13-P12 Wiring: Bind v1.3 Services, Ports, Providers and Routers

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 2 (ports, wiring), 15; design-docs/specs/architecture.md section 4 "v1.3 additions"
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p12-wiring`
- planPath: `impl-plans/active/v13-p12-wiring.md`
- wave: 4
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

Read `impl-plans/conventions.md` first.

## Intent and Context

The wave-2 and wave-3 plans left every new dependency defaulted to None or a no-op. This
plan binds the real implementations in `build_container`, registers every new router, and
proves the whole app with the route walk. It is the only plan that edits `wiring.py`.

Imitate:

- `src/telescreen/wiring_v12.py:build_v12`, `WiringV12Parts` and `WiringV12` (a separate
  module returning a parts dataclass; `wiring.py` only calls it);
- `wiring.py:DeferredIssueEventSink` (late binding of the event target);
- `tests/e2e/test_wiring_v12.py` and `tests/e2e/test_route_permissions.py` (container
  build with fakes, then the route walk).

## Non-goals

- No feature logic. A defect in a feature plan becomes a Reconciliation request; do not fix
  it in place.
- No CLI changes.

## Invariants

- Router order: every v1.3 router is placed BEFORE the issues router in
  `container.routers`, so that `GET /api/issues/counts` is not captured by
  `/api/issues/{issue_id}`.
- Every route of every router declares exactly one `PermissionDependency` or one external
  declaration. The external allow-list is unchanged from v1.2.
- `wiring.py` stays under 1000 lines (it is 756 today). New construction goes into
  `wiring_v13.py`.
- The issue event sink fans out to `WorkflowTriggerService` AND `AlertEvaluator`, in that
  order, in the same transaction. An exception in one propagates; do not swallow it.
- Offline build: transports default to real httpx only at runtime; tests inject
  `MockTransport`.

## Deliverables

1. **`wiring_v13.py`**: `WiringV13Parts` and `build_v13(...)`. It constructs:
   - from p04: `SavedViewService` and the `issue_counts` and `issue_views` routers;
   - from p05: `IssueService` new fields (`findings_observer`, `activity` and
     `archive_policy` from `settings.escalating_min_findings` and `escalating_factor`),
     `ArchiveMaintenanceProvider`, and the `issue_triage` router;
   - from p06: `BulkService`, `MergeService` and the `issue_bulk` router;
   - from p07:
     - `SubscriptionActivityNotifier`, bound as the `IssueService.activity` port;
     - `CommentService` via `IssuesRouterDeps.member_notifier`;
     - `GithubIssueCreator` (settings token ref and repos, `DlpOutboundGuard`);
     - the `issue_events`, `issue_activity` and `issue_external` routers;
   - from p08: `AlertRuleService`, `AlertEvaluator` (bound as
     `IssueService.findings_observer` and added to the event fan-out),
     `WorkflowRunStarter`, `AlertMaintenanceProvider`, and the `alerts` router;
   - from p09: `NotificationService` as `MemberNotifier`, `NotificationServiceTester`,
     `DlpOutboundGuard`, `WeeklyReportProvider` (`settings.weekly_report_cron`),
     `WeeklyReportHandler`, and the `notification_settings` router;
   - from p10: `DashboardService`, `IssueReportSourceImpl`, `IntegrationStatusService`,
     `IntegrationTester` (verifier from the v1.2 `ApiContext`), and the `dashboard` and
     `integrations` routers;
   - from p11: `DirectoryActorDirectory`, `OwnershipAssigneeSuggester`,
     `MemberAdminService` (with the issue remediation service), and the `ownership`
     router. The `directory` and `users` routers get their new deps.
2. **`wiring.py`**:
   - call `build_v13`;
   - pass `actors`, `suggester` and `member_notifier` into `IssuesRouterDeps`;
   - set `DeferredIssueEventSink.target` to `IssueEventFanout((workflow_triggers,
     alert_evaluator))`. Define `IssueEventFanout` in `wiring_v13.py` and widen the target
     type to `IssueEventSink | None`;
   - add the three schedule providers and the weekly task handler to the existing
     provider and handler tuples;
   - prepend the v13 routers to `routers`.
3. **`api/app.py`**: change only if router registration requires it (record the reason).
4. **Tests**:
   - `tests/e2e/test_api_routes.py`: add the new GET list paths to the admin-200 list:
     `/api/issues/counts`, `/api/views`, `/api/alert-rules`, `/api/dashboard`,
     `/api/integrations`, `/api/ownership-rules`, `/api/auth/me/notification-settings`
     and `/api/external-issues/github/repos`. These are called with a member-bound admin
     token, because some need a member. In the default offline configuration the GitHub
     repos route is unconfigured: it returns 200 `{configured: false, repos: []}`, not
     503. Only the create route returns 503 `integration_unconfigured`.
   - `tests/e2e/test_route_permissions.py`: no change unless the walk breaks. The legacy
     table stays.
   - `tests/e2e/test_route_permissions_v13.py`: a table of every v1.3 route (method, path,
     permission) taken from design-api-and-ui.md "v1.3 endpoints". It asserts that the
     container declares exactly these permissions and that every route has exactly one
     marker.
   - `tests/e2e/test_wiring_v13.py`:
     - the container has each v13 router, before the issues router;
     - `GET /api/issues/counts` returns 200 counts, not 404 `issue not found`;
     - the schedule providers include the archive, alert and weekly providers;
     - an `issue_created` event reaches both a workflow trigger and the alert evaluator
       (spy);
     - `IssueService.findings_observer` is the `AlertEvaluator`;
     - `IssueService.activity` is the `SubscriptionActivityNotifier`;
     - `IssuesRouterDeps.actors` is not None.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v13.py src/telescreen/api/app.py tests/e2e
uv run --frozen ruff check --no-cache src/telescreen tests/e2e
uv run --frozen ty check src/telescreen tests/e2e
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p12-typelint.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e 2>&1 | tee /tmp/telescreen-v13-p12-e2e.log; echo "exit=$?"
uv run --frozen ruff format --check . && uv run --frozen ruff check . && uv run --frozen ty check && uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p12-pytest-all.log 2>&1; echo "exit=$?"
uv build --out-dir /tmp/telescreen-v13-p12-dist > /tmp/telescreen-v13-p12-build.log 2>&1; echo "exit=$?"
```

Expected: every command exits 0. The full typelint and the full pytest pass at this point
(all backend plans are integrated).

## Completion Criteria

- [ ] Every v13 port is bound to its real implementation (asserted in
  `test_wiring_v13.py`).
- [ ] The v13 route table test and the full route walk pass.
- [ ] The full repository CI commands pass, and `wiring.py` is under 1000 lines.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
