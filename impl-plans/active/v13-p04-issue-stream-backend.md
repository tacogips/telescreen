# V13-P04 Issue Stream Backend: Query Language, Period, Trend, Sorts, Counts, Saved Views, Item Serializer

**Status**: Ready
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
uv run --frozen ruff format --check --no-cache src/telescreen/issues/query src/telescreen/issues/views.py src/telescreen/api/routers tests/issues/v13_query tests/api/v13_stream
uv run --frozen ruff check --no-cache src/telescreen/issues src/telescreen/api/routers tests/issues/v13_query tests/api/v13_stream
uv run --frozen ty check src/telescreen/issues src/telescreen/api/routers tests/issues/v13_query tests/api/v13_stream
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p04-typelint.log 2>&1; grep -E '^(src/telescreen/issues/(query|views)|src/telescreen/api/routers/(issues_list|issues_common|issue_counts|issue_views)|tests/(issues/v13_query|api/v13_stream))' /tmp/telescreen-v13-p04-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_query tests/api/v13_stream tests/api/v11_issues tests/issues/test_issues_api.py 2>&1 | tee /tmp/telescreen-v13-p04-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p04-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0, the typelint grep prints nothing, and the existing
issue-API tests still pass. The full run is informational.

## Completion Criteria

- [ ] B2, B3 and B5 regression tests exist and pass. Each test's docstring names the defect.
- [ ] `select_matching_ids` and `issue_item_extras` have the signatures above.
- [ ] Every new route declares exactly one permission dependency.
- [ ] Every file is under 1000 lines; the Progress Log is complete.

## Progress Log

(append sessions per conventions section 8)
