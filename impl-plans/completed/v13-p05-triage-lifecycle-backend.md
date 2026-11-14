# V13-P05 Triage Lifecycle Backend: Archive and Escalating, Priority, For Review, Group Coordination, Ingest Hooks

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D1-D6, D12, D13), 2, 6.2 (rebase_archive), 6.3, 7 (safety rules 1-5), 8.1, 8.4 (activity emitters), 15
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p05-triage-lifecycle-backend`
- planPath: `impl-plans/active/v13-p05-triage-lifecycle-backend.md`
- wave: 2
- dependsOn: `v13-p01-foundation`, `v13-p02-db`
- writePaths:
  - `impl-plans/active/v13-p05-triage-lifecycle-backend.md`
  - `src/telescreen/issues/service.py`
  - `src/telescreen/issues/ingest_transitions.py`
  - `src/telescreen/issues/lifecycle.py`
  - `src/telescreen/issues/repository.py`
  - `src/telescreen/issues/exceptions.py`
  - `src/telescreen/issues/policy_apply.py`
  - `src/telescreen/issues/archive.py`
  - `src/telescreen/issues/groups.py`
  - `src/telescreen/api/routers/issue_triage.py`
  - `tests/issues/v13_lifecycle`
  - `tests/issues/test_issues.py`
  - `tests/issues/v11_policy`
  - `tests/issues/v11_record`
  - `tests/issues/v11_remediation`
  - `tests/e2e/test_flow_ingest.py`
  - `tests/e2e/test_flow_v11_policy.py`
  - `tests/e2e/test_flow_v11_owner_health.py`
  - `tests/e2e/test_flow_v11_remediation.py`
  - `tests/leak/flows.py`
  - `tests/leak/test_leak_matrix.py`
  - `tests/leak/flows_v11.py`
  - `tests/leak/test_leak_matrix_v11.py`
  - `tests/assistant/test_assistant.py`
  - `tests/evidence/test_evidence.py`
- sharedPaths:
  - `src/telescreen/api/routers/issues_actions.py`
- sharedPathNotes:
  - `src/telescreen/api/routers/issues_actions.py`: created by v13-p01 (wave 1, mechanical
    move). This plan is its only wave-2 editor: `IssuePatchBody.priority` and the PATCH
    status rejections.
- artifactRoots: none
- Shared-test ownership (run-1 manifest e2; design section 15): the existing tests listed
  after `tests/issues/v13_lifecycle` read issue rows, issue history or the ingest path
  that this plan changes (priority, `needs_review`, `OPEN_STATUSES`/`RECONCILED_STATUSES`,
  the `ingest_transitions.py` move). Expect them to pass unchanged; edit one only when
  an assertion encodes a v1.2 fact the design changes (for example an exact history row
  count that now includes a v1.3 row), and record the reason in the Progress Log. Never
  weaken a leak assertion: a leak failure is a defect in your source change.
  Read-only for this plan: `tests/issues/test_issues_api.py` and
  `tests/api/v11_issues/test_issues_router.py` (owned by v13-p04), `tests/leak/harness.py`,
  `tests/leak/sinks.py` (v13-p09) and every route-walk test. A failure there is a
  Reconciliation request. `issue_triage.py` is not registered until v13-p12, so the route
  walk does not change; do not add routes to `issues_actions.py`.

Read `impl-plans/conventions.md` first.

## Intent and Context

Implement the issue state machinery of v1.3:

- the `archived` status (four conditions, bounded at 90 days) and the `escalating` status;
- the editable priority and the For review flag;
- group coordination for merged issues;
- the ingest hooks: findings observer, activity notifier, and the `issue_escalating` event.

Archive must never become permanent suppression, and approved exceptions stay the only
permanent suppression.

Imitate:

- `issues/service.py:IssueService.record_scan`, the post-insert branch chain at about
  :277-420 (regression, exception expiry, verification failure, allowed reopen);
- `issues/lifecycle.py:IssueRemediationService.request_verification` (history, audit and
  enqueue per issue);
- `issues/maintenance.py:ExceptionMaintenanceProvider.tick` (a once-a-minute
  `ScheduleProvider`);
- `issues/exceptions.py` approve, at about :131-145.

## Non-goals

- No bulk or merge endpoints (v13-p06); this plan provides the group helpers that p06
  calls.
- No `issues_common.py`, `issues_list.py` or `issues_detail.py` edits.
- No `routing.py` (v13-p11). No wiring (v13-p12).
- No activity-notification implementation: call the `IssueActivityNotifier` port only.

## Invariants (each has a test)

1. No v1.3 path writes `ignored`, `allowed` or an exception row. Exception approval still
   moves the issue to `ignored`, and now also clears the archive fields.
2. `archive_until` is never null while the issue is `archived`, and is never more than 90
   days after `archived_at`.
3. Every transition out of `archived` goes through `clear_archive(issue)`. This covers:
   escalation, expiry, unarchive, a verification request, auto-resolve, exception
   approval, policy `allowed`, and reasoned resolve.
4. Merge and unmerge never escalate by themselves and never change `archive_until` or
   `archived_at`. `rebase_archive` touches only `archive_baseline` and
   `archive_threshold`.
5. `issues/service.py` ends under 1000 lines. First move the post-insert transition chain
   into `ingest_transitions.py` with no behavior change, then add the hooks.
6. New `IssueService` fields have defaults, so `wiring.py` compiles unchanged:
   - `findings_observer: FindingsObserver = field(default_factory=NoopFindingsObserver)`;
   - `activity: IssueActivityNotifier = field(default_factory=NoopIssueActivityNotifier)`;
   - `archive_policy: ArchivePolicy = field(default_factory=ArchivePolicy)` (defaults 10
     and 3).

## Deliverables

1. **`issues/archive.py`**:
   - `ArchivePolicy(min_findings: int = 10, factor: int = 3)`;
   - `ArchiveRequest(kind: ArchiveKind, duration_minutes: int | None, count: int | None)`;
   - `validate_archive_request(request)`:
     - `for_duration` needs 30-129600;
     - `until_findings` and `until_values` need `count` 1-100000;
     - `until_escalating` needs neither;
     - violations raise `VALIDATION_ERROR`.
   - `async archive_issue(session, issue, request, actor_ref, now, policy)`:
     - allowed only from the unresolved set; otherwise `INVALID_TRANSITION`;
     - sets `archived_at = now`;
     - `archive_until` is `now + duration` for `for_duration`, else `now + 90 days`;
     - sets the baseline and threshold (design section 7 table) from group counters;
     - clears `needs_review`, writes `archived` history and audit, and calls the activity
       notifier.
   - `unarchive_issue` -> `unresolved`, with `unarchived` history.
   - `clear_archive(issue) -> None` sets the five fields to None.
   - `async evaluate_after_ingest(session, primary, previous_visibility: Visibility, now)
     -> bool`. On condition met, or on a widened `visibility_rank`:
     - status `escalating` and `clear_archive`;
     - `needs_review = True`;
     - `escalated` history (actor `system:scanner`) and audit `issue.escalated`;
     - `IssueEvent(ISSUE_ESCALATING)` through `self.events`, plus the activity notifier.
   - `async rebase_archive(session, issue, scope: RebaseScope) -> None`, where
     `RebaseScope` is `own` or `group`:
     - `until_findings` -> baseline = the recomputed `times_seen` of the scope;
     - `until_values` -> baseline = the recomputed `distinct_fingerprints`;
     - `until_escalating` -> threshold = `max(min_findings, factor * P)`, where P is the
       larger of the peak UTC-day count over the 7 days before now and the trailing-24 h
       count;
     - `for_duration` -> unchanged;
     - never runs an evaluation.
   - `ArchiveMaintenanceProvider(ScheduleProvider)` with `tick(session, now)`, throttled
     to once a minute like `ExceptionMaintenanceProvider`. Archived issues with
     `archive_until <= now` become `unresolved`: `clear_archive`, `needs_review`,
     `unarchived` history with actor `system:archive`, and the notifier.
2. **`issues/groups.py`** (contracts used by v13-p06):
   - `async active_children(session, primary_id) -> tuple[IssueRow, ...]`;
   - `async primary_of(session, issue) -> IssueRow` returns itself when not merged;
   - `async recompute_group_counters(session, primary) -> None`. It sets `times_seen`,
     `distinct_fingerprints`, `first_seen` and `last_seen` from the union of findings of
     the primary and its active children;
   - `async cascade_to_children(session, primary, apply: GroupChange) -> tuple[str, ...]`.
     `GroupChange` is a frozen dataclass of the change (status, assign, archive,
     unarchive, resolve, verify, review). It returns the ids it skipped.
   - `async propagate_child_open(session, child, now) -> None`. With the primary
     `resolved` -> `regressed`; `pending_verification` -> `unresolved`; `archived` ->
     `escalating`. It writes history `via TELE-<child>`, sets needs_review, and emits the
     matching event.
3. **`issues/service.py`** and **`issues/ingest_transitions.py`**:
   - After the split, `record_scan` follows these steps:
     1. insert findings;
     2. run the existing transitions (in `ingest_transitions.py`);
     3. if the issue is a merged child: `propagate_child_open` when it reopened;
     4. if the primary is archived: `evaluate_after_ingest` on the primary, with group
        counters recomputed first;
     5. `recompute_group_counters` when the issue is in a group;
     6. `findings_observer.on_findings_recorded(FindingsRecorded(issue.id, inserted,
        observed_at))` when `inserted > 0`.
   - Create: `priority = priority_for_severity(severity)` and `needs_review = True`.
   - Severity change with `priority_manual` false: priority follows.
   - Regression and the verification-failure reopen set `needs_review = True` and notify
     the activity port.
   - `change_status`: cascades, clears `needs_review`, notifies `status_changed`.
   - `assign`: cascades, clears `needs_review`, notifies `assigned` with
     `assignee_member_id`.
   - New `set_priority(session, issue_id, priority, actor)`: `priority_manual = True`,
     `priority_changed` history and audit.
   - New `mark_reviewed(session, issue_id, actor)`: `reviewed` history and audit.
   - `get()` fills `IssueSnapshot.priority` and `assignee_team_slug` (from
     `owners.team_ref`, without the `team:` prefix).
4. **`issues/lifecycle.py`**:
   - `request_verification` accepts `escalating` and `archived` and calls `clear_archive`;
     on a primary it cascades to active children (each gets its own rescan).
   - `request_exception` on a group member (a primary with children, or a child) ->
     `TelescreenError(MERGED_GROUP)`.
   - `resolve_with_reason` cascades and clears archive.
5. **`issues/exceptions.py`**: approval calls `clear_archive`.
   **`issues/policy_apply.py`**: any move out of `archived` calls `clear_archive`.
   **`issues/repository.py`**: add helpers only if needed (for example
   `archived_due(session, now)`).
6. **Routers**:
   - `issues_actions.py`: `IssuePatchBody` gains `priority: IssuePriority | None = None`.
     PATCH rejects `status` in (`archived`, `escalating`, `resolved`, `ignored`) with the
     existing `_invalid_transition()`.
   - New `issue_triage.py`: `build_issue_triage_router(deps: IssueTriageRouterDeps)` with
     `POST /api/issues/{issue_id}/archive` (body `ArchiveBody`), `/unarchive` and `/review`,
     each needing `Permission.ISSUES_WRITE`. Responses use `_issue_json(issue)` without
     extras, and index the issue after commit.

## Tests (`tests/issues/v13_lifecycle/`; SQLite and FakeClock)

- Archive `until_findings` 5 from `unresolved`: 4 new findings keep it archived; the 5th
  -> `escalating`, an `issue_escalating` event, `needs_review`, and empty archive fields.
- `until_values` 2: two new distinct fingerprints -> escalating.
- `until_escalating` with a peak of 2/day and policy (10, 3) gives threshold 10: 9 findings
  in 24 h stay archived, 10 escalate.
- `for_duration` 60 min: tick at +59 min -> archived; at +61 min -> `unresolved`, with
  `unarchived` history by `system:archive`.
- Every kind: `archive_until - archived_at <= 90 days`. `count` 0 or `duration_minutes`
  200000 -> `VALIDATION_ERROR`.
- Exposure widens from `internal` to `public` while archived -> escalating.
- Archive from `resolved` -> `INVALID_TRANSITION`. Exception approval on an archived
  issue -> `ignored` with archive fields cleared. A verification request on an archived
  issue -> `pending_verification` with archive cleared.
- `rebase_archive`, for each kind on an archived primary with a merged child that has 40
  historical findings: the baseline equals the group counter, `archive_until` and
  `archived_at` are unchanged, and no escalation happens. Then 4 new findings -> still
  archived; the 5th -> escalating (safety rule 5).
- Group:
  - a resolved primary whose child gets a violating finding -> the primary is
    `regressed`;
  - `request_exception` on a child or on the primary -> `MERGED_GROUP`;
  - a cascade of status to two children, one of which cannot transition -> the skipped
    id is returned;
  - the group counters equal the union.
- Priority:
  - a new critical issue -> `high`;
  - severity medium -> high with `priority_manual` false -> priority follows;
  - after `set_priority(low)` a severity change does not overwrite it.
- For review: set on create and regress; cleared by assign, status change, archive and
  mark reviewed.
- The fake `IssueActivityNotifier` receives `assigned` (with the member id),
  `status_changed`, `escalated` and `unarchived`. The fake `FindingsObserver` receives
  one event per batch with `inserted > 0`.
- Router:
  - PATCH `{"status": "archived"}` -> 422 `invalid_transition`;
  - POST archive without the permission -> 403;
  - PATCH `{"priority": "low"}` -> 200. Then reload the `IssueRow` (or
    `IssueService.get(...).priority`) and assert priority `low`, `priority_manual` true,
    and exactly one `priority_changed` history row;
  - POST `/archive` (until_findings 5) -> 200. Assert on the reloaded row: status
    `archived`, `archive_kind` set, and `archive_until` set and at most 90 days ahead;
  - POST `/unarchive` -> 200 with row status `unresolved` and archive fields null;
  - POST `/review` -> 200 with row `needs_review` false and one `reviewed` history row.
- Rule for every p05 test: never assert on the `_issue_json` fields that v13-p04 adds
  (`priority`, `priority_manual`, `needs_review`, `archive`, `assignee`, `merged_count`,
  `bookmarked`, `updated_at`). p04 runs concurrently and owns that serializer. Assert on
  DB rows, `IssueSnapshot` or history. A response may only be checked for the status code
  and `id`.
- `wc -l src/telescreen/issues/service.py` is under 1000, and the existing
  `tests/issues/**` suites pass unchanged.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues src/telescreen/api/routers/issues_actions.py src/telescreen/api/routers/issue_triage.py tests/issues/v13_lifecycle
uv run --frozen ruff check --no-cache src/telescreen/issues src/telescreen/api/routers tests/issues
uv run --frozen ty check src/telescreen/issues src/telescreen/api/routers tests/issues
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p05-typelint.log 2>&1; grep -E '^(src/telescreen/issues/(service|ingest_transitions|lifecycle|repository|exceptions|policy_apply|archive|groups)|src/telescreen/api/routers/(issues_actions|issue_triage)|tests/issues/v13_lifecycle)' /tmp/telescreen-v13-p05-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues tests/api/v11_issues --ignore=tests/issues/v13_query 2>&1 | tee /tmp/telescreen-v13-p05-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_flow_ingest.py tests/e2e/test_flow_v11_policy.py tests/e2e/test_flow_v11_owner_health.py tests/e2e/test_flow_v11_remediation.py tests/leak/test_leak_matrix.py tests/leak/test_leak_matrix_v11.py tests/assistant/test_assistant.py tests/evidence/test_evidence.py 2>&1 | tee /tmp/telescreen-v13-p05-pytest-shared.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p05-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep prints nothing, and every existing
`tests/issues/**` test passes. The full run is informational.

## Completion Criteria

- [x] Safety rules 1-5 have named tests across `tests/issues/v13_lifecycle`; rebase behavior
  is tested here, while merge/unmerge endpoint behavior is owned by downstream v13-p06.
- [x] The `groups.py` and `archive.py` contracts match the signatures above (used by p06).
- [x] `service.py` is under 1000 lines (974); existing and new issue tests pass.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

### Session 1 — Implementation and final source verification (2026-10-12)

- Implemented bounded archive/escalating state, archive clearing on all supported exits,
  group helpers/cascades, priority and `needs_review` behavior, ingest observers and
  activity notifications, the p05 triage router, and DB/history-based regression tests.
- Initial Step 6 coverage was incomplete and overstated: test-integrity findings
  P05-TIC-01/02/03 identified missing exit-path, route-guard, group, priority, review,
  and activity assertions. The retry evidence below corrects that gap. `service.py` is
  974 lines.
- Per-edit evidence for implementation edits is in
  `tmp/telescreen-v13-20261012-e2/p05/edit-001..034-*`. The final plan update intent and
  pre-edit hash are in `edit-035-intent.md` and `edit-035-before.sha256`.
- Final source hashes before this plan update:
  - `src/telescreen/issues/service.py`: `b37c9aa79922bf50710201c13ae0cb9c1eca9b54c2005033b665f6ecaddfdf43`
  - `src/telescreen/issues/ingest_transitions.py`: `3cba11dacbc9453c7616c2b03e4ec658eee54c2ee08fc016452c098fe0558f0f`
  - `src/telescreen/issues/lifecycle.py`: `5de7c57a83fcf4adb55b52ceac3fac84a64b56ca0ee97e1826e8f84474b89d7e`
  - `src/telescreen/issues/exceptions.py`: `a2d5e1f78b605c5b404ac0446504f2147c75c5911fad70d1bcac3e446158769d`
  - `src/telescreen/issues/policy_apply.py`: `60ab3765dd9f9b71e9df7c83a5b4957708557e3cedead846be6f09a1ffe1a834`
  - `src/telescreen/issues/archive.py`: `959d57bfeea1296d3985f3927290fc22546d28e897a036acddcf2b8131622384`
  - `src/telescreen/issues/groups.py`: `199b376a8fd036a5b625c7b889f4d55b3b94528953b2681e1f9cac5db5e4d67a`
  - `src/telescreen/api/routers/issues_actions.py`: `7aeb23ebbe9b51de62818fb73e399e91e11867407c66fb9934d0a230aefff367`
  - `src/telescreen/api/routers/issue_triage.py`: `a9e692a360347f756d197496dc3e14789958800e358cc21b6c954603b1aac027`
  - `tests/issues/v13_lifecycle/test_archive.py`: `f1197782dec830be03528409facfece25506ad2ee7a1ea6bc7dd73a8ea2311a2`
- Final verification (all exit 0):
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/issues/service.py src/telescreen/issues/ingest_transitions.py src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/policy_apply.py src/telescreen/issues/archive.py src/telescreen/issues/groups.py src/telescreen/api/routers/issues_actions.py src/telescreen/api/routers/issue_triage.py tests/issues/v13_lifecycle` — exit 0, 10 files already formatted; `/tmp/telescreen-v13-p05-format-final.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/issues/service.py src/telescreen/issues/ingest_transitions.py src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/policy_apply.py src/telescreen/issues/archive.py src/telescreen/issues/groups.py src/telescreen/api/routers/issues_actions.py src/telescreen/api/routers/issue_triage.py tests/issues/v13_lifecycle` — exit 0, all checks passed; `/tmp/telescreen-v13-p05-ruff-final.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/issues/service.py src/telescreen/issues/ingest_transitions.py src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/policy_apply.py src/telescreen/issues/archive.py src/telescreen/issues/groups.py src/telescreen/api/routers/issues_actions.py src/telescreen/api/routers/issue_triage.py tests/issues/v13_lifecycle` — exit 0, all checks passed; `/tmp/telescreen-v13-p05-ty-final.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p05-typelint-final.log 2>&1; ! grep -E '^(src/telescreen/issues/(service|ingest_transitions|lifecycle|repository|exceptions|policy_apply|archive|groups)[.]py|src/telescreen/api/routers/(issues_actions|issue_triage)[.]py|tests/issues/v13_lifecycle/)' /tmp/telescreen-v13-p05-typelint-final.log` — no p05 diagnostics.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues/v13_lifecycle -ra` — 18 passed; `/tmp/telescreen-v13-p05-lifecycle-final-r2.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues tests/api/v11_issues --ignore=tests/issues/v13_query -ra` — 102 passed; `/tmp/telescreen-v13-p05-issues-final-r2.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/e2e/test_flow_ingest.py tests/e2e/test_flow_v11_policy.py tests/e2e/test_flow_v11_owner_health.py tests/e2e/test_flow_v11_remediation.py tests/leak/test_leak_matrix.py tests/leak/test_leak_matrix_v11.py tests/assistant/test_assistant.py tests/evidence/test_evidence.py -ra` — 32 passed; `/tmp/telescreen-v13-p05-shared-final-r4.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider -ra` — 902 passed, 8 skipped; exit 0; `/tmp/telescreen-v13-p05-pytest-all-final-r2.log`.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; wc -l src/telescreen/issues/service.py src/telescreen/issues/ingest_transitions.py src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/policy_apply.py src/telescreen/issues/archive.py src/telescreen/issues/groups.py src/telescreen/api/routers/issues_actions.py src/telescreen/api/routers/issue_triage.py tests/issues/v13_lifecycle/test_archive.py > /tmp/telescreen-v13-p05-lines-final.log; ! awk '$1 >= 1000 { found=1 } END { exit found }' /tmp/telescreen-v13-p05-lines-final.log` — exit 0; `service.py` is 974 lines and all touched Python files are below 1000 lines.
- An earlier full-suite attempt on the same implementation found three TL003 missing
  annotations in the new route test (901 passed, 1 failed, 8 skipped; exit 1;
  `/tmp/telescreen-v13-p05-pytest-all-final.log`). The response locals are now explicitly
  typed as `httpx.Response`; the final whole-repository suite above passes.
- Formal test-integrity/adversarial review, serial integration review, plan archival,
  staging, commit, and push remain downstream workflow steps.

### Session 2 — Test-integrity repairs (2026-10-12)

- Addressed `P05-TIC-01` with new DB-row/history tests in
  `tests/issues/v13_lifecycle/test_archive_exits.py`:
  `test_exception_approval_paths_clear_archive` covers admin request and request-plus-
  approval; `test_verification_request_clears_archive`,
  `test_policy_allow_clears_archive_without_exception_row`, and
  `test_reasoned_resolve_and_change_status_clear_archive` assert all five archive
  columns cleared. Existing `test_until_findings_escalates_and_clears_bounded_archive`,
  `test_unarchive_and_expiry_clear_metadata_and_mark_review`, and
  `test_automatic_resolution_clears_archive_metadata` cover escalation, unarchive,
  expiry and auto-resolve. `test_group_rebase_preserves_archive_window_then_escalates_on_fifth`
  builds 40 child findings, verifies group baseline 40 and unchanged archive window/no
  event, then checks four findings stay archived and the fifth escalates. Added the
  missing archived-status assertion after the four-finding evaluation.
- Addressed `P05-TIC-02` in `test_triage_routes.py`:
  `test_patch_rejects_dedicated_status_transitions_without_mutating_row` covers all four
  forbidden statuses and unchanged DB rows; `test_patch_priority_persists_manual_flag_and_one_history_row`
  checks persisted low/manual priority and exactly one history row;
  `test_archive_route_requires_issues_write_permission` uses a read-only token and
  checks 403 with unchanged archive state.
- Addressed `P05-TIC-03` in `test_groups_priority.py`:
  `test_reopened_merged_child_regresses_resolved_primary`,
  `test_group_counters_equal_union_of_primary_and_child_findings`,
  `test_priority_follows_severity_until_manual_override`, and
  `test_triage_clears_review_flag_and_emits_activity_payloads` cover the requested
  regression, union counters, automatic/manual priority, review clearing, and activity
  payloads including the member id.
- Completion mapping: rule 1 uses `test_archive_never_writes_suppression_or_exception_state`
  and `test_exception_approval_paths_clear_archive`; rule 2 uses
  `test_until_findings_escalates_and_clears_bounded_archive` plus bounded-kind checks;
  rule 3 uses the escalation, expiry/unarchive, auto-resolve and four new exit tests
  above; rule 4 uses `test_group_rebase_preserves_archive_window_then_escalates_on_fifth`
  (merge/unmerge endpoint behavior remains owned by p06); rule 5 uses the recorded
  `service.py` line-count check (974). Existing route-structure test
  `test_triage_routes_have_one_issues_write_dependency_each` remains in place.
- Test files and plan edits follow the local intent/hash records `edit-037..046-*` in
  `tmp/telescreen-v13-20261012-e2/p05`. Latest test hashes:
  `test_archive.py` `2058bfbdbd54c395da2576aafaafa448ade65efeb2a9135239edd9328482fdb7`;
  `test_archive_exits.py` `00b0e0dfb05cb46487ad413fe9f4d12c45859c60dc317e965324679429c3b88a`;
  `test_groups_priority.py` `23d3ce04f2bb4051ab20cc1f9a559f60e6555685347f66c4f0d8b7697add73f6`;
  `test_triage_routes.py` `10df800077cd26a90c1bbb8ecc889c07bc8d459bfe54316705f5539ff1591d88`.
- Final-source verification: scoped Ruff format/check and `ty check` passed on p05
  source/shared/test paths; filtered typelint passed with no p05 diagnostics in
  `/tmp/telescreen-v13-p05-typelint-final.log`. Lifecycle suite: 34 passed;
  `/tmp/telescreen-v13-p05-tic-lifecycle-r3.log`. Issues/API suite: 118 passed;
  `/tmp/telescreen-v13-p05-tic-issues-final.log`. Shared e2e/leak/assistant/evidence and
  route-permission suite: 33 passed; `/tmp/telescreen-v13-p05-tic-shared-final.log`.
  Whole suite: 920 passed, 8 environment-gated skips, exit 0;
  `/tmp/telescreen-v13-p05-tic-full-final-r2.log`.
- A first whole-suite retry after adding the tests failed only at repository typelint
  self-test (`53` TL003 annotations, `919` passed / `8` skipped; exit 1;
  `/tmp/telescreen-v13-p05-tic-full-final.log`). Added explicit local annotations in the
  three new test modules; the filtered typelint and final whole-suite rerun now pass.

### Session 3 — Adversarial threshold repair (2026-10-12)

- Addressed `P05-ADV-01`: extracted pure `_rate_threshold(rows, now, policy)` and reused
  it from `escalation_threshold` and `rebase_archive`'s `UNTIL_ESCALATING` branch. Scope,
  public signatures, other branches, archive window/status and event behavior are intact.
- `test_until_escalating_rebase_uses_the_seven_day_rate_window` covers 40 findings at
  `NOW-30d` -> 10 and mixed 4-at-`NOW-2d`/3-at-`NOW-1h` -> 12; both assert archived
  status, unchanged `archived_at`/`archive_until`, and zero events.
- Per-edit records: `edit-047..050-*` under `tmp/telescreen-v13-20261012-e2/p05`.
  Final SHA-256: `archive.py` `db3712542fb49855a0a54bba9fedf9e1cb8b2e83ca7c542e26d5b07f9099ef80`;
  `test_archive_exits.py` `97ff5a068c74a3175954c5f788dbcdf0dfbe9270c13041f0558b2f9a696f943e`.
- Initial lifecycle attempt failed 1/36 from duplicate fixture indexes (`/tmp/telescreen-v13-p05-adv1-lifecycle.log`); monotonic test indexes fixed it. Final `uv run --frozen pytest -p no:cacheprovider tests/issues/v13_lifecycle -ra`: 36 passed, exit 0 (`/tmp/telescreen-v13-p05-adv1-lifecycle-r2.log`).
- Final `uv run --frozen pytest -p no:cacheprovider tests/issues tests/api/v11_issues --ignore=tests/issues/v13_query -ra`: 120 passed, exit 0 (`/tmp/telescreen-v13-p05-adv1-pytest.log`); shared e2e/leak/assistant/evidence command: 32 passed, exit 0 (`/tmp/telescreen-v13-p05-adv1-shared.log`).
- Final `uv run --frozen pytest -p no:cacheprovider -ra`: 922 passed, 8 offline skips, exit 0 (`/tmp/telescreen-v13-p05-adv1-full.log`).
- `uv run --frozen ruff format --check --no-cache src/telescreen/issues/archive.py tests/issues/v13_lifecycle` exited 0 (`/tmp/telescreen-v13-p05-adv1-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/issues/archive.py tests/issues/v13_lifecycle` and `uv run --frozen ty check src/telescreen/issues/archive.py tests/issues/v13_lifecycle` exited 0 (`/tmp/telescreen-v13-p05-adv1-ruff.log`, `/tmp/telescreen-v13-p05-adv1-ty.log`).
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p05-adv1-typelint.log 2>&1; ! grep -E '^(src/telescreen/issues/archive[.]py|tests/issues/v13_lifecycle/)' /tmp/telescreen-v13-p05-adv1-typelint.log` exited 0 with no matching diagnostics.
- Formal independent re-review and serial integration remain downstream workflow gates.
