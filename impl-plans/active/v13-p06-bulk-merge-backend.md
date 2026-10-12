# V13-P06 Bulk Actions and Merge/Unmerge Backend

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D11-D13), 6.1, 6.2, 6.3, 7 (safety rule 5), 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p06-bulk-merge-backend`
- planPath: `impl-plans/active/v13-p06-bulk-merge-backend.md`
- wave: 3
- dependsOn: `v13-p04-issue-stream-backend`, `v13-p05-triage-lifecycle-backend`
- writePaths:
  - `impl-plans/active/v13-p06-bulk-merge-backend.md`
  - `src/telescreen/issues/bulk.py`
  - `src/telescreen/issues/merge.py`
  - `src/telescreen/api/routers/issue_bulk.py`
  - `tests/issues/v13_bulk`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Provide one server bulk endpoint (ids or "all matching") that goes through the same
per-issue service methods as the single-issue routes, plus merge and unmerge built on
p05's group helpers and `rebase_archive`.

Use these contracts from the dependencies:

- `issues/query/select.py:select_matching_ids(session, query_text, period, ctx, limit)`
  and `issues/query/period.py:parse_period` (p04);
- `issues/groups.py:active_children`, `recompute_group_counters` and
  `cascade_to_children` (p05);
- `issues/archive.py:archive_issue`, `unarchive_issue`, `rebase_archive` and
  `RebaseScope` (p05);
- `IssueService.change_status`, `assign`, `set_priority` and `mark_reviewed`, and
  `IssueRemediationService.resolve_with_reason` and `request_verification`.

Imitate `api/routers/issues_actions.py` (index after commit; `_invalid_transition()`).

## Non-goals

- No change to p04 or p05 files. A needed change becomes a Reconciliation request.
- No "start workflow" bulk action: it stays a client loop (design 6.1).
- No merge of issues that hold exceptions; nothing in this plan writes exception rows.

## Invariants

- Bulk: each issue runs in its own transaction (`async with sessions.begin()`), with the
  same validation, history and audit as the single route. An error on one issue is
  recorded in `failed` and processing continues.
- Before any change, the request is rejected with 403 when `action=resolve` with reason
  `manual_unverified` and the principal lacks `ISSUES_RESOLVE_UNVERIFIED`.
- `ids`: 1-1000 unique values. Exactly one of `ids` and `query` is given; otherwise
  `VALIDATION_ERROR`. Query mode caps at 1000 and sets `truncated`.
- One `issue.bulk` audit summary records the action, matched, processed, succeeded and
  failed counts. It never holds the query text.
- Merge and unmerge run in one transaction. They never run archive evaluation, never emit
  `issue_escalating`, and never change `archive_until` or `archived_at`.

## Deliverables

1. **`issues/bulk.py`**:
   - `BulkAction` StrEnum: status, assign, priority, archive, unarchive, resolve,
     mark_reviewed, request_verification;
   - `BulkRequest(ids: tuple[str, ...] | None, query: str | None, period: str | None,
     action: BulkAction, params: BulkParams)`, where `BulkParams` holds the optional
     typed fields of design 6.1;
   - `BulkResult(matched, processed, truncated, succeeded: tuple[str, ...], failed:
     tuple[BulkFailure(issue_id, code: ErrorCode), ...])`;
   - `BulkService(sessions, issues: IssueService, remediation: IssueRemediationService,
     archive_policy, clock, audit, indexer: SearchIndexer)` with `run(request, principal_ref,
     query_ctx_factory) -> BulkResult`.
     - Map `TelescreenError.code` to the failure code.
     - A missing id -> `not_found`.
     - The resolve note is redacted by the existing service.
2. **`issues/merge.py`**: `MergeService(sessions, audit, clock, indexer, activity:
   IssueActivityNotifier)`.
   - `merge(primary_id, ids, actor)` checks, in order:
     - D13 preconditions: every member is in `UNRESOLVED_STATUSES` or `archived`;
     - no `requested` or `active` exception on any member -> `MERGED_GROUP`;
     - the primary is not itself merged -> `MERGED_GROUP`;
     - `primary_id` not in `ids`;
     - children per primary <= 50 after re-parenting -> `LIMIT_EXCEEDED`.

     Then it follows design 6.2 steps 1-5:
     1. set `merged_into_id`, re-parenting grandchildren;
     2. copy status, archive fields and assignee to each child;
     3. write an `IssueMergeRow` and `merged` history on both sides;
     4. `recompute_group_counters`;
     5. `rebase_archive(primary, RebaseScope.GROUP)` if the primary is archived.

     Audit `issue.merged` (ids and counts) and notify the activity port.
   - `unmerge(primary_id, ids, actor)` checks that each id is an active child of the
     primary (`VALIDATION_ERROR` otherwise). It then:
     - clears `merged_into_id` and sets `unmerged_at` and `unmerged_by`;
     - sets the child's `needs_review`;
     - recomputes the primary's counters;
     - runs `rebase_archive(child, RebaseScope.OWN)` for each archived child and
       `rebase_archive(primary, RebaseScope.GROUP)` when the primary is archived;
     - writes `unmerged` history on both sides and audit `issue.unmerged`.
3. **`api/routers/issue_bulk.py`**: `build_issue_bulk_router(deps: IssueBulkRouterDeps)`:
   - `POST /api/issues/bulk` (body `BulkBody`), `POST /api/issues/merge` (body
     `MergeBody {primary_id, ids}`) and `POST /api/issues/{issue_id}/unmerge` (body
     `UnmergeBody {ids}`);
   - each needs `Permission.ISSUES_WRITE`;
   - the bulk response is the `BulkResult` JSON; merge and unmerge return the primary via
     `_issue_json(issue)`;
   - every touched issue is indexed after commit.

## Tests (`tests/issues/v13_bulk/`; SQLite, FakeClock, fake indexer)

- Bulk status `acknowledged` over 3 ids, one of them `resolved` -> 2 succeeded and 1
  failed with `invalid_transition`. History and audit rows are per issue, and there is one
  `issue.bulk` audit.
- Bulk by `query="source:slack"` with 1002 matching -> processed 1000, `truncated` true,
  `matched` 1002.
- Bulk resolve `manual_unverified` by a responder -> 403 and no change.
- Bulk archive `until_findings` 3 -> every issue is archived with the bound; bulk
  `mark_reviewed` clears `needs_review`.
- Neither or both of `ids` and `query` -> 422; 1001 ids -> 422.
- Merge A into P:
  - A is hidden from `select_matching_ids` and P's counters equal the union;
  - a merge with A holding a `requested` exception -> `merged_group` and no change;
  - merging P into its own child -> 422;
  - re-parenting: merge P (with child A) into Q -> A and P are both children of Q.
- Safety rule 5:
  - P archived `until_findings` 5 (baseline 10), merge C with 40 findings -> P is
    archived, the baseline equals the group `times_seen`, `archive_until` is unchanged,
    and no `issue_escalating` event exists in the transaction;
  - after that, 5 new findings escalate P;
  - unmerge C while C is archived -> C's baseline equals its own `times_seen`, with no
    escalation.
- Unmerge of an id that is not a child -> 422.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues/bulk.py src/telescreen/issues/merge.py src/telescreen/api/routers/issue_bulk.py tests/issues/v13_bulk
uv run --frozen ruff check --no-cache src/telescreen/issues src/telescreen/api/routers tests/issues/v13_bulk
uv run --frozen ty check src/telescreen/issues src/telescreen/api/routers tests/issues/v13_bulk
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p06-typelint.log 2>&1; grep -E '^(src/telescreen/issues/(bulk|merge)|src/telescreen/api/routers/issue_bulk|tests/issues/v13_bulk)' /tmp/telescreen-v13-p06-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues 2>&1 | tee /tmp/telescreen-v13-p06-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p06-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0 and the typelint grep prints nothing. The full run is
informational.

## Completion Criteria

- [ ] Every test above passes, including the safety rule 5 cases.
- [ ] The routes declare exactly one permission each.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
