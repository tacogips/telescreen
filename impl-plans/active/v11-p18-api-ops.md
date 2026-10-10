# V11-P18 API: Collectors (Health, Coverage), Police Health, Directory Identities and Team Lead, Human Tasks (assignee=me fix, owner_review)

**Status**: Completed
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

- [x] Endpoints, fields and the `assignee=me` fix implemented as specified.
- [x] Tests pass; scoped gates pass; the typelint grep is empty; the wide pytest exit is
      0.
- [x] Progress Log complete.

## Progress Log

### Session 2026-10-10 (Step 6 implementation)

- Implemented collector health and coverage list/detail endpoints, Police worker health,
  team lead and keyed identity endpoints, member/team-scoped `assignee=me`, and atomic
  `owner_review` completion outcomes. Added API regressions under `tests/api/v11_ops`.
- Hashes (before -> after):
  - `src/telescreen/api/routers/collectors.py`: `44863a05` -> `32d5a57b`.
  - `src/telescreen/api/routers/police.py`: `f7a18dac` -> `62d3a9c7`.
  - `src/telescreen/api/routers/directory.py`: `d4b3f2d4` -> `1bd48d3f`.
  - `src/telescreen/api/routers/human_tasks.py`: `aba40918` -> `ec030798`.
  - `tests/api/v11_ops/test_api_ops.py`: absent -> `d6e85ad4`.
  - This plan: `1adc6c19` before this session update; final hash recorded after update.
- Passing gates:
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops && uv run --frozen ruff check --no-cache src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops && uv run --frozen ty check src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops` -> exit 0; 5 files formatted, Ruff and ty passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/api/v11_ops 2>&1 | tee /tmp/telescreen-v11-p18-api-ops-pytest.log` -> exit 0; 4 passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p18-api-ops-typelint.log 2>&1; lint_status=$?; grep -E '(^|/)(src/telescreen/api/routers/(collectors|police|directory|human_tasks)\\.py|tests/api/v11_ops/)' /tmp/telescreen-v11-p18-api-ops-typelint.log > /tmp/telescreen-v11-p18-api-ops-typelint-owned.log; filter_status=$?; if [ -s /tmp/telescreen-v11-p18-api-ops-typelint-owned.log ]; then cat /tmp/telescreen-v11-p18-api-ops-typelint-owned.log; exit 1; fi; printf 'typelint_exit=%s scoped_filter_exit=%s owned_diagnostics=0 exit=0\\n' "$lint_status" "$filter_status"` -> exit 0; whole repository `typelint: 344 file(s) clean`, no owned diagnostics.
- Required wide verification (not passing):
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/e2e tests/api tests/collectors tests/police > /tmp/telescreen-v11-p18-api-ops-wide.log 2>&1; exit_status=$?; tail -n 35 /tmp/telescreen-v11-p18-api-ops-wide.log; printf 'exit=%s\\n' "$exit_status"; exit "$exit_status"` -> exit 1; 147 passed, 1 failed in `tests/e2e/test_flow_github_remediation.py::test_github_remediation_escalates_rejects_evidence_and_rechecks`.
  - The failing e2e uses a memberless admin token and omits `assignee`, then expects an unfiltered `{items}` list. The accepted contract and plan require 403 for a memberless token when `assignee` is omitted or `me`; admin callers may explicitly request `assignee=all`. Do not relax the fail-closed behavior to satisfy the old test.
- Reconciliation request: the required wide suite cannot pass until the e2e caller at `tests/e2e/test_flow_github_remediation.py` explicitly requests `assignee=all` (or uses a member-scoped token). That path is outside this plan's `writePaths`. Assign the test update to serial reconciliation or amend this plan's writePaths before rerunning the wide gate. Until then the combined verification criterion remains open and this plan is Blocked.
- A pre-fix typelint run identified one missing local `MemberRow` annotation in the new test. It was corrected; the final whole-repository typelint run above is clean.

**Owner resolution (2026-10-10)**: The fail-closed contract stands (memberless token with
omitted or `me` assignee gets 403; admins request `assignee=all`). The owner updated the v1 caller
`tests/e2e/test_flow_github_remediation.py` (both `/api/human-tasks?status=open` calls now pass
`&assignee=all`). `pytest tests/e2e/test_flow_github_remediation.py tests/api/v11_ops` passes
(5 passed). Re-run the wide gate; this is not a blocker. Report per conventions 7a.

### Session: 2026-10-10 (Step 6 verification rerun)
**Tasks Completed**: Re-ran p18 scoped gates and the required wide suite after the owner-supplied e2e caller correction; closed the completion criterion.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None. The owner correction in `tests/e2e/test_flow_github_remediation.py` remains outside this plan's writePaths and was preserved as supplied.
**Hashes**: `impl-plans/active/v11-p18-api-ops.md` before=`e0ff552cfe331a4a7417ab3d3d253aed9e230238d2419ddf457cfcd7d8c4eaa1`; after=`d21677fc205bc6fb1c352668407c657e88e1fc3ac37a43326292a2302305de99` before this audit detail.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops` -> exit 0, 5 files already formatted.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops` -> exit 0, all checks passed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/api/routers/collectors.py src/telescreen/api/routers/police.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/human_tasks.py tests/api/v11_ops` -> exit 0, all checks passed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/api/v11_ops 2>&1 | tee /tmp/telescreen-v11-p18-api-ops-pytest-final.log` -> exit 0, 4 passed (log: `/tmp/telescreen-v11-p18-api-ops-pytest-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p18-api-ops-typelint-final-verified.log 2>&1; lint_exit=$?; grep -E '(^|/)(src/telescreen/api/routers/(collectors|police|directory|human_tasks)[.]py|tests/api/v11_ops/)' /tmp/telescreen-v11-p18-api-ops-typelint-final-verified.log > /tmp/telescreen-v11-p18-api-ops-typelint-owned-final-verified.log; grep_exit=$?; if [ -s /tmp/telescreen-v11-p18-api-ops-typelint-owned-final-verified.log ]; then cat /tmp/telescreen-v11-p18-api-ops-typelint-owned-final-verified.log; exit 1; fi; printf 'typelint_exit=%s scoped_grep_exit=%s owned_diagnostics=0 exit=0\\n' "$lint_exit" "$grep_exit"` -> exit 0; whole-repository typelint `344 file(s) clean`, no p18 diagnostics (logs: `/tmp/telescreen-v11-p18-api-ops-typelint-final-verified.log`, `/tmp/telescreen-v11-p18-api-ops-typelint-owned-final-verified.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/e2e tests/api tests/collectors tests/police > /tmp/telescreen-v11-p18-api-ops-wide-final-rerun.log 2>&1` -> exit 0, 148 passed (log: `/tmp/telescreen-v11-p18-api-ops-wide-final-rerun.log`).
- `git diff --check -- impl-plans/active/v11-p18-api-ops.md && test "$(grep -c '^- \\[ \\]' impl-plans/active/v11-p18-api-ops.md)" -eq 0` -> exit 0; no whitespace errors and all completion criteria checked; plan is under 400 lines.
**Notes**: The initial wide rerun wrapper used zsh's read-only `status` variable for exit capture and returned a shell error after pytest. It is superseded by the complete successful rerun above. The owner-supplied e2e change explicitly requests `assignee=all`; the fail-closed API behavior is unchanged.
