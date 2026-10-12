# V13-P06 Bulk Actions and Merge/Unmerge Backend (plus the run-1 bulk carry-over UI)

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D11-D13), 6.1, 6.2, 6.3, 7 (safety rule 5), 14, 15 (run 2 carry-over); design-docs/specs/design-ui-sentry.md section 11.2 (issue stream row)
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v13-p06-bulk-merge-backend`
- planPath: `impl-plans/active/v13-p06-bulk-merge-backend.md`
- wave: 3 (run 2)
- dependsOn: `v13-p04-issue-stream-backend`, `v13-p05-triage-lifecycle-backend`,
  `v13-p03-ui-foundation`, `v13-p13-ui-stream` (all accepted in run 1)
- writePaths:
  - `impl-plans/active/v13-p06-bulk-merge-backend.md`
  - `src/telescreen/issues/bulk.py`
  - `src/telescreen/issues/merge.py`
  - `src/telescreen/api/routers/issue_bulk.py`
  - `tests/issues/v13_bulk`
  - `src/web/src/api/client.ts`
  - `src/web/src/api/mock/router.ts`
  - `src/web/src/api/mock/mock.test.ts`
  - `src/web/src/pages/issues/IssueList.tsx`
  - `src/web/src/pages/issues/IssueList.test.tsx`
- sharedPaths: none
- sharedPathNotes:
  - `src/web/src/api/client.ts`: created by v13-p03. Edit ONLY `issuesApi.bulk` (add
    optional `start`/`end`). No other client function changes.
  - `src/web/src/api/mock/router.ts` and `mock.test.ts`: created by v13-p03. Edit ONLY the
    `/api/issues/bulk` handler and its tests. `tests/leak/test_ui_fixtures.py` scans every
    `src/web/src/api/mock/**/*.ts`, so it must keep passing (no sample secret literal).
  - `src/web/src/pages/issues/IssueList.tsx` and `IssueList.test.tsx`: created by v13-p13.
    Edit ONLY `runBulk` scoping and `remediate`, plus the matching tests.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Provide one server bulk endpoint (ids or "all matching") that goes through the same
per-issue service methods as the single-issue routes, plus merge and unmerge built on
p05's group helpers and `rebase_archive`. It also closes the three low items left open by
the run-1 integration review:

- INT-W2-04: all-matching bulk in a `custom` period lacks `start`/`end`.
  `parse_period("custom", None, None, now)` raises `VALIDATION_ERROR`, so the server body
  and the UI must both carry them.
- INT-W2-05: "Mark remediated" with all matching is selection-only.
  `IssueList.tsx:remediate` shows the warning "Remediation requires a finite selected set".
- Archive `until_escalating` parsing: bulk `params.archive` must be parsed exactly like the
  single route. The mock bulk handler defaults a missing `kind` to `for_duration` and gives
  every kind a 24 h `until`. The server stores `archived_at + 90 days` for the count and
  escalation kinds.

Contracts from accepted plans (do not change them):

- `issues/query/period.py:parse_period(period, start, end, now) -> Period`;
- `issues/query/select.py:select_matching_ids(session, query_text, period: Period,
  ctx: QueryContext, limit) -> MatchResult(ids, matched)`;
- `issues/query/filters.py:QueryContext(member_id, resolver, owners, now)`;
- `issues/groups.py:active_children`, `primary_of`, `recompute_group_counters`,
  `cascade_to_children`;
- `issues/archive.py:ArchiveRequest`, `validate_archive_request`, `archive_issue(session,
  issue, request, actor_ref, now, policy, audit, activity)`, `unarchive_issue`,
  `rebase_archive(session, issue, scope, now, policy)` and `RebaseScope.OWN/GROUP`;
- `IssueService.change_status`, `assign`, `set_priority` and `mark_reviewed`
  (`issues/service.py`);
- `IssueRemediationService.resolve_with_reason` and `request_verification(session,
  issue_id, action, include_linked, actor_ref, note)` (`issues/lifecycle.py`).

Imitate:

- `api/routers/issue_triage.py:build_issue_triage_router` and `ArchiveBody` (router deps,
  `pydantic.dataclasses` bodies, index after commit);
- `api/routers/issues_list.py:list_issues` for the `period`/`start`/`end` parameter types
  (`str | None`, `datetime | None`);
- `api/routers/issues_actions.py` (`_invalid_transition()`, error mapping);
- `pages/issues/IssueList.tsx:listRequest` for the custom-period spread.

## Non-goals

- No change to any p03, p04, p05 or p13 file outside the sharedPathNotes scopes above. A
  needed change elsewhere becomes a Reconciliation request.
- No "start workflow" bulk action: it stays a client loop (design 6.1).
- Merge stays finite-selection only. Keep `IssueList.tsx:merge`'s "Merge requires a
  finite selected set" guard and the Start workflow guard unchanged.
- No merge of issues that hold exceptions. Nothing in this plan writes exception rows,
  `ignored` or `allowed`.
- No router registration or wiring (v13-p12, run 3). Tests build the router directly.
- No edit of `src/web/src/api/types.ts`, the mock fixtures, `components/**`,
  `scripts/screenshots.ts`, `tests/leak/**`, `tests/db/test_models.py` or the route-walk
  tests.

## Invariants

- Bulk: each issue runs in its own transaction (`async with sessions.begin()`), with the
  same validation, history and audit as the single route. An error on one issue is
  recorded in `failed` and processing continues.
- Before any issue changes, the request is validated once. Any failure returns the error
  and changes nothing:
  - exactly one of `ids` and `query` is given;
  - `ids` has 1-1000 unique values;
  - `parse_period(period, start, end, now)` succeeds (query mode only);
  - the action's `params` are complete (`archive` through `validate_archive_request`; no
    default `kind`);
  - `resolve` with `manual_unverified` and no `ISSUES_RESOLVE_UNVERIFIED` -> 403.
- Query mode caps at 1000 ids and sets `truncated` when `matched > processed`.
- One `issue.bulk` audit summary records the action and the matched, processed, succeeded
  and failed counts. It never holds the query text, the note or any params value.
- Merge and unmerge run in one transaction. They never run archive evaluation, never emit
  `issue_escalating`, and never change `archive_until` or `archived_at`.
- Errors never echo the query, the note or the ids list.

## Deliverables

1. **`issues/bulk.py`**:
   - `BulkAction` StrEnum: `status`, `assign`, `priority`, `archive`, `unarchive`,
     `resolve`, `mark_reviewed`, `request_verification`.
   - Frozen dataclasses:
     - `BulkArchiveParams(kind: ArchiveKind, duration_minutes: int | None, count: int |
       None)`;
     - `BulkVerificationParams(action: RemediationAction, include_linked: bool)`;
     - `BulkParams(status, assignee_ref, priority, archive, reason, note, verification)`,
       all optional, with the key names of design 6.1;
     - `BulkRequest(ids: tuple[str, ...] | None, query: str | None, period: Period | None,
       action: BulkAction, params: BulkParams)`, holding the already parsed `Period`;
     - `BulkFailure(issue_id: str, code: ErrorCode)`;
     - `BulkResult(matched, processed, truncated, succeeded: tuple[str, ...], failed:
       tuple[BulkFailure, ...])`.
   - `validate_bulk_params(action, params) -> None`. It raises `VALIDATION_ERROR` when a
     required key is missing:
     - `status` needs `status`;
     - `assign` accepts `assignee_ref` null;
     - `priority` needs `priority`;
     - `archive` needs `archive`, then calls `validate_archive_request(ArchiveRequest(...))`;
     - `resolve` needs `reason` and a non-empty `note`;
     - `request_verification` needs `verification`.
   - `BulkService(sessions, issues: IssueService, remediation: IssueRemediationService,
     archive_policy, clock, audit, indexer: SearchIndexer)` with
     `run(request, principal_ref, is_admin, query_ctx: QueryContext) -> BulkResult`.
     - Map `TelescreenError.code` to the failure code. A missing id -> `not_found`.
     - The resolve note is redacted by the existing service. Do not redact twice.
     - Index each succeeded issue after its own commit.
2. **`issues/merge.py`**: `MergeService(sessions, audit, clock, indexer, activity:
   IssueActivityNotifier | None)`.
   - `merge(primary_id, ids, actor)` checks, in order (each failure changes nothing):
     1. D13 preconditions: every member is in `UNRESOLVED_STATUSES` or `archived`, else
        `invalid_transition`;
     2. no `requested` or `active` exception on any member -> `MERGED_GROUP`;
     3. the primary is not itself merged -> `MERGED_GROUP`;
     4. `primary_id` is not in `ids` -> `VALIDATION_ERROR`;
     5. at most 50 children per primary after re-parenting -> `LIMIT_EXCEEDED`.

     Then it follows design 6.2 steps 1-5:
     1. set `merged_into_id`, re-parenting grandchildren;
     2. copy status, archive fields and assignee to each child;
     3. write an `IssueMergeRow` and `merged` history on both sides;
     4. `recompute_group_counters`;
     5. `rebase_archive(primary, RebaseScope.GROUP)` if the primary is archived.

     Audit `issue.merged` (ids and counts) and notify the activity port when it is set.
   - `unmerge(primary_id, ids, actor)` checks that each id is an active child of the
     primary (`VALIDATION_ERROR` otherwise). It then:
     - clears `merged_into_id` and sets `unmerged_at` and `unmerged_by`;
     - sets the child's `needs_review`;
     - recomputes the primary's counters;
     - runs `rebase_archive(child, RebaseScope.OWN)` for each archived child and
       `rebase_archive(primary, RebaseScope.GROUP)` when the primary is archived;
     - writes `unmerged` history on both sides and audit `issue.unmerged`.
3. **`api/routers/issue_bulk.py`**: `build_issue_bulk_router(deps: IssueBulkRouterDeps)`.
   - `POST /api/issues/bulk` with body `BulkBody {ids?, query?, period?, start?, end?,
     action, params}`. `start`/`end` are `datetime | None`, as in `issues_list.py`. Nested
     `params` uses pydantic dataclasses whose key names match design 6.1.
   - `POST /api/issues/merge` with body `MergeBody {primary_id, ids}`.
   - `POST /api/issues/{issue_id}/unmerge` with body `UnmergeBody {ids}`.
   - Each route has exactly one permission dependency, `Permission.ISSUES_WRITE`.
   - The router parses the period with `parse_period` BEFORE calling the service, and
     builds `QueryContext` from the principal's member id, `deps.resolver`, `deps.owners`
     and `clock.now()`.
   - Responses: bulk returns `{matched, processed, truncated, succeeded, failed:
     [{issue_id, code}]}`; merge and unmerge return the primary via `_issue_json(issue)`.
   - Do not use `include_router` anywhere. p12 composes this router by copying `APIRoute`
     objects (see `api/routers/issues.py`).
4. **UI carry-over (TypeScript)**:
   - `client.ts` `issuesApi.bulk`: the value type gains `start?: string; end?: string`,
     forwarded when defined, in the same style as `period`.
   - `IssueList.tsx`:
     - `runBulk`'s all-matching request spreads `start`/`end` only when `period() ===
       "custom"`, exactly like `listRequest`;
     - `remediate` with `allMatching()` calls `runBulk("request_verification",
       { verification: { action: "remove", include_linked: false } })` instead of the
       warning toast;
     - the selected-set path is unchanged.
   - `mock/router.ts` `/api/issues/bulk`:
     - copy `start`/`end` from the body into the period URL used by `inPeriod`;
     - an `archive` action without `params.archive.kind` -> `error("validation_error",
       ..., 422)` and no change;
     - `for_duration` -> `until = now + duration_minutes`; every other kind -> `until =
       now + 90 days`;
     - count kinds keep `threshold = count`, and `until_escalating` has `threshold: null`.

## Tests

Python (`tests/issues/v13_bulk/`; SQLite, FakeClock, fake indexer, router built
directly):

- Bulk status `acknowledged` over 3 ids, one of them `resolved` -> 2 succeeded and 1
  failed with `invalid_transition`. History and audit rows are per issue, and there is one
  `issue.bulk` audit with counts only.
- Bulk by `query="source:slack"` with 1002 matching -> processed 1000, `truncated` true,
  `matched` 1002.
- INT-W2-04:
  - query mode with `period="custom"`, `start="2026-10-01"`, `end="2026-10-12"` touches
    only issues whose `last_seen`/`first_seen` fall in the window, and returns the same id
    set as `GET /api/issues` with those parameters;
  - `period="custom"` without `start`/`end` -> 422 and no change;
  - a span over 365 days -> 422.
- INT-W2-05: query-mode `request_verification` with `verification {action: "remove",
  include_linked: false}` moves the matching eligible issues to `pending_verification`.
  Missing `verification` -> 422 and no change.
- Archive parsing:
  - `params.archive {kind: "until_escalating"}` over ids -> each issue is `archived` with
    `archive_until = archived_at + 90 days` and a threshold per design section 7;
  - `{kind: "until_escalating", duration_minutes: 60}` -> 422 and no issue changed;
  - `{duration_minutes: 60}` without `kind` -> 422 (no default kind);
  - `until_findings` with `count` 3 -> every issue archived with the bound.
- Bulk resolve `manual_unverified` by a responder -> 403 and no change. A resolve note
  with a sample secret (`tests.support.samples`) -> the raw sample is absent from the
  history, audit and response.
- Bulk `mark_reviewed` clears `needs_review`.
- Neither or both of `ids` and `query` -> 422. 1001 ids -> 422.
- Merge A into P:
  - A is hidden from `select_matching_ids`, and P's counters equal the union;
  - a merge with A holding a `requested` exception -> `merged_group` and no change;
  - merging P into its own child -> 422;
  - re-parenting: merge P (with child A) into Q -> A and P are both children of Q.
- Safety rule 5:
  - P archived `until_findings` 5 (baseline 10), merge C with 40 findings -> P stays
    archived, the baseline equals the group `times_seen`, `archive_until` is unchanged,
    and no `issue_escalating` event exists in the transaction;
  - after that, 5 new findings escalate P;
  - unmerge C while C is archived -> C's baseline equals its own `times_seen`, with no
    escalation.
- Unmerge of an id that is not a child -> 422.
- Router: each route has exactly one permission dependency. Walk the built router's
  `APIRoute.dependant`, as in `tests/api/v13_stream/test_issue_routes.py`. A viewer gets
  403, as in `tests/issues/v13_lifecycle/test_triage_routes.py:
  test_archive_route_requires_issues_write_permission`.

TypeScript (vitest):

- `IssueList.test.tsx`:
  - replace "keeps remediation selection-only when all matching issues are selected" with
    a test where all-matching Mark remediated posts exactly one `/api/issues/bulk` body
    `{query, period, action: "request_verification", params: {verification: {action:
    "remove", include_linked: false}}}` (INT-W2-05);
  - add a test where a custom period `2026-10-01`..`2026-10-12` plus "Select all N
    matching" plus resolve posts a bulk body with `period: "custom"`, `start` and `end`
    (INT-W2-04);
  - keep the existing merge finite-selection behavior asserted.
- `mock.test.ts`:
  - bulk archive `{kind: "until_escalating"}` gives `archive.kind` `until_escalating` with
    an `until` about 90 days ahead;
  - bulk archive without `kind` -> rejects with `validation_error`;
  - bulk with custom `start`/`end` touches only issues in the window.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`. Run from the
repository root unless the command starts with `cd src/web`. Record each exit status and
log path in the Progress Log.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues/bulk.py src/telescreen/issues/merge.py src/telescreen/api/routers/issue_bulk.py tests/issues/v13_bulk
uv run --frozen ruff check --no-cache src/telescreen/issues/bulk.py src/telescreen/issues/merge.py src/telescreen/api/routers/issue_bulk.py tests/issues/v13_bulk
uv run --frozen ty check src/telescreen/issues/bulk.py src/telescreen/issues/merge.py src/telescreen/api/routers/issue_bulk.py tests/issues/v13_bulk
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p06-typelint.log 2>&1; ! grep -E '^(src/telescreen/issues/(bulk|merge)[.]py|src/telescreen/api/routers/issue_bulk[.]py|tests/issues/v13_bulk/)' /tmp/telescreen-v13-p06-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_bulk tests/issues/v13_lifecycle tests/issues/v13_query tests/leak/test_ui_fixtures.py 2>&1 | tee /tmp/telescreen-v13-p06-pytest.log
cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p06-install.log 2>&1
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p06-typecheck.log
cd src/web && bun run test 2>&1 | tee /tmp/telescreen-v13-p06-test.log
cd src/web && bun run build --outDir /tmp/telescreen-v13-p06-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p06-build.log
```

Expected:

- every command exits 0, and the typelint grep finds nothing;
- `tests/leak/test_ui_fixtures.py` passes unchanged;
- the whole vitest suite passes.

A failure caused only by a sibling's in-progress file (p14 under
`src/web/src/pages/issues/detail`) is recorded as such. This plan does not run `bun run
screenshots`: v13-p14 runs it in wave 3, and the integration review reruns it. The full
`uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p06-pytest-all.log 2>&1`
is informational.

## Completion Criteria

- [ ] Every Python test above passes, including safety rule 5 and the INT-W2-04,
  INT-W2-05 and archive-parsing regressions (each docstring names the item).
- [ ] The IssueList and mock regressions pass, and `grep -n "Remediation requires a finite
  selected set" src/web/src/pages/issues/IssueList.tsx` prints nothing.
- [ ] Each of the three routes declares exactly one permission.
- [ ] `git diff --name-only HEAD -- src/web/src/api/types.ts src/web/src/api/mock/fixtures
  src/web/package.json src/web/bun.lock` is empty, and `src/web/scripts/screenshots.ts`
  differs from HEAD by at most v13-p16's one-line member-edit name change (wave 2).
- [ ] Every new Python file is under 1000 lines.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
