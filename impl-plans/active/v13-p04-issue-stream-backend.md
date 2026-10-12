# V13-P04 Issue Stream Backend: Query Language, Period, Trend, Sorts, Counts, Saved Views, Item Serializer

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D1, D6-D10), 3 (B2, B3, B5), 4, 5, 15 ("Shared item serializer")
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p04-issue-stream-backend`
- planPath: `impl-plans/active/v13-p04-issue-stream-backend.md`
- wave: 2
- dependsOn: `v13-p01-foundation`, `v13-p02-db`
- writePaths:
  - `impl-plans/active/v13-p04-issue-stream-backend.md`
  - `src/telescreen/issues/query`
  - `src/telescreen/issues/views.py`
  - `src/telescreen/api/routers/issue_counts.py`
  - `src/telescreen/api/routers/issue_views.py`
  - `tests/issues/v13_query`
  - `tests/api/v13_stream`
  - `tests/issues/test_issues_api.py`
  - `tests/api/v11_issues/test_issues_router.py`
- sharedPaths:
  - `src/telescreen/api/routers/issues_list.py`
  - `src/telescreen/api/routers/issues_common.py`
- sharedPathNotes:
  - `src/telescreen/api/routers/issues_list.py`: created by v13-p01 (wave 1, mechanical
    move). This plan is its only wave-2 editor: query, period, trend, sorts and B2.
  - `src/telescreen/api/routers/issues_common.py`: created by v13-p01 (wave 1, pure move
    plus the `IssueItemExtras` contract). This plan is its only wave-2 editor: the S1
    fields of `_issue_json` and the new `issue_item_extras`. v13-p05 and v13-p07 never
    edit it.
- artifactRoots: none
- Shared-test ownership (run-1 manifest e2; design section 15): this plan owns the two
  existing issue-API tests above because it changes the list route and the item
  serializer they call. Edit them only for an assertion your change breaks (for example a
  legacy filter that now goes through the parser); never weaken a masking or permission
  assertion. v13-p05 runs both files read-only. No existing test asserts the exact item
  key set (checked at `d8073b1`). The route walk (`tests/e2e/test_route_permissions.py`,
  `tests/e2e/test_api_routes.py`, `tests/api/v12_rbac/**`) is not affected because
  `issue_counts.py` and `issue_views.py` are not registered until v13-p12; do not add any
  route to `issues_list.py`.

Read `impl-plans/conventions.md` first.

## Intent and Context

Move the issue stream to a server-side query language, and fix three defects:

- B2: `assigned:none` raised `RECIPIENT_UNKNOWN`;
- B3: `is:unresolved` hid regressed, acknowledged and in_progress issues;
- B5: negation was dropped.

Then add the period, the per-issue trend, the new sorts, tab counts and server saved views.
This plan is the single wave-2 owner of the shared item serializer (`issues_common.py`).

Imitate:

- `api/routers/issues_list.py:list_issues` (the keyset cursor with `encode_cursor` and
  `decode_cursor`) and `issue_stats` (python bucketing, which works on SQLite);
- `api/routers/issues_common.py:_invalid_transition` (a hand-built error `Response`);
- `notifications/templates/validate.py` (`Redactor` checks of user text);
- `issues/exceptions.py` (audit through `AuditLog`).

## Non-goals

- No change to `issues_detail.py`, `issues_actions.py`, `issues_comments.py` or
  `issues.py`.
- No Meilisearch use for the stream (D7).
- No bulk or merge (v13-p06).
- No wiring (v13-p12).

## Invariants

- Legacy parameters (`status`, `source`, `rule_class`, `severity`, `assignee`, `q`,
  `exposure`, and the sorts `last_seen`, `first_seen`, `times_seen`, `risk`) keep their
  meaning and are combined with `query` using AND.
- Merged children (`merged_into_id IS NOT NULL`) never appear in the list or the counts.
- Error responses never echo the query. The 422 body is `{"error": {"code":
  "invalid_query", "detail": "invalid query", "token_index": n, "reason": "<code>"}}`.
- `_issue_json(issue, extras=None)` keeps its name and parameters. Without `extras` it
  emits `assignee: null`, `merged_count: 0` and `bookmarked: false`; it always emits the
  row fields. v13-p05 calls it without extras and v13-p07 with extras.
- Route-order contract for v13-p12: `GET /api/issues/counts` lives in the new router
  `issue_counts.py`. That router must be registered BEFORE the issues router, or
  `/api/issues/{issue_id}` captures `counts`. This plan's API tests build the app in that
  order.

## Deliverables

1. **`issues/query/` package**, each file under 1000 lines:
   - `model.py`: `QueryKey` StrEnum (`is`, `status`, `severity`, `priority`, `rule`,
     `source`, `exposure`, `assigned`, `has`, `first_seen`, `last_seen`, `times_seen`,
     `values`, `bookmarks`), `Comparison` (eq, gt, gte, lt, lte),
     `QueryTerm(key, values: tuple[str, ...], negated: bool, comparison: Comparison)`,
     `ParsedQuery(terms, text: tuple[str, ...])`, and
     `QueryError(token_index: int, reason: QueryErrorReason)`, where the reasons are
     `unknown_key`, `invalid_value`, `too_many_tokens` and `too_long`.
   - `parser.py`: `parse_issue_query(text: str) -> ParsedQuery`.
     - It handles `key:value`, `!key:value`, `key:[a,b]`, quoted values and free text.
     - More than 20 tokens, or more than 1000 characters, gives an error.
     - Enum values are checked against the vocabulary enums.
     - `first_seen` and `last_seen` accept `-Nh|-Nd|-Nw`, `+N...`, `>`/`>=`/`<`/`<=`
       with an ISO date or date-time, or a plain `YYYY-MM-DD`.
     - `times_seen` and `values` accept `N`, `>N`, `>=N`, `<N`, `<=N`.
   - `filters.py`: `QueryContext(member_id: str | None, resolver: RecipientResolver, owners:
     OwnerDirectory | None, now: datetime)` and `async def build_filters(session, parsed,
     ctx) -> tuple[ColumnElement[bool], ...]`. Semantics of design 5.1:
     - `is:unresolved` -> `status IN UNRESOLVED_STATUSES`;
     - `is:for_review` -> unresolved set AND `needs_review`;
     - `assigned:none` -> both assignee ids are null (B2), and never calls the resolver;
     - `assigned:#slug` -> the same as `team:slug`;
     - `has:exception` -> an `issue_exceptions` row with status `requested` or `active`
       exists;
     - `has:external_issue` -> an `external_issue_links` row exists;
     - `bookmarks:me` -> an `issue_bookmarks` row exists for `ctx.member_id`;
     - a negated term is wrapped in `not_(...)`;
     - free text `TELE-<n>` -> `number == n`; other text -> `LIKE` on title or locator
       with `%` and `_` escaped (use the `escape` argument);
     - `assigned:me` or `my_teams` without a member -> `TelescreenError(FORBIDDEN)`, as
       the list route does today.
   - `period.py`: `Period(start: datetime | None, end: datetime | None)` and
     `parse_period(period, start, end, now) -> Period`. `all` -> (None, None); a custom
     span over 365 days or with start after end -> `VALIDATION_ERROR`.
   - `trend.py`: `async def trends(session, issue_ids, bucket: TrendBucket, now) ->
     tuple[tuple[str, tuple[int, ...]], ...]`. It returns 24 hourly or 14 daily
     zero-filled counts per issue, including the findings of merged children
     (`issues.merged_into_id = id`).
   - `select.py`: `async def select_matching_ids(session, query_text, period: Period, ctx,
     limit: int) -> MatchResult(ids: tuple[str, ...], matched: int)`. This is a contract
     for v13-p06 (bulk "select all matching"). It excludes merged children.
2. **`issues/views.py`**: `SavedViewService(redactor, audit: AuditLog, clock)` with
   `list(session, member_id)`, `create`, `update`, `delete`, `star`, `unstar` and
   `set_default`.
   - Name length is 1-64. The query must parse.
   - `Redactor` hit in name or query -> `TelescreenError(RAW_VALUE_DETECTED)`, nothing
     stored. The audit `view.rejected` holds field names only.
   - Limits: 50 personal views per member, 100 shared views -> `LIMIT_EXCEEDED`.
   - Only the owner edits a personal view. Shared views need the `views.manage_shared`
     flag, passed in as a bool.
   - Audit actions are `view.created`, `view.updated` and `view.deleted`, with id and
     visibility only.
3. **`api/routers/issues_list.py`**: `GET /api/issues` gains `query`, `period`, `start`,
   `end`, `trend` (`24h`, `14d` or `none`; default `14d`) and the sorts `values`,
   `priority` and `trending`.
   - Cursors carry the sort tuple and the id; a malformed cursor -> `VALIDATION_ERROR`.
   - `priority` order: rank high=3, medium=2, low=1, then `last_seen`, then id.
   - `trending` order: findings in the last 24 h (correlated count), then `last_seen`,
     then id.
   - Legacy `assignee=none` -> unassigned (B2).
4. **`api/routers/issues_common.py`**:
   - `_issue_json` adds `priority`, `priority_manual`, `needs_review`, `archive`
     (`{kind, until, threshold, baseline}` or null), `updated_at`, and from extras
     `assignee` (`{kind, ref, display_name}` or null), `merged_count` and `bookmarked`;
   - new `async def issue_item_extras(session, issues, member_id, actors: ActorDirectory |
     None) -> tuple[IssueItemExtras, ...]`, which batches the lookups.
     - Assignee ref: `member:<id>` / `team:<id>`. Without `actors`, the display name is the
       ref.
     - `merged_count` is one grouped count.
     - `bookmarked` is one query for the page.
5. **`api/routers/issue_counts.py`**: `build_issue_counts_router(deps:
   IssueCountsRouterDeps)` with `GET /api/issues/counts` (`Permission.ISSUES_READ`). It
   applies the query with its `is:` and `status:` terms removed, then the six tab filters
   of design section 4, plus `updated_since_count` (`updated_at > updated_since`).
6. **`api/routers/issue_views.py`**: `build_issue_views_router(deps)` serving the
   `/api/views` routes of design 5.2.
   - Permissions: `VIEWS_READ` for GET; `VIEWS_WRITE` for create, update, delete, star and
     default. A shared view needs `principal.allows(VIEWS_MANAGE_SHARED)`, checked in the
     handler.
   - A principal without `member_id` -> 403.

## Tests

`tests/issues/v13_query/` (parser and filters; SQLite through `tests.support.db`):

- `!source:slack` -> one negated term. `foo:x` -> `unknown_key` at index 0.
  `severity:[high,critical]` -> 2 values. `times_seen:>=5` -> gte 5. `first_seen:-24h` and
  `last_seen:+7d` are valid. 21 tokens -> `too_many_tokens`.
- The filters against seeded rows:
  - `is:unresolved` returns regressed, acknowledged, in_progress and escalating, but not
    archived, resolved or pending_verification (B3 regression; fails on 57f389b);
  - `assigned:none` returns only unassigned issues (B2);
  - `!source:slack` (B5);
  - `has:exception`, `assigned:#secops`, `bookmarks:me`, `values:>2`;
  - `TELE-7` matches number 7 only;
  - free text with `%` is matched literally.
- `parse_period`: `7d` gives the start now-7d; a custom span of 400 days -> error.
- `trends`: a child's findings are counted in the primary's buckets; empty buckets are 0.

`tests/api/v13_stream/` (app built with the counts router BEFORE the issues router):

- `GET /api/issues?assignee=none` -> 200 with unassigned items only (B2; fails on
  57f389b).
- `?query=is:unresolved` includes a regressed issue (B3); `?query=!source:slack` (B5).
- `?query=foo:bar` -> 422 with `token_index` 0 and `reason` `unknown_key`; the body does
  not contain `foo:bar`.
- `sort=priority` and `sort=trending` with cursor paging over 3 pages give no duplicates
  or gaps.
- A merged child is absent from the list and from the counts. An item has `trend` with 14
  ints, and `assignee.display_name` comes from a fake `ActorDirectory`.
- `/api/issues/counts`: the tab counts match the seeded statuses, and
  `updated_since_count` is right.
- Views:
  - create, list, star, default;
  - a shared view without `views.manage_shared` -> 403;
  - a sample secret from `tests.support.samples` in the name -> 422 with no row
    (`select count(*) from saved_views` == 0);
  - an unbound token -> 403;
  - the 51st personal view -> 422 `limit_exceeded`.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues/query src/telescreen/issues/views.py src/telescreen/api/routers/issues_list.py src/telescreen/api/routers/issues_common.py src/telescreen/api/routers/issue_counts.py src/telescreen/api/routers/issue_views.py tests/issues/v13_query tests/api/v13_stream tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py
uv run --frozen ruff check --no-cache src/telescreen/issues/query src/telescreen/issues/views.py src/telescreen/api/routers/issues_list.py src/telescreen/api/routers/issues_common.py src/telescreen/api/routers/issue_counts.py src/telescreen/api/routers/issue_views.py tests/issues/v13_query tests/api/v13_stream tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py
uv run --frozen ty check src/telescreen/issues/query src/telescreen/issues/views.py src/telescreen/api/routers/issues_list.py src/telescreen/api/routers/issues_common.py src/telescreen/api/routers/issue_counts.py src/telescreen/api/routers/issue_views.py tests/issues/v13_query tests/api/v13_stream tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p04-typelint-final4.log 2>&1; ! grep -E '^(src/telescreen/issues/query/|src/telescreen/issues/views[.]py|src/telescreen/api/routers/(issues_list|issues_common|issue_counts|issue_views)[.]py|tests/issues/v13_query/|tests/api/v13_stream/|tests/issues/test_issues_api[.]py|tests/api/v11_issues/test_issues_router[.]py)' /tmp/telescreen-v13-p04-typelint-final4.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_query tests/api/v13_stream tests/api/v11_issues tests/issues/test_issues_api.py tests/e2e/test_route_permissions.py tests/api/v12_rbac > /tmp/telescreen-v13-p04-pytest-final4.log 2>&1
```

Each command runs from the repository root after `set -o pipefail` and
`export PYTHONDONTWRITEBYTECODE=1`. The final Progress Log records exits and full log
paths. Whole-repository typelint may report concurrent sibling-plan findings; the filtered
command must print no p04-owned findings. The full repository pytest run is informational.

## Completion Criteria

- [x] B2, B3 and B5 regression tests exist and pass. Each test's docstring names the defect.
- [x] `select_matching_ids` and `issue_item_extras` have the signatures above.
- [x] Every new route declares exactly one permission dependency.
- [x] Every file is under 1000 lines; the Progress Log is complete.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-12 23:58
**Tasks Completed**: Implemented the query model/parser/filter/compiler, bounded periods,
zero-filled issue trends, bounded matching-ID contract, list sorts and keyset cursors,
counts router, safe saved-view service/router, and batched stream-item extras. Added B2/B3/B5
regressions, API coverage for merged-child exclusion, counts, display-name serialization,
safe query errors, cursor pagination and validation, period bounds, saved-view redaction,
ownership/limits, audit masking, and exactly-one permission dependencies.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/v13-p04-issue-stream-backend.md` before=94aac37f4c913ae2ca9494b8d82d40d5f0a01d32dfd701d879ed1215db5d47c9 after=`tmp/telescreen-v13-20261012-e2/p04/edit-24-after.sha256`; `src/telescreen/api/routers/issues_list.py` before=6fd80aeee03154fafc742924bf5ad7b1aa648fe339d93549351ce25782e398cd after=777af8dc8a7eca66563c79b913d557a2f2db666ea5aa094fd89a1faa823ed117; `src/telescreen/api/routers/issues_common.py` before=8a398bcba7a534cf8cc35a27345057d68f197c2e2276bf81d9efe69fe316616d after=99a92bffb7cca31c92b42be9a60c5c414ae1832f1eb406f5c85f06691d2bf226; new source/test files were absent before and their final hashes are in `tmp/telescreen-v13-20261012-e2/p04/final-hashes.sha256`. Per-edit before/after hashes and intent notes are in the same directory.
**Verification**: `uv run --frozen ruff format --check --no-cache <p04 scoped paths>` -> exit 0 (log: `/tmp/telescreen-v13-p04-format-final3.log`); `uv run --frozen ruff check --no-cache <p04 scoped paths>` -> exit 0 (log: `/tmp/telescreen-v13-p04-ruff-final3.log`); `uv run --frozen ty check <p04 scoped paths>` -> exit 0 (log: `/tmp/telescreen-v13-p04-ty-final3.log`); `uv run --frozen python tools/typelint.py` filtered to p04 paths -> exit 0, no p04 findings (log: `/tmp/telescreen-v13-p04-typelint-final3.log`; whole-repository run reports three concurrent p05 archive-test annotations); `uv run --frozen pytest -p no:cacheprovider tests/issues/v13_query tests/api/v13_stream tests/api/v11_issues tests/issues/test_issues_api.py tests/e2e/test_route_permissions.py tests/api/v12_rbac` -> exit 0, 24 passed (log: `/tmp/telescreen-v13-p04-pytest-final3.log`); p04 source and test files line-count gate -> exit 0, largest 515 lines (log: `/tmp/telescreen-v13-p04-wc-final3.log`).
**Notes**: Dependency acceptance was supplied by dispatch: `v13-p01-foundation` and `v13-p02-db` are in `acceptedPlanIds`. Independent review and final integration gates remain downstream workflow steps.

### Session: 2026-10-13 00:14
**Tasks Completed**: Fixed `P04-TIC-01`: the route-permission test now walks the retained counts and saved-views router route lists, recursively counts `PermissionDependency` markers, checks all eight expected `(method, path, permission)` entries, and asserts that exactly eight routes were inspected.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/api/v13_stream/test_issue_routes.py` edit-25 before=db80521995fe36c5ac22c3c965c9d87e427123cda7826ba6382294092c613686 after=5575808e57de8d2a368c157b24d4427deedec01cc1ef702cc4f2785a4091d67c; edit-26 before=5575808e57de8d2a368c157b24d4427deedec01cc1ef702cc4f2785a4091d67c after=2eeb833aa12dd4f321b19a73d12d608f3a8a5996d82289d4b89ecd4ca6c20d5e. Plan file: edit-27 before=8ab2601464e5b4f6a7746465724f68b743955dac3286fa5bb8d3f05c4cf33eaa after=0e8eda144452c48f0521253c453d99839898cb38163e66a094fb50ab38e5d790; edit-28 before=2a0400b6a564ef90a88b87205a49590ecece420ad1c2690206b990e306e25b42 after=0e8eda144452c48f0521253c453d99839898cb38163e66a094fb50ab38e5d790; edit-29 before=0e8eda144452c48f0521253c453d99839898cb38163e66a094fb50ab38e5d790 after=7ab0bef5a648961a35c53381a585f78eb8643ddbe288ccbd8e039a2b9e6427eb; edit-30 before=7ab0bef5a648961a35c53381a585f78eb8643ddbe288ccbd8e039a2b9e6427eb after=`tmp/telescreen-v13-20261012-e2/p04/edit-30-after.sha256`. Hash files and per-edit intents are in the p04 evidence directory.
**Verification**: Required p04 pytest command -> exit 0, 24 passed (log: `/tmp/telescreen-v13-p04-pytest-final4.log`); scoped `ruff format --check` -> exit 0 (log: `/tmp/telescreen-v13-p04-format-final4.log`); scoped `ruff check` -> exit 0 (log: `/tmp/telescreen-v13-p04-ruff-final4.log`); scoped `ty check` -> exit 0 (log: `/tmp/telescreen-v13-p04-ty-final4.log`); whole-repository typelint and p04 path filter -> exit 0, all 468 files clean (log: `/tmp/telescreen-v13-p04-typelint-final4.log`). The route-walk assertion itself verifies all eight pairs and permissions during the passing pytest run.
**Notes**: The corrected guard is test-only. No production router or out-of-scope test file changed. Review decision `P04-TIC-01` is addressed and ready for independent re-review; no review acceptance is claimed.

### Session: 2026-10-13
**Tasks Completed**: Fixed `P04-ADV-01-free-text-case-sensitive`: `build_filters` now uses
`ilike` for both display columns while preserving escaped `%`/`_` patterns and the exact
`TELE-<n>` branch. Added SQLite mixed-case matching and PostgreSQL dialect compilation
regressions; all prior query assertions remain intact.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/issues/query/filters.py` edit-31 before=
`ee268e72ee62625e93904fcacf07873103710afcbfc715ced09e2bd7555ed70f` after=
`324a1eb6d09c4a54006c9bf90f13c32bbee592b3e61cdb9c300217fe7ffaf680`; 
`tests/issues/v13_query/test_query.py` edit-32 before=
`eb27467a95ee1f1ad344bff73fd1128332d0599ff1db3ee6d0512a2decc76e3f` after=
`2fbdda420cee41ad2b5d1580330d21bfda8632ac1c1d585a9958ec37ef905c17`; plan edit-33
before is in `tmp/telescreen-v13-20261012-e2/p04/edit-33-before.sha256`; its post-edit hash
is recorded in `edit-33-after.sha256`. Per-edit intents and hashes are in the same p04
evidence directory.
**Verification**: `uv run --frozen pytest -p no:cacheprovider
tests/issues/v13_query/test_query.py` -> exit 0, 10 passed
(`/tmp/telescreen-v13-p04-query-case-final5.log`); required scoped pytest plus
`tests/issues/test_issues.py` -> exit 0, 35 passed
(`/tmp/telescreen-v13-p04-pytest-final5.log`); scoped `ruff format --check` -> exit 0
(`/tmp/telescreen-v13-p04-format-final5.log`); scoped `ruff check` -> exit 0
(`/tmp/telescreen-v13-p04-ruff-final5.log`); scoped `ty check` -> exit 0
(`/tmp/telescreen-v13-p04-ty-final5.log`); whole-repository typelint with the p04 path
filter -> exit 0, no p04 diagnostics (`/tmp/telescreen-v13-p04-typelint-final5.log`;
whole-repository output reports 53 sibling p05 findings across 471 files); line-count gate
-> exit 0, largest listed source file 412 lines
(`/tmp/telescreen-v13-p04-wc-final5.log`); Completion Criteria unticked-box check -> exit 0.
**Notes**: P04-ADV-01's source and behavioral verification are ready for independent
re-review; no review acceptance is claimed. Combined-tree integration gates remain owned by
the serial integration step.

### Session: 2026-10-13 verification addendum
**Tasks Completed**: Extended the SQLite free-text regression to prove `%` and `_` remain
literal pattern characters (`100%_safe` matches the literal row and excludes `100X_safe`).
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: edit-34 `tests/issues/v13_query/test_query.py` before=
`2fbdda420cee41ad2b5d1580330d21bfda8632ac1c1d585a9958ec37ef905c17` after=
`b2255e37c984689857df3c04c7256b04928b33d61682639a2456be781e705556`; plan edit-35
before is in `tmp/telescreen-v13-20261012-e2/p04/edit-35-before.sha256` and its post-edit
hash is in `edit-35-after.sha256`.
**Verification**: Query module -> exit 0, 10 passed
(`/tmp/telescreen-v13-p04-query-case-final6.log`); scoped p04 suite plus
`tests/issues/test_issues.py` -> exit 0, 35 passed
(`/tmp/telescreen-v13-p04-pytest-final7.log`); scoped format, ruff and ty checks -> exit 0
(`/tmp/telescreen-v13-p04-format-final7.log`, `/tmp/telescreen-v13-p04-ruff-final7.log`,
`/tmp/telescreen-v13-p04-ty-final7.log`); whole-repository typelint with p04 path filter
-> exit 0, no p04 diagnostics (`/tmp/telescreen-v13-p04-typelint-final7.log`; 53 sibling
p05 diagnostics remain outside this plan); line-count and unticked-criteria checks -> exit
0 (`/tmp/telescreen-v13-p04-wc-final7.log`).
**Notes**: The required p04 criteria remain checked. Independent re-review and combined-tree
integration gates remain downstream; no review acceptance is claimed.
