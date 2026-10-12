# V13-P01 Foundation: Vocabulary, Permissions, Settings, Ports, Issues Router Split

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D1, D2, D5, D16, D22), 2, 15; design-docs/specs/design-api-and-ui.md "v1.3 endpoints"
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p01-foundation`
- planPath: `impl-plans/active/v13-p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v13-p01-foundation.md`
  - `src/telescreen/core/vocabulary.py`
  - `src/telescreen/core/permissions.py`
  - `src/telescreen/core/config.py`
  - `src/telescreen/ports/issues.py`
  - `src/telescreen/ports/notifications.py`
  - `src/telescreen/ports/workflow.py`
  - `src/telescreen/ports/directory.py`
  - `src/telescreen/issues/transitions.py`
  - `src/telescreen/api/routers/issues.py`
  - `src/telescreen/api/routers/issues_common.py`
  - `src/telescreen/api/routers/issues_list.py`
  - `src/telescreen/api/routers/issues_detail.py`
  - `src/telescreen/api/routers/issues_actions.py`
  - `src/telescreen/api/routers/issues_comments.py`
  - `src/telescreen/notifications/templates/builtins.py`
  - `src/telescreen/notifications/templates/variables.py`
  - `src/telescreen/notifications/templates/synthetic.py`
  - `tests/core/v13`
  - `tests/ports/v13`
  - `tests/core/test_v11_foundation.py`
  - `tests/core/v12/test_permissions_and_oidc_settings.py`
  - `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`
- sharedPaths: none
- artifactRoots: none

Ownership handoffs (explicit; wave 1 to wave 2):

- `issues_common.py` and `issues_list.py` go to v13-p04, `issues_actions.py` to v13-p05,
  and `issues_detail.py` and `issues_comments.py` to v13-p07.
- `notifications/templates/{builtins,variables,synthetic}.py` go to v13-p09.
- `tests/api/v12_rbac/test_v12_rbac_router_permissions.py` goes to v13-p11.
- `issues.py` (the composer) stays with p01 only. No later plan edits it.

Read `impl-plans/conventions.md` first.

## Intent and Context

Pin every shared v1.3 contract in wave 1, so that the wave-2 plans can run side by side:

- enums;
- permissions and their role sets;
- settings;
- port DTOs and Protocols;
- a mechanical split of the 851-line `api/routers/issues.py` into one module per later owner.

Behavior today must not change: all existing tests stay green.

Imitate:

- `core/vocabulary.py`, the style of the existing StrEnums and the `OPEN_STATUSES` tuple at
  :690;
- `core/permissions.py`, the `READ_PERMISSIONS` and `WRITE_PERMISSIONS` set algebra;
- `core/config.py:_digest_cron`, for cron validation, and `_optional` / `_integer`;
- `ports/notifications.py:NotificationContext`, where every field is defaulted to `None`.

## Non-goals

- No DB columns or models (v13-p02). The split code must not reference new `IssueRow`
  columns.
- No new routes, and no new fields in any response.
- No wiring change. `wiring.py` keeps calling `build_issues_router(IssuesRouterDeps(...))`.

## Invariants

- Every existing test passes unchanged, except the three owned test files listed above. In
  those, only the assertions this plan's changes break are edited.
- New Protocol methods go into NEW Protocols. Never add a method to an existing Protocol:
  existing test fakes implement `NotificationSender` and `IssueEventSink`
  structurally, and a new method would break them. That is why `MemberNotifier` is a
  separate Protocol; the design's `NotificationSender.send_to_member` maps to it.
- Every new dataclass field on an existing DTO has a default.
- `telescreen.api.routers.issues` still exports `IssuesRouterDeps` and
  `build_issues_router`. All routes keep their paths, methods, permissions and response
  bytes.

## Deliverables

1. **`core/vocabulary.py`**, with the exact values of design section 2:
   - `IssueStatus`: `ARCHIVED = "archived"`, `ESCALATING = "escalating"`.
   - `IssueHistoryKind`: archived, unarchived, escalated, priority_changed, reviewed, merged,
     unmerged, comment_edited, comment_deleted, external_issue_linked,
     external_issue_unlinked.
   - `IssueEventKind` and `RunTrigger`: `issue_escalating`. `RunTrigger` also gets
     `alert_rule`.
   - `EngineTaskKind`: `WEEKLY_REPORT = "weekly_report"`.
   - New enums: `IssuePriority` (high, medium, low), `ArchiveKind`, `AlertTriggerKind`,
     `AlertFireOutcome`, `NotificationCategory`, `NotificationPreference` (email, slack,
     off), `SavedViewVisibility`, `SubscriptionReason`, `ExternalIssueKind`,
     `IntegrationKind`, `SuggestionReason` (owner, ownership_rule, policy_team), and
     `ActorDisplayKind` (member, team, token, system, unknown).
   - `NotificationEvent`: alert_fired, issue_assigned, issue_mentioned, issue_activity,
     weekly_report, integration_test.
   - `ErrorCode`: invalid_query, recipient_opted_out, merged_group, dlp_blocked,
     integration_unconfigured, duplicate_trigger, limit_exceeded.
   - Status sets:
     - new `UNRESOLVED_STATUSES = (UNRESOLVED, ACKNOWLEDGED, IN_PROGRESS, REGRESSED,
       ESCALATING)`;
     - `OPEN_STATUSES` appends `ESCALATING, ARCHIVED` after its current members, keeping
       the order;
     - `RECONCILED_STATUSES` keeps its formula.
   - New helper `priority_for_severity(s: Severity) -> IssuePriority`: critical and high
     map to high, medium maps to medium.
2. **`issues/transitions.py`**: `USER_TRANSITIONS` adds `ESCALATING -> (ACKNOWLEDGED,
   IN_PROGRESS, UNRESOLVED)` and `ARCHIVED -> (UNRESOLVED,)`.
3. **`core/permissions.py`**: new `Permission` members with the values `views.read`,
   `views.write`, `views.manage_shared`, `issues.follow`, `comments.manage_any`,
   `external_issues.write`, `alerts.read`, `alerts.write`, `alerts.mute`, `profile.self`,
   `integrations.read`, `integrations.test`, `ownership.read`, `ownership.write`. Sets:
   - `READ_PERMISSIONS` also excludes `INTEGRATIONS_READ` (admin only) and adds
     `VIEWS_WRITE`, `ISSUES_FOLLOW` and `PROFILE_SELF`;
   - `WRITE_PERMISSIONS` adds `VIEWS_MANAGE_SHARED`, `EXTERNAL_ISSUES_WRITE` and
     `ALERTS_MUTE`;
   - the admin set stays "everything except machine".

   Check the result against the matrix in design section 2.
4. **`core/config.py`**: new `TelescreenSettings` fields with defaults:
   - `escalating_min_findings: int = 10`, read from `TELESCREEN_ESCALATING_MIN_FINDINGS`,
     range 1-100000;
   - `escalating_factor: int = 3` (1-100);
   - `weekly_report_cron: str | None = "0 9 * * 1"`. An empty value means None; an invalid
     value raises `ConfigError("TELESCREEN_WEEKLY_REPORT_CRON is invalid")`, as
     `_digest_cron` does;
   - `github_issues_token_ref: str | None = None`;
   - `github_issues_repos: tuple[str, ...] = ()`. Comma split, strip, pattern
     `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`, at most 20; a violation raises `ConfigError` with
     a static text.
5. **Ports** (frozen dataclasses and Protocols). The signatures are contracts:
   - `ports/issues.py`:
     - `FindingsRecorded(issue_id, inserted: int, observed_at)` and Protocol
       `FindingsObserver.on_findings_recorded(session, event) -> None`;
     - `IssueActivity(kind: IssueHistoryKind, issue_id, actor_ref, at,
       assignee_member_id: str | None = None, comment_id: str | None = None, from_value:
       str | None = None, to_value: str | None = None)` and Protocol
       `IssueActivityNotifier.on_activity(session, activity) -> None`;
     - `ReportIssue(issue_id, short_id, display_title, findings: int)`;
       `WeeklySummary(start, end, new_count, resolved_count, regressed_count,
       escalating_count, open_count, mttr_seconds: int | None, top_issues:
       tuple[ReportIssue, ...])`; Protocol `IssueReportSource` with
       `weekly_summary(session, start, end) -> WeeklySummary` and
       `assigned_open_count(session, member_id) -> int`;
     - `SuggestedAssignee(ref, reason: SuggestionReason)` and Protocol
       `AssigneeSuggester.suggest(session, issue_id) -> tuple[SuggestedAssignee, ...]`;
     - `IssueSnapshot` gains `priority: IssuePriority | None = None` and
       `assignee_team_slug: str | None = None`;
     - no-op classes `NoopFindingsObserver` and `NoopIssueActivityNotifier` (frozen
       dataclasses whose methods return None).
   - `ports/notifications.py`:
     - `NotificationRequest.category: NotificationCategory | None = None`;
     - `NotificationContext` adds `alert_rule`, `alert_trigger`, `alert_count`,
       `alert_window`, `activity_kind`, `activity_actor`, `comment_excerpt`,
       `report_summary` and `report_assigned_open` (all `str | None = None`);
     - `MemberNotification(member_id, category, event, context, issue_id: str | None =
       None)` and Protocol `MemberNotifier.send_to_member(session, notification) ->
       NotificationReceipt`;
     - `TestOutcome(ok: bool, error_code: ErrorCode | None, notification_id: str | None)`
       and Protocol `NotificationTester.send_test(channel: NotificationChannel, member_id:
       str) -> TestOutcome` (no session: it opens its own transactions);
     - `GuardVerdict(allowed: bool, hit_count: int)` and Protocol
       `OutboundContentGuard.inspect(text: str) -> GuardVerdict` (async).
   - `ports/workflow.py`: Protocol `RunStarter` with `start_for_issue(session,
     workflow_name, issue_id, trigger: RunTrigger) -> str | None` and
     `trigger_events(session, workflow_name) -> tuple[IssueEventKind, ...] | None`.
   - `ports/directory.py`: `ActorDisplay(ref, kind: ActorDisplayKind, display_name, handle:
     str | None, active: bool)` and Protocol `ActorDirectory.display(session, refs:
     tuple[str, ...]) -> tuple[ActorDisplay, ...]`. The result has the same length and
     order as `refs`; v13-p11 implements it. Ref grammar (document it in the docstring):
     - `member:<x>` resolves by member id, then by handle;
     - `team:<x>` resolves by team id, then by slug;
     - `token:<id>` is the token label;
     - `system:*` is "Telescreen";
     - anything else is kind `unknown`, display "Unknown".
6. **Issue router split** (mechanical). Move code from `api/routers/issues.py` as follows:
   - `issues_common.py`: the `IssuesRouterDeps` dataclass itself (moved here so that
     the sub-modules import it without a cycle; `issues.py` re-exports it with `from
     telescreen.api.routers.issues_common import IssuesRouterDeps` so existing imports keep
     working), and the helpers. Request bodies move to the module that uses them:
     `IssuePatchBody`, `RemediationBody`, `ResolveBody` and `ExceptionRequestBody` go to
     `issues_actions.py`, and `CommentBody` goes to `issues_comments.py`, so that their
     later owners can extend them. The helpers are `_response`, `_cursor`, `_optional_string`, `_issue_json`,
     `_issue_detail_json`, `_safe_exposure`, `_timestamp`, `_unavailable`,
     `_invalid_transition`, `_finding_json`, `_history_json`, `_bucket_start`;
   - `issues_list.py`: `GET /api/issues` and `GET /api/issues/stats`;
   - `issues_detail.py`: `GET /api/issues/{issue_id}`, `/events`, `/history`,
     `/occurrences`;
   - `issues_actions.py`: PATCH, `/remediation`, `/resolve`, `/release`, `/exceptions`;
   - `issues_comments.py`: `POST /api/issues/{issue_id}/comments`.

   Rules:
   - Each sub-module exposes `build_issues_<part>_router(deps: IssuesRouterDeps) ->
     APIRouter`.
   - `IssuesRouterDeps` gains `actors: ActorDirectory | None = None`, `suggester:
     AssigneeSuggester | None = None` and `member_notifier: MemberNotifier | None = None`
     (used by v13-p07's comment route for mentions). After p01, no plan adds fields to it. In `issues.py`,
     `build_issues_router(deps)` creates one `APIRouter` and calls `include_router` on the
     five sub-routers. `GET /api/issues/stats` must stay registered BEFORE
     `/api/issues/{issue_id}`.
   - **Serializer contract pin** (no field change): in `issues_common.py` define the frozen
     dataclass `IssueItemExtras(assignee: ActorDisplay | None, merged_count: int,
     bookmarked: bool)`. Change the signature to `_issue_json(issue: IssueRow, extras:
     IssueItemExtras | None = None) -> JsonValue`, where `extras` is accepted and ignored
     in p01. v13-p04 emits the fields; v13-p07 passes extras for the detail route.
   - Keep each module under 1000 lines.
7. **Notification built-ins for the six new events** (keeps the v12 parametrized template
   tests green):
   - `builtins.py`: `system.<event>` texts.
     - `alert_fired`: `Telescreen alert: {{ alert.rule }} {{ issue.short_id }}` /
       `{{ issue.title }}\n{{ alert.trigger }} {{ alert.count }} {{ alert.window }}\n{{
       issue.link }}`.
     - `issue_assigned`: `Assigned to you: {{ issue.short_id }}` / `{{ issue.title }}\nby
       {{ activity.actor }}\n{{ issue.link }}`.
     - `issue_mentioned`: `{{ activity.actor }} mentioned you on {{ issue.short_id }}` /
       `{{ comment.excerpt }}\n{{ issue.link }}`.
     - `issue_activity`: `{{ issue.short_id }}: {{ activity.kind }}` / `{{ activity.actor
       }}\n{{ comment.excerpt }}\n{{ issue.link }}`.
     - `weekly_report`: `Telescreen weekly report` / `{{ report.summary }}\nAssigned to
       you and open: {{ report.assigned_open }}`.
     - `integration_test`: `Telescreen test notification` / `Test from {{ health.target
       }}`.
   - `variables.py`: map the new paths to the new context fields, and add the event groups
     of design 10.3.
   - `synthetic.py`: a synthetic context per new event, with reserved example values only.

## Tests (new files under `tests/core/v13/` and `tests/ports/v13/`)

- `UNRESOLVED_STATUSES` -> exactly the five statuses; `OPEN_STATUSES` contains `ARCHIVED`
  and `ESCALATING` and still contains `PENDING_VERIFICATION`.
- `ensure_user_transition(ESCALATING, ACKNOWLEDGED)` passes;
  `ensure_user_transition(ARCHIVED, IN_PROGRESS)` raises `INVALID_TRANSITION`.
- `role_permissions(VIEWER)` contains `VIEWS_WRITE`, `ISSUES_FOLLOW` and `PROFILE_SELF`,
  and not `INTEGRATIONS_READ`, `ALERTS_WRITE`, `VIEWS_MANAGE_SHARED` or
  `OWNERSHIP_WRITE`. The RESPONDER set adds `ALERTS_MUTE` and `EXTERNAL_ISSUES_WRITE`.
  ADMIN has every v13 permission. Scope `READ` equals the VIEWER set.
- Settings:
  - defaults load from an empty env;
  - `TELESCREEN_WEEKLY_REPORT_CRON=""` -> None;
  - an invalid cron -> `ConfigError`;
  - `TELESCREEN_GITHUB_ISSUES_REPOS="a/b, c/d"` -> `("a/b", "c/d")`;
  - `"bad"` -> `ConfigError`.
- `priority_for_severity`: critical -> high, high -> high, medium -> medium.
- Every new DTO constructs with only its required fields. The no-op observers return None.
- The split: `build_issues_router` exposes the same (method, path) set as before, which the
  test lists literally (12 routes).
- Edits to existing tests:
  - `tests/core/test_v11_foundation.py:115`: the `OPEN_STATUSES` tuple assertion gets the
    two new members;
  - `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`: the module names of the issue
    routes change from `issues` to `issues_list`, `issues_detail`, `issues_actions` and
    `issues_comments` (the 12 `RouteRule("issues", ...)` rows), and `MODULES` replaces
    `"issues"` with the four names. Paths and permissions are unchanged.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/issues/transitions.py src/telescreen/api/routers src/telescreen/notifications/templates tests/core tests/ports tests/api/v12_rbac
uv run --frozen ruff check --no-cache src/telescreen tests/core tests/ports tests/api
uv run --frozen ty check src/telescreen tests/core tests/ports tests/api
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p01-typelint.log 2>&1; grep -E '^(src/telescreen/(core|ports|api/routers/issues|issues/transitions|notifications/templates)|tests/(core|ports|api/v12_rbac))' /tmp/telescreen-v13-p01-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p01-pytest-all.log 2>&1; echo "exit=$?"
```

Expected:

- each check exits 0;
- the typelint grep prints nothing;
- the FULL pytest suite passes (exit 0). This plan must leave the whole suite green,
  including `tests/notifications/v12/**`, `tests/api/v11_issues/**` and
  `tests/e2e/test_route_permissions.py`.

## Completion Criteria

- [ ] Deliverables 1-7 are done, and every new value matches design section 2.
- [ ] `wc -l src/telescreen/api/routers/issues*.py` shows every file under 1000 lines.
- [ ] New tests pass; the full suite passes.
- [ ] The Progress Log has hashes and every command with its exit status and log path.

## Progress Log

(append sessions per conventions section 8)
