# V13-P08 Alert Rules Backend, RunStarter, Enriched Workflows List

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D18-D21), 2, 9, 10.3, 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v13-p08-alerts-backend`
- planPath: `impl-plans/active/v13-p08-alerts-backend.md`
- wave: 2 (run 2; Last Updated 2026-10-13 with the run-2 shared-file rules)
- dependsOn: `v13-p01-foundation`, `v13-p02-db`
- writePaths:
  - `impl-plans/active/v13-p08-alerts-backend.md`
  - `src/telescreen/alerts`
  - `src/telescreen/workflow/triggers`
  - `src/telescreen/api/routers/workflows.py`
  - `src/telescreen/api/routers/alerts.py`
  - `tests/alerts`
  - `tests/workflow/v13`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Alert rules are first-class objects:

- triggers: new issue, regression, escalating, frequency, distinct values;
- filters and actions: notify targets and start a workflow;
- a per-issue action interval, mute, and fire history.

The rules are evaluated inside the ingest and lifecycle transaction, independently of
workflow triggers. A rule must not start a workflow that the same event already
triggers.

Imitate:

- `workflow/triggers/service.py:WorkflowTriggerService.on_issue_event` and `_matches`
  (filters and the active-run check);
- `workflow/dsl/notification_sets.py:validate_targets` and
  `validate_recipient_for_channels` (reuse them for targets; `telescreen.alerts` is L3
  and may import `telescreen.workflow.dsl`, which is L1);
- `notifications/templates/service.py` (CRUD, versions, audit);
- `api/routers/notification_templates.py` (router style, validation errors as `{field,
  code}`).

## Non-goals

- No notification rendering or settings logic: send `NotificationRequest(event=ALERT_FIRED,
  category=ALERTS, ...)` through the `NotificationSender` port.
- No wiring and no fan-out sink (v13-p12).
- No UI.

## Invariants

- `telescreen.alerts` imports only L0, L1 (db models, ports, workflow.dsl) and L2. It never
  imports `telescreen.issues` or `telescreen.notifications`.
- It reads issue and finding rows through the ORM (read-only) for counts and group
  membership, and writes only `alert_rules` and `alert_fires`.
- Muted and interval-suppressed fires: at most one `muted` or `suppressed_interval` row per
  (rule, issue) per interval window (D21).
- Archived issues and merged children never match `frequency` or `values`. A child's event
  is evaluated against its primary (`issues.merged_into_id`).
- The rule name passes `Redactor.has_hits` (422 `raw_value_detected`, no row).
- Validation errors carry field paths and codes only, never values.

## Deliverables

1. **`alerts/model.py`**: frozen dataclasses:
   - `AlertTrigger(kind: AlertTriggerKind, threshold: int | None, window_minutes: int |
     None)`;
   - `AlertFilters(min_severity: Severity | None, priorities, sources, rule_classes,
     exposures, owner_teams, detectors)` (tuples);
   - `AlertActions(targets: tuple[NotificationTargetSpec, ...], start_workflow: str |
     None)`;
   - `AlertRule(...)`.

   Add `to_json` and `from_json` through `telescreen.core.json` (canonical) for
   `filters_json` and `actions_json`.
2. **`alerts/validate.py`**: `validate_rule(rule, catalog: TemplateCatalog | None,
   starter: RunStarter | None, session) -> tuple[RuleIssue(field, code), ...]`:
   - name 1-64 characters and `Redactor`;
   - `frequency` and `values` need a threshold of 1-100000 and a window of 5-10080;
   - interval 5-43200;
   - at least one action;
   - at most 10 targets, each checked with `validate_targets`;
   - a target template must exist with event kind `alert_fired`;
   - `start_workflow` must have an active version (`trigger_events` not None), else
     `unknown_workflow`;
   - D19: an event-trigger rule whose kind is in `trigger_events(...)` ->
     `duplicate_trigger`;
   - `muted_until` at most 90 days ahead.
3. **`alerts/repository.py`** and **`alerts/service.py`**: `AlertRuleService(redactor,
   audit, clock, catalog, starter)` with `list`, `get`, `create`, `update`, `delete`,
   `enable`, `disable`, `mute(minutes)`, `unmute` and `history(rule_id, cursor)`. Audit
   `alert_rule.created`, `.updated`, `.deleted`, `.muted` and `.unmuted` (id and name
   only). A duplicate name -> 409.
4. **`alerts/evaluator.py`**: `AlertEvaluator(sender: NotificationSender, starter:
   RunStarter, issues: IssueReader, clock)` implements `IssueEventSink.on_issue_event`
   and `FindingsObserver.on_findings_recorded`.
   1. Resolve the primary.
   2. Load the enabled rules whose trigger matches. Event kinds map 1:1 to the trigger
      kinds `issue_created`, `issue_regressed` and `issue_escalating`. `frequency` counts
      the group findings in the window; `values` counts their distinct fingerprints.
   3. Apply the filters to the `IssueSnapshot` (`owner_teams` uses
      `snapshot.assignee_team_slug`).
   4. Decide the outcome in this order: muted -> interval -> act.
   5. Act:
      - one `NotificationRequest` per target and channel, with `category=ALERTS`, event
        `ALERT_FIRED`, `set_name=None` and `record_unreachable=True`;
      - the context fields `alert_rule`, `alert_trigger`, `alert_count` and
        `alert_window` plus the masked issue fields;
      - `starter.start_for_issue(session, name, primary_id, RunTrigger.ALERT_RULE)`.
   6. Write an `AlertFireRow` with outcome:
      - `delivered` when at least one notification is queued or a run starts;
      - `blocked` when every receipt is DLP-blocked;
      - `failed` with the `error_code` on a caught `TelescreenError`.

      Update the rule's `last_fired_at` on `delivered`.
5. **`alerts/maintenance.py`**: `AlertMaintenanceProvider(ScheduleProvider)` deletes fires
   older than 90 days, at most once a day.
6. **`workflow/triggers/starter.py`**: `WorkflowRunStarter(engine, repository)`
   implements `RunStarter`:
   - `start_for_issue` starts the active definition with the `WorkflowTriggerService`
     active-run check, and returns the run id or None;
   - `trigger_events` returns the active definition's `trigger.on`, or None.
7. **`api/routers/alerts.py`**: `build_alerts_router(deps: AlertsRouterDeps)` with the
   routes and permissions of design 9:
   - GET routes need `ALERTS_READ`;
   - mute and unmute need `ALERTS_MUTE`;
   - the rest need `ALERTS_WRITE`.

   List items add `last_fired_at`, `fires_24h` and `status` (`enabled`, `disabled` or
   `muted`, plus `muted_until`).
8. **`api/routers/workflows.py`**: list items add `trigger_events`, `active`,
   `last_run_at` and `runs_30d {running, waiting, succeeded, failed, cancelled}`.
   `WorkflowsRouterDeps` gains only defaulted fields, and no route is added.

## Tests (`tests/alerts/`, `tests/workflow/v13/`)

- Validation:
  - `frequency` without a threshold -> `{field: "/trigger/threshold", code}`;
  - 11 targets -> error;
  - `email:` recipient with the slack channel -> `undeliverable_combination`;
  - a template of a different event kind -> `template_event_mismatch`;
  - `start_workflow` of a workflow triggered by `issue_created`, on an `issue_created`
    rule -> `duplicate_trigger`;
  - a name with a sample secret -> 422 and no row.
- Evaluator (fake sender and starter; FakeClock):
  - `issue_created` matching -> one `delivered` fire, requests with category alerts;
  - a second event within 60 min -> a `suppressed_interval` fire, and a third within the
    same window adds no row;
  - after 61 min -> `delivered` again;
  - muted -> a `muted` fire and no request;
  - disabled -> no fire.
- Frequency 5 in 10 min: 4 findings -> no fire; 5 -> fire. An archived issue -> no fire.
  Findings on a merged child count for its primary.
- Filters: `min_severity` high excludes medium; `owner_teams` matches the assignee team
  slug.
- All requests blocked (fake receipts) -> outcome `blocked`; the starter raises ->
  `failed` with the code.
- Maintenance: a 91-day-old fire is deleted and an 89-day-old fire is kept.
- `WorkflowRunStarter`: no active version -> None; an active run for the issue -> None;
  `trigger_events` returns `('issue_created',)`.
- Router: permissions per route (a viewer cannot POST; a responder can mute but not
  create); history paging.
- Workflows list: `runs_30d` counts seeded runs; `active` reflects the active version.

## Run-2 shared-file rules (design section 15, run 2)

- `alerts.py` is a new, unregistered router module. Its tests build it directly with
  `build_alerts_router`. p12 (run 3) registers it by copying `APIRoute` objects; never use
  `include_router` in source.
- `workflows.py` is already registered. Add NO route there: only list-item fields and
  defaulted `WorkflowsRouterDeps` fields. Then `tests/e2e/test_route_permissions.py`,
  `tests/e2e/test_api_routes.py` and `tests/api/v12_rbac/**` stay read-only and must keep
  passing.
- `WorkflowTriggerService` keeps its public behavior. `starter.py` reuses its active-run
  check without changing `on_issue_event`. The existing tests that use it
  (`tests/workflow/engine/test_engine_extended.py`, `tests/workflow/engine/
  test_resume_gaps.py`, `tests/core/test_v11_foundation.py`) and the `/api/workflows`
  flows (`tests/leak/flows.py`, `tests/leak/flows_v12.py`,
  `tests/e2e/test_flow_v12_notifications.py`, `tests/examples/test_examples.py`) are
  read-only and must pass unchanged.
- No new table, migration, template variable or notification event kind. `alert_fired`
  and the `alerts` category already exist (v13-p01), so `tests/db/test_models.py`,
  `tests/notifications/**` and `tests/leak/**` are read-only.
- Masking: alert rule names pass `Redactor`, and fire rows and notification contexts hold
  masked issue fields only. Assert it in `tests/alerts` with `tests.support.samples`: the
  raw sample is absent from `alert_rules`, `alert_fires`, the queued requests and every
  response.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13
uv run --frozen ruff check --no-cache src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13
uv run --frozen ty check src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p08-typelint.log 2>&1; ! grep -E '^(src/telescreen/(alerts|workflow/triggers)/|src/telescreen/api/routers/(alerts|workflows)[.]py|tests/(alerts|workflow/v13)/)' /tmp/telescreen-v13-p08-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/alerts tests/workflow tests/core/test_v11_foundation.py 2>&1 | tee /tmp/telescreen-v13-p08-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py tests/e2e/test_api_routes.py tests/api/v12_rbac tests/e2e/test_flow_v12_notifications.py tests/examples/test_examples.py tests/leak/test_leak_matrix.py tests/leak/test_leak_matrix_v12.py 2>&1 | tee /tmp/telescreen-v13-p08-pytest-shared.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p08-pytest-all.log 2>&1; echo "exit=$?"
```

Expected:

- the scoped checks exit 0, and the typelint grep finds nothing;
- the existing workflow tests and the shared read-only suites pass;
- `git diff --name-only HEAD -- pyproject.toml uv.lock src/telescreen/db tests/db
  tests/leak tests/notifications tests/e2e tests/examples tests/workflow/engine tests/core
  tests/api/v12_rbac/rbac_world.py tools/typelint.py` is empty. Sibling v13-p11 edits
  `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`; ignore that path.

The full run is informational. A failure caused only by v13-p11's in-progress files is
recorded as such.

## Completion Criteria

- [x] `AlertEvaluator` satisfies `IssueEventSink` and `FindingsObserver`, and
  `WorkflowRunStarter` satisfies `RunStarter` (ty check).
- [x] The D19, D21 and archived-issue exclusions are tested.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-13 16:20
**Tasks Completed**: Implemented alert models, validation, persistence, rule service, event evaluator, retention maintenance, RunStarter adapter, alerts API router and enriched workflow list. Added regression tests for validation/masking, D19 duplicate triggers, D21 interval suppression, archived and merged-child exclusions, blocked/failed outcomes, retention and router permissions.
**Tasks In Progress**: Shared route inventory and repository-wide typing verification need reconciliation with p11's in-progress directory changes.
**Blockers**: The shared suite fails because `POST /api/members/{handle}/deactivate` has no expected scope in `tests/e2e/test_route_permissions.py`. Full pytest also reports the v12 router inventory expected 16 routes but found 19, plus 12 p11 TL003 annotations in directory and ownership files. These paths are outside p08 writePaths.
**Reconciliation requests**: Reconcile p11 route ownership and inventory in `tests/e2e/test_route_permissions.py` and `tests/api/v12_routers/test_routers.py`, and resolve the p11 TL003 findings in `src/telescreen/directory/actors.py`, `tests/directory/v13/test_directory_members_tokens.py` and `tests/issues/v13_ownership/test_ownership_rules.py`. Then rerun the p08 shared suite and full repository pytest. Evidence: `/tmp/telescreen-v13-p08-pytest-shared.log`, `/tmp/telescreen-v13-p08-pytest-all.log` and `/tmp/telescreen-v13-p08-typelint.log`.
**Hashes**: `impl-plans/active/v13-p08-alerts-backend.md` before=e1d10cc74613ce17600c502474b9dda51d656b6c7df29503a0787f541c33d3d6 after=recorded in `tmp/telescreen-v13-20261013-e3/p08/final-hashes.txt`; `src/telescreen/api/routers/workflows.py` before=dd84a33ca09c2f95df8c1963968b6a274415efbf38553c9f44e60b89c98b5fcf after=1e7cb67f298e9a4e0b6af2a7c89cca7ad7177965423826337f8fdf97543592af; new p08 files were absent before and their final hashes are recorded in `tmp/telescreen-v13-20261013-e3/p08/final-hashes.txt`.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13` -> exit 0 (log: `/tmp/telescreen-v13-p08-format.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13` -> exit 0 (log: `/tmp/telescreen-v13-p08-ruff.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/alerts src/telescreen/workflow/triggers src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py tests/alerts tests/workflow/v13` -> exit 0 (log: `/tmp/telescreen-v13-p08-ty.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p08-typelint.log 2>&1; ! grep -E '^(src/telescreen/(alerts|workflow/triggers)/|src/telescreen/api/routers/(alerts|workflows)[.]py|tests/(alerts|workflow/v13)/)' /tmp/telescreen-v13-p08-typelint.log` -> exit 0; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/alerts tests/workflow tests/core/test_v11_foundation.py 2>&1 | tee /tmp/telescreen-v13-p08-pytest.log` -> exit 0, 193 passed / 1 skipped (log: `/tmp/telescreen-v13-p08-pytest.log`); `wc -l src/telescreen/alerts/*.py src/telescreen/workflow/triggers/*.py src/telescreen/api/routers/alerts.py src/telescreen/api/routers/workflows.py > /tmp/telescreen-v13-p08-wc.log; ! awk '$2 != "total" && $1 >= 1000' /tmp/telescreen-v13-p08-wc.log | grep -q .` -> exit 0; `test -z "$(git diff --name-only HEAD -- pyproject.toml uv.lock src/telescreen/db tests/db tests/leak tests/notifications tests/e2e tests/examples tests/workflow/engine tests/core tests/api/v12_rbac/rbac_world.py tools/typelint.py)"` -> exit 0; `! rg -n 'telescreen\.(issues|notifications)' src/telescreen/alerts` -> exit 0. Shared read-only suite command from the Verification section above -> exit 1, 35 passed / 1 failed (log: `/tmp/telescreen-v13-p08-pytest-shared.log`). `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p08-pytest-all.log 2>&1; rc=$?; tail -n 80 /tmp/telescreen-v13-p08-pytest-all.log; echo exit=$rc; exit $rc` -> exit 1, 944 passed / 8 skipped / 3 failed (log: `/tmp/telescreen-v13-p08-pytest-all.log`).
**Notes**: Scoped Ruff, ty and typelint checks pass. Shared failures are attributable to p11's in-progress directory routes and strict typing work; p08 cannot edit the required route-inventory or p11-owned files. Resume after p11 or serial integration reconciles those paths and rerun both failed suites. No router registration was added. Full final source hashes are in `tmp/telescreen-v13-20261013-e3/p08/final-hashes.txt`.

### Serial reconciliation (session 13, run 2, after the wave-2 join)

- Owner: the serial reconcile-implementations writer (not a Luna worker). Pre and post SHA-256 of every touched file: `tmp/telescreen-v13-20261013-e3/reconcile/pre-hashes.txt`, `tmp/telescreen-v13-20261013-e3/reconcile/post-hashes.txt`.
- Combined tree: `uv run --frozen ruff format --check --no-cache .`, `ruff check --no-cache .`, `python tools/typelint.py` (489 files clean), `ty check` -> exit 0; `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 947 passed / 8 skipped (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-all.log`); `uv build` -> exit 0; web install/typecheck/test (223 passed)/build/screenshots -> exit 0, 0 browser errors, no `state-skipped` lines (`tmp/telescreen-v13-20261013-e3/reconcile/web-*.log`).
- p08 shared suite rerun -> exit 0, 36 passed (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-p08-shared.log`). The p11 blockers recorded above are resolved; no p08 file was changed.

### Session: 2026-10-13 02:22 JST (INT-E3-P08-01 review repair)
**Tasks Completed**: Fixed D21 interval anchoring: only the latest `delivered` fire starts `action_interval_minutes`; the repository checks for one `suppressed_interval` row since that delivery. Muted cadence is separate and anchored to the latest `muted` row. Added `test_d21_interval_is_anchored_on_latest_delivery` for t0 delivered, t50 suppressed, t61 delivered, t62 suppressed, t70 no additional row. Existing `test_d21_only_one_interval_suppression_row_is_written` is unchanged.
**Tasks In Progress**: None for p08 implementation. Independent review and later workflow integration remain downstream.
**Blockers**: None.
**Reconciliation requests**: None. The scope-audit command below found `tests/e2e/test_route_permissions.py` changed by serial p11 route-inventory reconciliation; p08 did not edit it, and the shared suite passes. All other audited protected paths are unchanged.
**Hashes**: `src/telescreen/alerts/evaluator.py` before=2f85866c0d14740e7458e96d7865f1ffba48641abbdab696f93c3d4298c2c4b2 after=2b23cc03ed5cefe4c2fd6b359d7da7e24a0498b1ff48edfa807b92b096e5d923; `src/telescreen/alerts/repository.py` before=a4832e67c605dd9ca651c95e1f924d102239b89fda51f06be8449d3fc7a8296d after=d1703e1a5f99e98bb4fd334bfb1f755d75469da6dd0fa1a349f87a8f9fcf324f; `tests/alerts/test_alerts.py` before=2a0d77a0becd1ce94d7e90bc79f0412950b5e04990d21e8f7cc321548610ed9e after=df155ae8b768c76155fadfaaaa3782cb57017066080b536189cd159a6a4c9bb7; this plan before=63bfdb4fdec4a9eee9cf99e256a5538ce258f72a3a88c336aab38b08bddb436f (post-edit hash recorded in final-hashes file).
**Verification**: Focused D21 tests -> exit 0, 2 passed (`/tmp/telescreen-v13-p08-int-e3-p08-01-focused.log`); scoped Ruff format -> exit 0 (`/tmp/telescreen-v13-p08-int-e3-p08-01-format.log`); scoped Ruff check -> exit 0 (`/tmp/telescreen-v13-p08-int-e3-p08-01-ruff.log`); scoped `ty check` -> exit 0 (`/tmp/telescreen-v13-p08-int-e3-p08-01-ty.log`); whole-repository typelint filtered to p08 paths -> exit 0, no p08 findings (`/tmp/telescreen-v13-p08-int-e3-p08-01-typelint.log`); `uv run --frozen pytest -p no:cacheprovider tests/alerts tests/workflow tests/core/test_v11_foundation.py` -> exit 0, 194 passed / 1 expected integration skip (`/tmp/telescreen-v13-p08-int-e3-p08-01-pytest.log`); required read-only shared suite -> exit 0, 36 passed (`/tmp/telescreen-v13-p08-int-e3-p08-01-pytest-shared.log`); Python file-size check -> exit 0 (`/tmp/telescreen-v13-p08-int-e3-p08-01-wc.log`). A first format check returned 1 for Ruff's suggested wrapping; applied that formatting and the final logged format check passed. `git diff --name-only HEAD -- pyproject.toml uv.lock src/telescreen/db tests/db tests/leak tests/notifications tests/e2e tests/examples tests/workflow/engine tests/core tests/api/v12_rbac/rbac_world.py tools/typelint.py` -> exit 1, sole path `tests/e2e/test_route_permissions.py` (`/tmp/telescreen-v13-p08-int-e3-p08-01-protected-path-audit.log`); serial p11 permission-inventory reconciliation owns this path, p08 did not edit it, and the shared suite passes.
**Notes**: Review finding `INT-E3-P08-01` is addressed in `evaluator.py` and `repository.py`. The regression test would fail under the reviewed behavior: the t50 suppression can no longer move the t0 delivery anchor, so t61 delivers. All p08 completion criteria remain checked.

### Session: 2026-10-13 02:27 JST (TI-E3-P08-02 test-integrity repair)
**Tasks Completed**: Added `test_d21_muted_rule_writes_one_muted_row_per_window_and_sends_nothing`. It asserts one muted row and zero requests at t0 and t+1m, a second muted row and zero requests at t+61m, then a delivered row and one request after expiring the mute. Both existing D21 tests are unchanged.
**Tasks In Progress**: None for p08 implementation. Independent review remains downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/alerts/test_alerts.py` before= df155ae8b768c76155fadfaaaa3782cb57017066080b536189cd159a6a4c9bb7 after=541ca7dd0b571a370b7a7d031d95084a4442a8760730438946922fcce52953a; this plan before=648ae244ae5588af23c40dcfc657dcaa871d01af1b15da9fb20ff1bc3969e895 (post-edit hash recorded in `tmp/telescreen-v13-20261013-e3/p08/step6-ti-e3-p08-02/final-hashes.txt`).
**Verification**: `uv run --frozen pytest -p no:cacheprovider tests/alerts/test_alerts.py -k 'd21'` -> exit 0, 3 passed / 10 deselected (`/tmp/telescreen-v13-p08-ti-02-focused.log`); scoped Ruff format -> exit 0 (`/tmp/telescreen-v13-p08-ti-02-format.log`); scoped Ruff check -> exit 0 (`/tmp/telescreen-v13-p08-ti-02-ruff.log`); scoped `ty check` -> exit 0 (`/tmp/telescreen-v13-p08-ti-02-ty.log`); filtered whole-repository typelint -> exit 0, no p08 findings (`/tmp/telescreen-v13-p08-ti-02-typelint.log`); p08 pytest -> exit 0, 195 passed / 1 expected integration skip (`/tmp/telescreen-v13-p08-ti-02-pytest.log`); shared suite -> exit 0, 36 passed (`/tmp/telescreen-v13-p08-ti-02-pytest-shared.log`).
**Notes**: Review finding `TI-E3-P08-02` is addressed by the named muted evaluator test. The test confirms the documented muted-row cadence and that muted outcomes do not become delivered interval anchors. Plan status remains Completed and all completion criteria remain checked.
