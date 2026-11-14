# V13-P10 Dashboard Stats, Weekly Report Source, Integrations Status and Tests

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D30), 2, 10.2, 11, 12 (Integrations), 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

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
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p10-typelint.log 2>&1; ! grep -E '(/src/telescreen/issues/dashboard[.]py|/src/telescreen/integrations/|/src/telescreen/api/routers/(dashboard|integrations)[.]py|/tests/(issues/v13_dashboard|integrations)/|^src/telescreen/issues/dashboard[.]py|^src/telescreen/integrations/|^src/telescreen/api/routers/(dashboard|integrations)[.]py|^tests/(issues/v13_dashboard|integrations)/)' /tmp/telescreen-v13-p10-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_dashboard tests/integrations tests/notifications/v13 2>&1 | tee /tmp/telescreen-v13-p10-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p10-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep finds nothing, and the weekly-report
tests in `tests/notifications/v13` still pass. The full run is informational.

## Completion Criteria

- [x] `IssueReportSourceImpl` satisfies `IssueReportSource` (ty check).
- [x] No secret or host in any integrations response (tested).
- [x] The Progress Log has hashes and command evidence.

## Progress Log

### Session: 2026-10-13 03:07 JST
**Tasks Completed**: Implemented the S8 dashboard service and weekly `IssueReportSourceImpl`; added unregistered dashboard and integrations routers; implemented safe integration status, check persistence, audit and Slack/email/GitHub/OIDC test actions; added SQLite, provider, HTTP masking, permission and 503/403 tests.
**Tasks In Progress**: None for this plan.
**Blockers**: None.
**Reconciliation requests**: Re-run the repository-wide pytest and typelint checks after pending p06 bulk/merge changes are complete; the current informational full run reports three failures in `tests/issues/v13_bulk/test_bulk_merge.py` and the repository typelint test reports in-progress p06/p07 findings. These paths are outside p10 ownership.
**Hashes**: `src/telescreen/issues/dashboard.py` before=absent after=a4ef38d569e9692f748b847214fe8660781a5723ff4779966c7480179a61a891; `src/telescreen/integrations/__init__.py` before=absent after=fcca1abb7412799a8d6c17e79c16a92e238bad14b4584213969fe664d45facac; `src/telescreen/integrations/repository.py` before=absent after=65c92f232855bb034f29cd4086cefdb9b29ace4aed8e5223d2a858759900d1ce; `src/telescreen/integrations/status.py` before=absent after=b6b7d08940c1a623bc06cc63f49c302675c14e0b5bf86dbec6f54eace046f0b0; `src/telescreen/integrations/tests.py` before=absent after=bbb21bca5ce9bff3106875ee9f2145c6cc5a1f174ada95406d9b1cd2d9625823; `src/telescreen/api/routers/dashboard.py` before=absent after=8c9dacb4c85dd769677ece75f5b4df1bdffc10763558569466f36e8a1e8335cc; `src/telescreen/api/routers/integrations.py` before=absent after=3e0c471d48dd7244e857e78fb5027a6f29085a51a60f83a3064bde87984eab37; `tests/issues/v13_dashboard/test_dashboard.py` before=absent after=e45768a149c8bd3416c1c389c85fdb83f1c14beee010ac6191487daaa48181d0; `tests/integrations/test_integrations.py` before=absent after=be05167ef605dd0c8bc718d06c374777ef72533489a5cafd60edff485dcc89c5; `tests/integrations/test_router.py` before=absent after=fd82e22d43877a1c937b15d04d1c84a85322efad6d57ca8a286117146de440b1; plan before=1a7de33371edc3a5cc26a4dc5932a81cc827c15a96decf4ff06a8a38d0237477.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/issues/dashboard.py src/telescreen/integrations src/telescreen/api/routers/dashboard.py src/telescreen/api/routers/integrations.py tests/issues/v13_dashboard tests/integrations` -> exit 0 (log: `/tmp/telescreen-v13-p10-ruff-format-final.log`); scoped ruff check -> exit 0 (log: `/tmp/telescreen-v13-p10-ruff-check-final.log`); scoped `ty check` -> exit 0 (log: `/tmp/telescreen-v13-p10-ty-final.log`); whole-repo typelint filtered to p10 paths -> exit 0, no p10 findings (log: `/tmp/telescreen-v13-p10-typelint-final.log`, empty filtered output `/tmp/telescreen-v13-p10-typelint-filter.log`; underlying whole-repo typelint exits 1 on p06/p07 paths); `uv run --frozen pytest -p no:cacheprovider tests/issues/v13_dashboard tests/integrations tests/notifications/v13` -> exit 0, 27 passed (log: `/tmp/telescreen-v13-p10-pytest-final.log`); line count, no-router-registration and protected-path guards -> exit 0 (log: `/tmp/telescreen-v13-p10-wc-final.log` and command output); full `uv run --frozen pytest -p no:cacheprovider` -> exit 1, 965 passed, 8 skipped, 4 failed (three failures in p06 `tests/issues/v13_bulk/test_bulk_merge.py`, one repository typelint clean assertion due p06/p07 in-progress findings; log: `/tmp/telescreen-v13-p10-pytest-all.log`). Full run is informational during fanout per this plan.
**Notes**: New routers remain unregistered for v13-p12. Integrations status JSON includes only kind, configured, last check timestamp/success, enum error code and relative docs path; configured token values, token refs, host and email were asserted absent from actual HTTP response text. Regression suite includes merged-child exclusion, null empty means, MTTA system actor exclusion, weekly counts, GitHub 200/404, OIDC `idp_unavailable`, route permission count and viewer 403.

### Session: 2026-10-13 — TI-E3-P10-01 repair
**Finding**: Added the missing 14-day daily-bucket/MTTR assertions and weekly summary counts/order/assigned-open assertions in `tests/issues/v13_dashboard/test_dashboard.py`. Existing assertions remain unchanged. The production dashboard service required no change.
**Hashes**: dashboard test pre-repair=`e45768a149c8bd3416c1c389c85fdb83f1c14beee010ac6191487daaa48181d0`; final=`7af83123405f9c6c68c05acbffeb31a2c034c18327e6dc69cb98a75567362f7a`. Plan pre-edit=`01989daacf29606b7b49cce8ceb648f8915bb60174806eac8801e9bc077a0f98`; post-edit plan hash is recorded in `tmp/telescreen-v13-20261013-e3/p10/ti-e3-p10-01-final-hashes.txt`.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues/v13_dashboard tests/integrations tests/notifications/v13 2>&1 | tee /tmp/telescreen-v13-p10-ti-e3-p10-01-pytest.log` -> exit 0, 29 passed. Scoped `ruff format --check --no-cache` -> exit 0 (`/tmp/telescreen-v13-p10-ti-e3-p10-01-format.log`); scoped `ruff check --no-cache` -> exit 0 (`/tmp/telescreen-v13-p10-ti-e3-p10-01-ruff.log`); scoped `ty check` -> exit 0 (`/tmp/telescreen-v13-p10-ti-e3-p10-01-ty.log`); repository `tools/typelint.py` completed and the p10-path filter was empty -> exit 0 (`/tmp/telescreen-v13-p10-ti-e3-p10-01-typelint.log`). Dashboard-only rerun -> exit 0, 5 passed (`/tmp/telescreen-v13-p10-ti-e3-p10-01-dashboard-rerun.log`). An earlier dashboard-only attempt -> exit 1, 4 passed/1 failed because the new assertion used `ReportIssue.id`; corrected it to the DTO's `issue_id`, then reran successfully (`/tmp/telescreen-v13-p10-ti-e3-p10-01-dashboard.log`).
**Finding status**: TI-E3-P10-01 is implemented and locally verified; independent Opus review remains a downstream workflow decision.
