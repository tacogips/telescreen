# V11-P18 API: Collectors (Health, Coverage), Police Health, Directory Identities and Team Lead, Human Tasks (assignee=me fix, owner_review)

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md ("v1.1 endpoints": Coverage, Collectors, Police, Human tasks, Directory rows); design-docs/specs/design-v1-1-dspm-adoption.md sections 6, 7, 8
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p18-api-ops`
- planPath: `impl-plans/active/v11-p18-api-ops.md`
- wave: 3
- dependsOn: `v11-p05-issues-record`, `v11-p06-issues-remediation`, `v11-p08-collectors-core`, `v11-p12-police-bureau`, `v11-p13-workflow`, `v11-p14-directory-search`
- writePaths:
  - `impl-plans/active/v11-p18-api-ops.md`
  - `src/telescreen/api/routers/collectors.py`
  - `src/telescreen/api/routers/police.py`
  - `src/telescreen/api/routers/directory.py`
  - `src/telescreen/api/routers/human_tasks.py`
  - `tests/api/v11_ops` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (section 5).

## Intent and Context

Expose the operational v1.1 data and fix the human-tasks `assignee=me` defects. The v1
router at `api/routers/human_tasks.py:list_tasks` (about line 75) has two:

- it builds `member:<member_id>` while refs are keyed by handle;
- a token without a member that passes `assignee=me` gets an unfiltered list.

Services used:

- `CoverageRecorder`, `list_health` (v11-p08);
- `list_worker_health` (v11-p12);
- `IdentityService`, `DirectoryService.set_team_lead`, `DirectoryOwnerResolver`
  (v11-p14);
- the `IssueRemediation` and `ResourceIssueReader` ports (v11-p06 / v11-p05).

New `*RouterDeps` fields are appended with defaults (None), so `wiring.py` keeps
compiling until v11-p19.

## Non-goals

- No business logic beyond request validation and authorization already described. No
  `wiring.py`.
- The Police contract endpoints (`/api/police/v1/*`) are unchanged.

## Invariants

- Coverage responses: `resource_id` = the first 12 hex characters of `resource_key`,
  never a locator.
- Identity endpoints never return or echo `value`.
- Human-task completion of an `owner_review` task performs the `IssueRemediation` call
  and the run signal in the SAME transaction. If the remediation call raises, nothing is
  committed and the error is returned.

## Deliverables

1. **`collectors.py`**:
   - `CollectorsRouterDeps` appends `coverage: CoverageRecorder | None = None`,
     `health_view: CollectorHealthLister | None = None` (a small Protocol in this file
     wrapping `list_health`, or the concrete function holder from v11-p08; read it),
     `issues: ResourceIssueReader | None = None`.
   - `GET /api/collectors` items add `health {status, conditions, last_success_at,
     last_failure_at, consecutive_failures, last_event_at, watch_expires_at,
     event_only}`, or null when unavailable.
   - `GET /api/collectors/{id}/coverage` -> `{since, counts: [{state, reason, count}],
     unscannable_total}`. `since` = now - 7 days.
   - `GET /api/collectors/{id}/coverage/resources?state&limit&cursor` -> items
     `{resource_id, source, state, reason, truncated_bytes, exposure_visibility,
     first_seen_at, last_attempt_at, issues: [{issue_id, short_id}]}` plus
     `next_cursor`.
   - Unknown config -> 404.
2. **`police.py`**: `GET /api/police/workers` items add `health {status, conditions}`
   from `list_worker_health`.
3. **`directory.py`**:
   - `DirectoryRouterDeps` appends `identities: IdentityService | None = None`.
   - `TeamBody` gains `lead: str | None = None`; `GET /api/teams` items add `lead` (the
     handle or null); create passes `lead_handle`; a new `PUT /api/teams/{slug}/lead
     {lead}` (admin) calls `set_team_lead`.
   - `GET /api/members/{handle}/identities` (read), `POST` with `{kind, value}` (admin;
     `kind` limited to `email` or `github_login`, because `slack_user` is derived) ->
     201 `{id, kind, origin, key_prefix, created_at}`.
   - `DELETE /api/members/{handle}/identities/{id}` (admin) -> 204.
4. **`human_tasks.py`**:
   - `HumanTasksRouterDeps` appends `owners: OwnerDirectory | None = None` and
     `remediation: IssueRemediation | None = None`.
   - `list_tasks`:
     - `assignee=me` or omitted with a member: refs = `owners.member_ref(member_id)` plus
       `owners.team_refs_of(member_id)`; filter `assignee_ref IN refs`;
     - a token without a member asking for `me` or omitting `assignee` -> 403
       `forbidden` (admins may pass an explicit ref or `all`; keep v1 explicit-ref
       behavior for admins);
     - items include `kind`.
   - `CompleteHumanTaskBody` gains `exception_duration: ExceptionDuration | None = None`.
     For a task whose kind is `owner_review`:
     - `exception` requires a non-empty `comment` and an `exception_duration` that is not
       `forever` (else 422 `validation_error`);
     - the outcome effects run before `signaler.signal`, with `run.issue_id`:
       - remove / restrict -> `remediation.request_verification(session, issue_id,
         RemediationAction(outcome), False, actor_ref, comment)`;
       - exception -> `request_exception(session, issue_id, comment, duration,
         actor_ref, is_admin)`;
       - not_mine -> `release_to_team(...)`.
     - `remediation` None -> 503.

## Pitfalls

- `assignee=me` must include team refs, otherwise team-assigned owner reviews never show
  in My tasks.
- Owner-review completion must check that the run has an `issue_id`; without one -> 409
  `conflict`.
- Never put `value` into audit details or responses. The `IdentityService` already
  audits kind only.

## Tests (`tests/api/v11_ops/`)

Imitate the existing router tests (`tests/e2e/test_api_routes.py` for app construction,
`tests/api/common/test_api.py` for tokens).

- collectors list carries a `health` object; coverage summary counts; coverage resources:
  `resource_id` length 12, and the linked issue appears as `{issue_id, short_id}`. Seed
  that issue with display locator `docs/canary-v11.txt` and assert this string is absent
  from the response body;
- police workers carry `health.status`;
- identities:
  - POST github_login -> 201 with `key_prefix`; the response and DB text do not contain
    the value;
  - POST `slack_user` -> 422;
  - DELETE manual -> 204;
  - a non-admin -> 403;
- team lead PUT and GET;
- human tasks:
  - `assignee=me` returns tasks assigned to `member:<handle>` and to the member's team;
  - a token without a member -> 403;
  - owner_review `remove` -> the issue becomes `pending_verification` and the run is
    signalled;
  - `exception` without a comment -> 422;
  - `exception` with `30d` -> an exception requested;
  - `not_mine` -> the member assignee cleared;
  - a remediation error rolls back (task still open).

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py`
and `<tests>` = `tests/api/v11_ops`. Also
`uv run --frozen pytest -p no:cacheprovider tests/e2e tests/api tests/collectors tests/police > /tmp/telescreen-v11-p18-api-ops-wide.log 2>&1; echo "exit=$?"`
-> `exit=0`. The only tolerated failures are tests under `tests/api/v11_issues` or tests
that exercise `src/telescreen/api/routers/{issues,exceptions,policies}.py` while the
concurrent wave-3 plan v11-p17 is still in progress; record their test ids in the
Progress Log as informational. Every failure in a path this plan owns blocks completion.

## Completion Criteria

- [ ] Endpoints, fields and the `assignee=me` fix implemented as specified.
- [ ] Tests pass; scoped gates pass; the typelint grep is empty; the wide pytest exit is
      0.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
