# V13-P10 Dashboard Stats, Weekly Report Source, Integrations Status and Tests

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D30), 2, 10.2, 11, 12 (Integrations), 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p10-dashboard-integrations-backend`
- planPath: `impl-plans/active/v13-p10-dashboard-integrations-backend.md`
- wave: 3 (run 2; Last Updated 2026-10-13 with the run-2 shared-file rules)
- dependsOn: `v13-p01-foundation`, `v13-p02-db`, `v13-p04-issue-stream-backend` (reuses
  `issues/query/period.py:parse_period`; p04 runs in run 1 and this plan in run 2)
- writePaths:
  - `impl-plans/active/v13-p10-dashboard-integrations-backend.md`
  - `src/telescreen/issues/dashboard.py`
  - `src/telescreen/integrations`
  - `src/telescreen/api/routers/dashboard.py`
  - `src/telescreen/api/routers/integrations.py`
  - `tests/issues/v13_dashboard`
  - `tests/integrations`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Two pieces:

- the overview dashboard aggregates over issue and finding tables, plus the
  `IssueReportSource` implementation that the weekly report consumes;
- an integrations status page backend (slack, email, github_issues, oidc) with test
  actions. It returns only booleans, timestamps and enum codes.

Imitate:

- `api/routers/issues_list.py:issue_stats` (python bucketing that is portable to SQLite);
- `issues/digest.py` (issue queries over `OPEN_STATUSES`);
- `api/routers/users.py` (a small router with deps);
- `collectors/github/**` (httpx with MockTransport).

## Non-goals

- No collector status in `/api/integrations`. The UI uses `/api/collectors` for that
  (D30).
- No onboarding endpoint (D28, client-only).
- No wiring (v13-p12).

## Invariants

- "Open" means `UNRESOLVED_STATUSES`, and merged children are always excluded.
- Every mean is null when there is no data. Never divide by zero.
- `top_issues` returns `display_title`, which is the masked column; nothing else is
  textual.
- `/api/integrations` never returns a token, secret ref, host, address or URL other than
  `docs_path`, a relative README anchor.
- The GitHub test uses only `TELESCREEN_GITHUB_ISSUES_TOKEN` (resolved at use, never
  logged) and fixed timeouts.

## Deliverables

1. **`issues/dashboard.py`**:
   - `DashboardService.dashboard(session, period: Period, now) -> DashboardData`. The
     router parses the period with `issues/query/period.py:parse_period` (v13-p04), with
     the default `14d`.
   - Fields (design 11):
     - `buckets`: hourly for spans of 24 h or less, else daily. Severity counts are
       findings joined to their issue's current severity; `new_issues` counts
       `first_seen` in the bucket and `resolved_issues` counts `resolved_at` in it;
     - `open_by_exposure`, `open_by_source`, `archived_count`, `escalating_count`;
     - `top_issues`: the top 10 by findings in the period;
     - `mttr_seconds`: issues resolved in the period, mean of `resolved_at - first_seen`;
     - `mtta_seconds`: issues first seen in the period, time to the first history row of
       kind `status_changed`, `assigned` or `commented` whose `actor_ref` does not start
       with `system:`;
     - `unassigned_high_priority`.
   - `IssueReportSourceImpl` implements `IssueReportSource`:
     - `weekly_summary(session, start, end)` returns the new, resolved, regressed and
       escalating counts in the window (history kinds `regressed` and `escalated`), the
       open count, MTTR and the top 5;
     - `assigned_open_count(session, member_id)`.
2. **`integrations/` package**:
   - `status.py`: `IntegrationStatusService(settings, checks: IntegrationCheckRepository)`
     with `list(session) -> tuple[IntegrationStatus(kind, configured, last_check_at,
     last_check_ok, error_code, docs_path), ...]`. Configured means:
     - slack: the Slack bot token ref is set;
     - email: SMTP host and from-address are set;
     - github_issues: the token ref and at least one repo are set;
     - oidc: the issuer is set.
   - `tests.py`: `IntegrationTester(tester: NotificationTester, verifier: BearerVerifier |
     None, github_transport: httpx.AsyncBaseTransport | None, settings, env, checks,
     audit, clock)` with `run(kind, member_id) -> IntegrationCheckResult`.
     - slack and email -> `tester.send_test(channel, member_id)`;
     - github_issues -> `GET {api}/repos/{repo}` for each repo; ok when all return 200;
     - oidc -> `verifier.metadata()`;
     - unconfigured -> `INTEGRATION_UNCONFIGURED` (503);
     - the result is upserted into `integration_checks`, with audit `integration.tested`
       (kind and ok only).
   - `repository.py`: `IntegrationCheckRepository` with `get_all` and `upsert`.
3. **Routers**:
   - `dashboard.py`: `build_dashboard_router(deps)` with `GET /api/dashboard?period=...&start&end`
     (`ISSUES_READ`). The default period is `14d`.
   - `integrations.py`: `build_integrations_router(deps)` with `GET /api/integrations`
     (`INTEGRATIONS_READ`) and `POST /api/integrations/{kind}/test`
     (`INTEGRATIONS_TEST`). An unknown kind -> 404. A member principal is required for
     slack and email (403 otherwise).

## Tests

`tests/issues/v13_dashboard/` (seeded SQLite, FakeClock):

- 14d buckets: the counts per severity match the seeded findings; one issue resolved after
  48 h -> `mttr_seconds` 172800.
- MTTA ignores `system:` actors and counts a member assignment after 1 h -> 3600.
- Merged children are excluded from the open counts; an archived and an escalating issue
  are counted separately.
- An empty database -> null means and zero counts.
- `weekly_summary` over a 7-day window gives the expected counts and top issues ordered by
  findings. `assigned_open_count` excludes resolved issues.

`tests/integrations/`:

- Status flags for each configuration combination; the response JSON contains no token,
  host, email or secret-ref string (assert that specific configured values are absent).
- slack test with a fake tester ok -> `last_check_ok` true and an audit row.
- github test with MockTransport 200/200 -> ok; 200/404 -> not ok with `provider_error`.
- oidc with a fake verifier that raises `idp_unavailable` -> not ok.
- Unconfigured -> 503. A viewer cannot GET (403). An admin can.

## Run-2 shared-file rules (design section 15, run 2)

- Both routers are new, unregistered modules, tested by building them directly. Never
  use `include_router` in source. Route-walk files (`tests/e2e/test_route_permissions.py`,
  `tests/e2e/test_api_routes.py`, `tests/api/v12_rbac/**`) are read-only.
- No new table, migration or notification kind (`integration_checks` exists from
  v13-p02). `tests/db/test_models.py`, `tests/notifications/**` and `tests/leak/**` are
  read-only. Assert the absence of secrets and hosts in `tests/integrations`, and masked
  `display_title` only in `tests/issues/v13_dashboard`.
- Write nothing outside this plan's paths. `IssueReportSource` (`ports/issues.py`) and
  the weekly-report consumer (v13-p09) are read-only.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues/dashboard.py src/telescreen/integrations src/telescreen/api/routers/dashboard.py src/telescreen/api/routers/integrations.py tests/issues/v13_dashboard tests/integrations
uv run --frozen ruff check --no-cache src/telescreen/issues/dashboard.py src/telescreen/integrations src/telescreen/api/routers/dashboard.py src/telescreen/api/routers/integrations.py tests/issues/v13_dashboard tests/integrations
uv run --frozen ty check src/telescreen/issues/dashboard.py src/telescreen/integrations src/telescreen/api/routers/dashboard.py src/telescreen/api/routers/integrations.py tests/issues/v13_dashboard tests/integrations
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p10-typelint.log 2>&1; ! grep -E '^(src/telescreen/issues/dashboard[.]py|src/telescreen/integrations/|src/telescreen/api/routers/(dashboard|integrations)[.]py|tests/(issues/v13_dashboard|integrations)/)' /tmp/telescreen-v13-p10-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_dashboard tests/integrations tests/notifications/v13 2>&1 | tee /tmp/telescreen-v13-p10-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p10-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep finds nothing, and the weekly-report
tests in `tests/notifications/v13` still pass. The full run is informational.

## Completion Criteria

- [ ] `IssueReportSourceImpl` satisfies `IssueReportSource` (ty check).
- [ ] No secret or host in any integrations response (tested).
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
