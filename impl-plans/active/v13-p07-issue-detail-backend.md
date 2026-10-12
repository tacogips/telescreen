# V13-P07 Issue Detail Backend: Event Navigation, Tags, Stats, Activity, Comments, Mentions, Subscriptions, Bookmarks, External Issues

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D14-D17), 3 (B1, B4, B6, B7), 8.1-8.5, 10.3, 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v13-p07-issue-detail-backend`
- planPath: `impl-plans/active/v13-p07-issue-detail-backend.md`
- wave: 3 (run 2; Last Updated 2026-10-13 with the run-2 shared-file rules)
- dependsOn: `v13-p01-foundation`, `v13-p02-db`, `v13-p04-issue-stream-backend`. p07
  consumes p04's `_issue_json` extras emission (`assignee`, `merged_count`, `bookmarked`)
  and `issue_item_extras` in `issues_common.py`. p04 runs in run 1 and p07 in run 2.
- writePaths:
  - `impl-plans/active/v13-p07-issue-detail-backend.md`
  - `src/telescreen/issues/detail`
  - `src/telescreen/issues/activity`
  - `src/telescreen/issues/external`
  - `src/telescreen/api/routers/issue_events.py`
  - `src/telescreen/api/routers/issue_activity.py`
  - `src/telescreen/api/routers/issue_external.py`
  - `tests/issues/v13_detail`
  - `tests/api/v13_detail`
  - `tests/issues/test_issues.py`
  - `tests/issues/test_issues_api.py`
  - `tests/api/v11_issues/test_issues_router.py`
  - `tests/e2e/test_flow_ingest.py`
- sharedPaths:
  - `src/telescreen/api/routers/issues_detail.py`
  - `src/telescreen/api/routers/issues_comments.py`
- sharedPathNotes:
  - `src/telescreen/api/routers/issues_detail.py`: created by v13-p01 (wave 1, mechanical
    move). This plan is its only wave-2 editor: the detail-only fields, extras, and the
    events `order` parameter.
  - `src/telescreen/api/routers/issues_comments.py`: created by v13-p01. This plan is its
    only run-2 editor: the POST route uses `CommentService`.
  - The four existing tests above were owned by p04 or p05 in run 1. This plan owns them
    in run 2 (wave 3): they are expected to pass unchanged, and only a D14-broken
    assertion may change.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Make the issue detail work like Sentry's, and fix four defects:

- B1: event navigation was reversed;
- B4: raw ids were shown as assignees;
- B6: activity was session-local and had no actor;
- B7: everything used only the first 50 events.

Add:

- server event navigation, tag distribution, per-issue stats and an activity feed with
  actors;
- comment edit and delete, mentions that notify, subscriptions and bookmarks;
- external issue links and GitHub create (masked body, DLP-gated).

Everything covers the merged group of a primary: the issue id plus active children with
`merged_into_id = id`.

Imitate:

- `api/routers/issues_detail.py:issue_events` (cursor paging) and `get_issue`;
- `api/routers/issues_list.py:issue_stats` (zero-filled python buckets);
- `issues/service.py:IssueService.comment` (`redactor.redact(body).text[:4000]`, history
  and audit);
- `collectors/github/**` (httpx with `TypeAdapter(...).validate_json`; tests use
  `MockTransport`);
- `core/secrets.py:resolve_secret`.

## Non-goals

- Do not edit `issues_common.py` (v13-p04 owns it); call `_issue_json(issue, extras)` and
  `_issue_detail_json(...)`, then append the detail-only members.
- Do not edit `issues/service.py` or `lifecycle.py` (v13-p05).
- No `ActorDirectory` or `AssigneeSuggester` implementation (v13-p11): use the ports from
  `IssuesRouterDeps.actors` and `.suggester`, and fakes in tests.
- No notification rendering: call `MemberNotifier.send_to_member` (p01 port) only.
- No Jira create.

## Invariants

- Chronological order is (`observed_at`, `id`) ascending. `previous` = older and `next` =
  newer (B1).
- Comment bodies are redacted BEFORE storage and BEFORE mention parsing. A body that is
  empty after redaction, or longer than 4000 characters -> `VALIDATION_ERROR`.
- `can_edit` and edit/delete permission: the author's `actor_ref` equals the caller's
  `actor_ref`, or the caller has `COMMENTS_MANAGE_ANY`. Otherwise 403.
- Only mentions newly added by a create or an edit notify. At most 10 per comment; the
  rest are ignored. Inactive or unknown handles are ignored silently.
- An explicit unsubscribe (`subscribed = false`, reason `manual`) is never overridden by
  an auto-subscribe.
- External link URL rules:
  - `https` only, no userinfo, at most 2048 characters;
  - `Redactor.has_hits(url)` or a hit in the title -> 422 `raw_value_detected`, with no
    row and no echo.
- GitHub create:
  - the body is built ONLY from allow-listed masked fields;
  - title and body pass `Redactor.redact`, then `OutboundContentGuard.inspect`;
  - a block -> 422 `dlp_blocked`, nothing sent, audit `external_issue.blocked`;
  - the token is resolved at use and never logged or stored;
  - without token or repos -> 503 `integration_unconfigured`;
  - a repo not in the allow-list -> 422.
- No route is added to `issues_detail.py` or `issues_comments.py`, because the RBAC
  module table lists them. New routes go into the three new router modules.

## Deliverables

1. **`issues/detail/`**:
   - `group.py`: `async group_issue_ids(session, issue_id) -> tuple[str, ...]` returns the
     issue plus its active children.
   - `navigation.py`: `async select_event(session, issue_ids, selector) ->
     EventSelection(event: FindingRow, previous_id, next_id, position, total)`, where
     selector is `oldest`, `latest`, `recommended` (max confidence, then latest) or a
     finding id. An unknown id -> `NOT_FOUND`.
   - `tags.py`: `async tag_distribution(session, issue_ids, period) ->
     TagDistribution(total, tags)`.
     - Keys: `detector`, `rule_id`, `location` (the first segment of `location` split on
       `[ >/:]`), and, for groups only, `source` and `member` (short ids).
     - Top 10 values with `percent` rounded to one decimal, plus `other_count`.
   - `stats.py`: `async issue_stats(session, issue_ids, period, bucket, now)` returns
     zero-filled buckets.
2. **`issues/activity/`**:
   - `comments.py`: `CommentService(redactor, audit, clock, notifier: MemberNotifier,
     resolver: RecipientResolver)`.
     - `create` writes an `issue_comments` row and a `commented` history row with
       `to_value = comment id`, `note = None`, audit `issue.commented`. It also
       auto-subscribes the author (when `member:`) and handles mentions.
     - `edit` re-redacts, sets `edited_at`, writes `comment_edited` history, and notifies
       new mentions only.
     - `delete` is soft: `body = ""`, `deleted_at` set, `comment_deleted` history.
   - `mentions.py`: `parse_mentions(text) -> tuple[str, ...]`, pattern
     `@([a-z0-9_.-]{1,128})`, unique, at most 10. Handles resolve through
     `resolver.assignee_of(session, "member:<handle>")`; `RECIPIENT_UNKNOWN` is ignored.
     Each mention gets an `issue_mentions` row, an auto-subscribe (reason `mentioned`),
     and `send_to_member(MemberNotification(member_id, MENTIONS, ISSUE_MENTIONED,
     context))`. The context holds the masked issue fields, `activity_actor` (display) and
     `comment_excerpt` (the first 200 redacted characters).
   - `subscriptions.py`: `set_subscription(session, issue_id, member_id, subscribed,
     reason)` and `auto_subscribe(...)`, which is a no-op when a manual row with
     `subscribed = false` exists.
   - `bookmarks.py`: `bookmark` and `unbookmark`.
   - `feed.py`: `async activity_feed(session, issue_id, cursor, limit, actors,
     viewer_ref, can_manage_any) -> ActivityPage`.
     - Newest first; the cursor is (`at`, `id`).
     - It merges history rows and comments. A `commented` row whose `to_value` is a
       comment id is replaced by the comment item; legacy rows without one become
       read-only comment items using `note`.
     - Actors and `member:`/`team:` values are resolved through
       `ActorDirectory.display`.
   - `notifier.py`: `SubscriptionActivityNotifier(notifier: MemberNotifier, actors)`
     implements the `IssueActivityNotifier` port.
     - With `assignee_member_id` set: send one `ASSIGNED` / `ISSUE_ASSIGNED` notification
       and auto-subscribe (reason `assigned`).
     - Every other active subscriber except the actor's member, and except the newly
       assigned member: one `SUBSCRIBED` / `ISSUE_ACTIVITY` notification.
3. **`issues/external/`**:
   - `links.py`: `ExternalLinkService` with `list`, `link(session, issue_id, url, title,
     actor)` (duplicate -> 409 `conflict`) and `unlink`. The title defaults to the URL
     host and must be at most 200 characters. Audit `external_issue.linked` and
     `external_issue.unlinked`.
   - `github.py`: `GithubIssueCreator(transport: httpx.AsyncBaseTransport | None,
     api_url, token_ref, repos, env, guard: OutboundContentGuard, redactor, audit)`.
     - `create(session, issue_snapshot, repo, title)` calls `POST
       {api}/repos/{repo}/issues` with a 10 s timeout.
     - A provider error -> 502 `provider_error`.
     - Store an `ExternalIssueLinkRow` (kind `github`, `html_url`, `owner/repo#n`) and
       audit `external_issue.created` (repo and number only).
     - `build_body(snapshot, link)` is a pure function used by the leak tests.
4. **Routers**:
   - `issues_detail.py`, `GET /api/issues/{id}`:
     - call `_issue_json(issue, extras)`, with the extras computed by p04's
       `issue_item_extras` for this one issue (assignee display via `deps.actors`,
       `merged_count`, `bookmarked`), which fixes B4;
     - append `subscription`, `merged_into`, `merged_children` and
       `suggested_assignees` (via `deps.suggester`; an empty list when None) to the
       members of `_issue_detail_json`.

     `GET /events` adds `order=asc|desc` (default `desc`) and `issue_short_id` per event,
     covering the group.
   - `issues_comments.py`: the existing POST uses `CommentService.create`, with the same
     path, permission and 201 response. Build `CommentService` from `IssuesRouterDeps`:
     the redactor, audit and clock come from `deps.service`, `resolver` is `deps.resolver
     or deps.service.resolver`, and `notifier` is `deps.member_notifier`. When
     `member_notifier` is None, mentions are recorded but no notification is sent.
   - New `issue_events.py`: `GET /api/issues/{id}/events/{selector}`, `/tags` and `/stats`
     (`ISSUES_READ`).
   - New `issue_activity.py`:
     - `GET /api/issues/{id}/activity` (`ISSUES_READ`);
     - `PATCH` and `DELETE /api/issues/{id}/comments/{comment_id}` (`ISSUES_WRITE`);
     - `PUT /api/issues/{id}/subscription` and `PUT`/`DELETE /api/issues/{id}/bookmark`
       (`ISSUES_FOLLOW`; a member principal is required, otherwise 403).
   - New `issue_external.py`:
     - `GET /api/issues/{id}/external-issues` and `GET /api/external-issues/github/repos`
       (`ISSUES_READ`);
     - `POST /api/issues/{id}/external-issues`, `DELETE
       /api/issues/{id}/external-issues/{link_id}` and `POST
       /api/issues/{id}/external-issues/github` (`EXTERNAL_ISSUES_WRITE`).

## Tests

`tests/issues/v13_detail/` and `tests/api/v13_detail/`:

- B1: findings at t1 < t2 < t3 -> `oldest` returns t1 with `previous_id` null and `next_id`
  t2; `latest` returns t3; `position` and `total` are correct (fails on 57f389b).
- B7:
  - 120 findings -> `total` 120;
  - tags count over 120 (the top detector percent is exact);
  - stats sum to 120;
  - `/events?order=asc` returns the oldest first.
- Group: a primary with a child -> navigation spans both; tags include `member`.
- B4: the detail `assignee.display_name` comes from a fake `ActorDirectory`, not `mem-01`.
  `merged_count` and `bookmarked` in the detail item come from the extras p07 passes.
  p07 builds them with p04's `issue_item_extras(session, (issue,), member_id,
  deps.actors)`: one child gives `merged_count` 1, and a bookmark by the caller gives
  `bookmarked` true.
- B6: after POST comment, `GET /activity` shows the comment body and an actor display
  name; history items carry actor displays.
- Comments:
  - a body with a sample secret is stored redacted, and the raw sample is absent from the
    `issue_comments` rows and the response;
  - edit by another member -> 403; with `comments.manage_any` -> 200;
  - delete -> body empty, history `comment_deleted`.
- Mentions:
  - `@alice @alice @ghost` -> one notification to alice, none for ghost;
  - an edit adding `@bob` notifies bob only;
  - 12 mentions -> 10 rows.
- Subscriptions:
  - a manual unsubscribe, then a mention -> stays unsubscribed and still gets the mention
    notification (mention category);
  - the activity notifier sends `assigned` to the new assignee and `issue_activity` to
    the other subscribers, but not to the actor.
- External links:
  - an `http://` URL -> 422; a URL with userinfo -> 422; a URL with a sample secret ->
    422 and no row, and the response does not contain the sample;
  - duplicate -> 409;
  - GitHub create with MockTransport: the request body has the masked fields only (no
    raw sample, even when the latest masked snippet came from a secret); it stores
    `owner/repo#12`;
  - a guard block -> 422 `dlp_blocked` and no HTTP call;
  - create when unconfigured -> 503; `GET /api/external-issues/github/repos` when
    unconfigured -> 200 `{configured: false, repos: []}`; an unlisted repo -> 422.
- Bookmark and subscription with an unbound token -> 403.

## Run-2 shared-file rules (design section 15, run 2)

- `issues_detail.py` and `issues_comments.py` are registered through the `issues.py`
  composer. Add no route there (see Invariants), so `tests/e2e/test_route_permissions.py`,
  `tests/e2e/test_api_routes.py` and `tests/api/v12_rbac/**` stay read-only. The three
  new router modules are unregistered until v13-p12 and are tested by building them
  directly. Never use `include_router` in source.
- Owned shared tests (this plan's writePaths in the run-2 manifest; no other wave-3 plan
  edits them). They are expected to pass unchanged. Edit only an assertion that a D14
  fact breaks (a comment row plus a `commented` history row with `note = null`), and
  record the reason in the Progress Log:
  - `tests/issues/test_issues.py` (comment POST and history, around line 690);
  - `tests/issues/test_issues_api.py` (events paging);
  - `tests/api/v11_issues/test_issues_router.py` (safe detail);
  - `tests/e2e/test_flow_ingest.py` (events of an ingested issue).
- No new table, migration, template variable or notification kind. `issue_mentioned`,
  `issue_activity` and `issue_assigned` already exist (v13-p01). `tests/db/test_models.py`,
  `tests/notifications/**` and `tests/leak/**` are read-only. The masking of comments,
  mentions, activity and external-issue bodies is asserted in `tests/issues/v13_detail`
  and `tests/api/v13_detail` with `tests.support.samples`. The full leak matrix is
  v13-p17.
- Do not edit `issues_common.py`, `issues/service.py`, `lifecycle.py` or
  `issues/archive.py`. v13-p06 runs in the same wave and only reads them.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py
uv run --frozen ruff check --no-cache src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py
uv run --frozen ty check src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p07-typelint.log 2>&1; ! grep -E '^(src/telescreen/issues/(detail|activity|external)/|src/telescreen/api/routers/(issues_detail|issues_comments|issue_events|issue_activity|issue_external)[.]py|tests/(issues/v13_detail|api/v13_detail)/|tests/issues/test_issues(_api)?[.]py|tests/api/v11_issues/test_issues_router[.]py|tests/e2e/test_flow_ingest[.]py)' /tmp/telescreen-v13-p07-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/issues/v13_detail tests/api/v13_detail tests/api/v11_issues tests/issues/test_issues_api.py tests/issues/test_issues.py tests/e2e/test_flow_ingest.py 2>&1 | tee /tmp/telescreen-v13-p07-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py tests/e2e/test_api_routes.py tests/api/v12_rbac tests/leak 2>&1 | tee /tmp/telescreen-v13-p07-pytest-shared.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p07-pytest-all.log 2>&1; echo "exit=$?"
```

Expected:

- the scoped checks exit 0, and the typelint grep finds nothing;
- the existing issue-API tests and the shared read-only suites pass;
- every Python file is under 1000 lines (`wc -l src/telescreen/api/routers/issues_detail.py
  src/telescreen/api/routers/issues_comments.py`).

The full run is informational. Tests assert only this plan's fields, never the full item
key set.

## Completion Criteria

- [x] B1, B4, B6 and B7 regression tests pass; each docstring names the defect.
- [x] Every new route declares exactly one permission.
- [x] `SubscriptionActivityNotifier` satisfies the `IssueActivityNotifier` port (ty
  check).
- [x] The Progress Log has hashes and command evidence.

## Progress Log

### Session 1 — 2026-10-13

- Implemented B1 event order/navigation, B4 directory-backed assignee display, B6 persisted
  actor activity/comments, B7 full tag/stat counts, comment edits/deletes and mentions,
  subscriptions/bookmarks, external links and guarded GitHub issue creation. New event,
  activity and external routers remain unregistered for p12. `issues_detail.py` suppresses
  raw assignee refs when the optional actor directory is unavailable.
- Added tests under `tests/issues/v13_detail` and `tests/api/v13_detail`; the API test
  directly builds the new routers and checks one permission dependency per route.
- Final SHA-256 inventory: `tmp/telescreen-v13-20261013-e3/p07/final-hashes.txt`.
  Per-edit intent records 01–21 are in the same directory. No model, migration, dependency,
  `issues_common.py`, `issues/service.py`, or router-wiring changes.
- Final verification (all exit 0):
  - `uv run --frozen ruff format --check --no-cache src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py` — `/tmp/telescreen-v13-p07-format-final.log`.
  - `uv run --frozen ruff check --no-cache src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py` — `/tmp/telescreen-v13-p07-ruff-final.log`.
  - `uv run --frozen ty check src/telescreen/issues/detail src/telescreen/issues/activity src/telescreen/issues/external src/telescreen/api/routers/issues_detail.py src/telescreen/api/routers/issues_comments.py src/telescreen/api/routers/issue_events.py src/telescreen/api/routers/issue_activity.py src/telescreen/api/routers/issue_external.py tests/issues/v13_detail tests/api/v13_detail tests/issues/test_issues.py tests/issues/test_issues_api.py tests/api/v11_issues/test_issues_router.py tests/e2e/test_flow_ingest.py` — `/tmp/telescreen-v13-p07-ty-final.log`.
  - `uv run --frozen python tools/typelint.py` with p07-path filter — `/tmp/telescreen-v13-p07-typelint.log` (`523 file(s) clean`).
  - `uv run --frozen pytest -p no:cacheprovider tests/issues/v13_detail tests/api/v13_detail tests/api/v11_issues tests/issues/test_issues_api.py tests/issues/test_issues.py tests/e2e/test_flow_ingest.py` — `/tmp/telescreen-v13-p07-pytest.log` (21 passed).
  - `uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py tests/e2e/test_api_routes.py tests/api/v12_rbac tests/leak` — `/tmp/telescreen-v13-p07-pytest-shared.log` (18 passed).
  - `wc -l` over p07 detail/activity/external sources and routers, with `<1000` assertion — `/tmp/telescreen-v13-p07-wc.log` (pass).
- Formal independent reviews and combined-tree integration gates remain downstream workflow steps.
- Redispatch verification (2026-10-13): confirmed p10's corrected
  `tests/issues/v13_dashboard/test_dashboard.py` hash is
  `7af83123405f9c6c68c05acbffeb31a2c034c18327e6dc69cb98a75567362f7a`. A first full
  pytest run completed with 988 passed and 8 skipped, but its wrapper exited 1 because
  zsh reserves the variable name `status`; `/tmp/telescreen-v13-p07-pytest-all.log`
  preserves that attempt. Reran with `result_code`; the full suite exited 0 with 988
  passed and 8 skipped at `/tmp/telescreen-v13-p07-pytest-all-rerun.log`.
- Current-tree p07 verification: scoped format, Ruff, and ty passed at
  `/tmp/telescreen-v13-p07-format-rerun.log`,
  `/tmp/telescreen-v13-p07-ruff-rerun.log`, and
  `/tmp/telescreen-v13-p07-ty-rerun.log`; unfiltered typelint reported 525 clean files
  at `/tmp/telescreen-v13-p07-typelint-rerun.log`; owned tests passed 21 at
  `/tmp/telescreen-v13-p07-pytest-rerun.log`; shared tests passed 18 at
  `/tmp/telescreen-v13-p07-pytest-shared-rerun.log`; all p07 Python files remain under
  1000 lines (`/tmp/telescreen-v13-p07-wc-rerun.log`). Out-of-scope guards and the
  completion-box check passed.
- Independent test-integrity, adversarial, and branch-evidence reviews remain downstream
  workflow steps; no review approval is claimed here.

### Owner note: 2026-10-13 (session 13, progress-gate block)
The p07 implementation is complete and its gates pass; the full-suite failure INT-E3-P07-01 was in p10's tests/issues/v13_dashboard and p10 fixed it. The progress gate blocked p07 only because the step result listed the resolved INT-E3-P07-01 under `findings` with severity "mid", added review-pending notes to `risks`/`authorSelfCheck.residualRisks`, and kept the superseded failing full-suite run in `priorVerification`. On the next dispatch: do not change code unless a check fails; re-run this plan's Verification on the current tree; report INT-E3-P07-01 ONLY in `addressedFeedback` (changed paths: none in p07; verification: the passing full-suite command); leave `findings`, `risks`, `authorSelfCheck.findings`, `authorSelfCheck.residualRisks` and `priorVerification` EMPTY (pending downstream reviews are not risks; conventions 7a); list only final passing commands in `verification`.
