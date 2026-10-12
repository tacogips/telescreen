# V13-P08 Alert Rules Backend, RunStarter, Enriched Workflows List

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D18-D21), 2, 9, 10.3, 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

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

- [ ] `AlertEvaluator` satisfies `IssueEventSink` and `FindingsObserver`, and
  `WorkflowRunStarter` satisfies `RunStarter` (ty check).
- [ ] The D19, D21 and archived-issue exclusions are tested.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
