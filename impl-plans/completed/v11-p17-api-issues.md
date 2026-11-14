# V11-P17 API: Issues (v1.1 fields, remediation, resolve, occurrences), Exceptions and Policies Routers

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md ("v1.1 endpoints": Issues, Exceptions, Policies rows); design-docs/specs/design-v1-1-dspm-adoption.md sections 4, 5, 9, 11
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p17-api-issues`
- planPath: `impl-plans/active/v11-p17-api-issues.md`
- wave: 3
- dependsOn: `v11-p05-issues-record`, `v11-p06-issues-remediation`, `v11-p07-policy-risk`, `v11-p14-directory-search`
- writePaths:
  - `impl-plans/active/v11-p17-api-issues.md`
  - `src/telescreen/api/routers/issues.py`
  - `src/telescreen/api/routers/exceptions.py` (new)
  - `src/telescreen/api/routers/policies.py` (new)
  - `tests/issues/test_issues_api.py`
  - `tests/api/v11_issues` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (section 5 API rules).

## Intent and Context

Expose the v1.1 issue, exception and policy operations over HTTP, using the services
built in wave 2:

- `IssueService` (v11-p05);
- `IssueRemediationService`, `ExceptionService`, `occurrences.linked` (v11-p06);
- `PolicyStore` (v11-p07);
- the `OwnerDirectory` port (implemented by v11-p14).

`wiring.py` (v11-p19, wave 4) builds the routers. Until then, every new `*RouterDeps`
field is appended with a default (`None`), and endpoints whose dependency is None return
503 `internal_error` with a static detail. `wiring.py` keeps compiling.

Imitate:

- `api/routers/issues.py` (`IssuesRouterDeps`, keyset cursor in `list_issues`,
  `_issue_json`);
- `api/routers/workflows.py` (validate, create version, activate; 422 `{valid, errors}`);
- `api/common/auth.py:require_scope`, `Principal.has`.

## Non-goals

- No service or business-logic changes; call the services. If a service is missing
  something, file a reconciliation request.
- No changes to other routers (v11-p18) or `wiring.py`.

## Invariants

- Bodies are pydantic dataclasses. Responses are built with `JsonValue` + `dump_json`, as
  `issues.py` does.
- No response field contains a raw value, an email, an `owner_ref` / identity key, or an
  exception `reason` beyond its stored redacted text.
- Errors never echo input (static details).
- `actor_ref` comes from `Principal` exactly as the existing PATCH does; `is_admin` =
  `principal.has(ApiScope.ADMIN)`.

## Deliverables

1. **`issues.py`**:
   - `IssuesRouterDeps` appends:
     - `remediation: IssueRemediationService | None = None`;
     - `exceptions: ExceptionService | None = None`;
     - `owners: OwnerDirectory | None = None`;
     - `resolver: RecipientResolver | None = None`.
   - `GET /api/issues`:
     - `status` accepts the new `IssueStatus` values; `exposure: Visibility | None`;
       `sort` regex adds `risk` (keyset on `risk_score desc, id`);
     - `assignee=me` -> `owners.member_ref(principal.member_id)`;
     - `assignee=my_teams` -> issues whose assignee team id is in the principal's teams
       (map refs to team ids through `resolver.assignee_of`);
     - a token without a member asking for `me` / `my_teams` -> 403 `forbidden`.
   - `_issue_json` adds `base_severity`, `exposure_visibility`, `risk_score`,
     `policy_name`, `policy_version` (and keeps `resolution`).
   - `GET /api/issues/{id}` adds:
     - `exposure` (parsed from `exposure_json`, enums and counts only);
     - `exposure_changed_at`, `asset_tier`;
     - `assignee_ref` (via the snapshot);
     - `owner: {present: bool, member_ref: str | null}` (`member_for_owner`; never the
       keyed ref);
     - `active_exception` (`requested` or `active`, or null).
   - `PATCH`: unchanged code path; the service enforces the new table.
   - `POST /api/issues/{id}/remediation` (write) -> `{issue_ids, rescans_enqueued}`.
     Index every returned issue after commit (`deps.indexer.index_issue`).
   - `POST /api/issues/{id}/resolve` (write; the service enforces admin for
     `manual_unverified`).
   - `GET /api/issues/{id}/occurrences` (read) -> `{items, key_versions}`.
   - `POST /api/issues/{id}/exceptions` (write) -> `{id, status}`.
2. **`exceptions.py`** `build_exceptions_router(ExceptionsRouterDeps(ctx, service,
   indexer))`:
   - `GET /api/exceptions?status&issue_id&approver=me&limit&cursor` (read);
     `approver=me` resolves through `owners.member_ref`.
   - `POST /api/exceptions/{id}/approve {note?}`, `/reject {note?}`, `/revoke` (write;
     the service authorizes approver or admin). Re-index the issue after commit.
   - Item fields: `id, issue_id, reason, duration, status, requested_by, approver_ref,
     decided_by, decided_at, expires_at, created_at`.
3. **`policies.py`** `build_policies_router(PoliciesRouterDeps(ctx, store))`:
   - `GET /api/policies`, `GET /api/policies/{name}` and
     `GET /api/policies/{name}/versions/{v}` (read).
   - `POST /api/policies/validate` -> `{valid, errors: [{path, code}]}` (read).
   - `POST /api/policies` (admin) -> 201 version view, or 422 `{valid: false, errors}`.
   - `POST /api/policies/{name}/activate {version}` and `POST /api/policies/{name}/disable`
     (admin).
   - Raw value in source -> 422 `raw_value_detected`, without echo.

## Pitfalls

- Do not compute `owner.member_ref` by exposing `owner_ref`. The API must never return
  `owner_ref`.
- Index after the transaction commits, never inside it (architecture section 5.8).
- `sort=risk` must use a stable keyset (`risk_score`, `id`) with a cursor encoding both
  values. Imitate the existing `_cursor`.

## Tests

`tests/api/v11_issues/`: build the app with `FastAPI()` + `include_router`, as
`tests/issues/test_issues_api.py` does (SQLite harness, real services, fake ports).

- list `exposure=public`, `sort=risk` order and cursor, `assignee=me` with a member token,
  403 without a member;
- detail includes `exposure`, `owner.present` and `active_exception`; the response text
  has no `owner_ref` value and no `identity` hex;
- remediation 200 -> the issue becomes `pending_verification`; from `resolved` -> 422
  `invalid_transition`;
- resolve `manual_unverified` with a write token -> 403; with admin -> 200;
- PATCH `{status: "ignored"}` -> 422 `invalid_transition`;
- exception request -> approve by an admin -> issue `ignored`; list `approver=me`;
- policies: validate errors list paths only; create (admin) -> version 1; a non-admin ->
  403; a sample secret in source -> 422 `raw_value_detected` and the response body does
  not contain the sample (`tests.support.samples.assert_no_leaks`); activate -> 200.

Update `tests/issues/test_issues_api.py` only where v1.1 behavior intentionally changed.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/api/routers/issues.py src/telescreen/api/routers/exceptions.py src/telescreen/api/routers/policies.py`
and `<tests>` = `tests/api/v11_issues tests/issues/test_issues_api.py`. Also
`uv run --frozen pytest -p no:cacheprovider tests/issues tests/e2e > /tmp/telescreen-v11-p17-api-issues-wide.log 2>&1; echo "exit=$?"`
-> `exit=0`, or only failures in tests exercising
`src/telescreen/api/routers/{collectors,police,directory,human_tasks}.py` while the
concurrent wave-3 plan v11-p18 is still in progress; record their test ids in the
Progress Log as informational. Every failure in a path this plan owns blocks completion.

## Completion Criteria

- [x] All endpoints of the three rows exist with the stated scopes and shapes.
- [x] Tests pass; scoped gates pass; the typelint grep is empty; the wide pytest exits 0
      or fails only on the named concurrent v11-p18 router paths.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 18:20
**Tasks Completed**: Added issue list exposure/risk filters and risk keyset cursor; v1.1 list/detail projections; member and team assignee resolution; remediation, reasoned resolve, release, linked occurrence, exception request/list/decision, and placement policy endpoints. Added feature-off 503 `internal_error` responses and API regression tests for the endpoint contracts, privacy, scopes, state changes, and pagination.
**Tasks In Progress**: Downstream p19 router wiring and independent workflow review remain with their owning steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/api/routers/issues.py` before=4bd4364ea318e4d498167a4091005d35e347ac8c9bdca30a6e9a0ad806b82fc5 after=182468fa1d4b2cc6f61f44e392b39addc4051df29a472cf3f3d8ac912951cebc; `src/telescreen/api/routers/exceptions.py` before=absent after=e612335590f9084873f97383352e157adb07942ca7e0e688f8e611b8622d281c; `src/telescreen/api/routers/policies.py` before=absent after=72f34bff0dc025e92395f638b19a2ffcf8c61b28c971d068a6f9fcae18a2e19d; `tests/api/v11_issues/test_issues_router.py` before=absent after=802d5c91561b6b2add25df2a0c98e417956772d1c91d5cc58dffddb16e152369; `tests/issues/test_issues_api.py` before=b9019c02389e179c63bc39467b243655b1d26c8f13d239481ffc3a68941c0ef1 after=b9019c02389e179c63bc39467b243655b1d26c8f13d239481ffc3a68941c0ef1.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/api/routers/issues.py src/telescreen/api/routers/exceptions.py src/telescreen/api/routers/policies.py tests/api/v11_issues tests/issues/test_issues_api.py` -> exit 0 (log: `/tmp/telescreen-v11-p17-api-issues-format-final5.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/api/routers/issues.py src/telescreen/api/routers/exceptions.py src/telescreen/api/routers/policies.py tests/api/v11_issues tests/issues/test_issues_api.py` -> exit 0 (log: `/tmp/telescreen-v11-p17-api-issues-ruff-final5.log`).
- `uv run --frozen ty check src/telescreen/api/routers/issues.py src/telescreen/api/routers/exceptions.py src/telescreen/api/routers/policies.py tests/api/v11_issues tests/issues/test_issues_api.py` -> exit 0 (log: `/tmp/telescreen-v11-p17-api-issues-ty-final5.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/api/v11_issues tests/issues/test_issues_api.py` -> exit 0, 5 passed (log: `/tmp/telescreen-v11-p17-api-issues-pytest-final5.log`).
- `uv run --frozen python tools/typelint.py` plus the `(^|/)` owned-path grep -> typelint exit 0, zero owned diagnostics (logs: `/tmp/telescreen-v11-p17-api-issues-typelint-final5.log`, `/tmp/telescreen-v11-p17-api-issues-typelint-owned-final5.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/issues tests/e2e` -> exit 1, 90 passed and 1 failed (log: `/tmp/telescreen-v11-p17-api-issues-wide-final4.log`). The sole failure is `tests/e2e/test_flow_github_remediation.py::test_github_remediation_escalates_rejects_evidence_and_rechecks`, at its `/api/human-tasks?status=open` assertion; this exercises the concurrent p18-owned human-tasks router. The earlier wide run's issue-detail failures were fixed in this plan and do not recur in final4.
**Notes**: All implementation changes are within the plan writePaths. The working tree contains concurrent sibling edits elsewhere. Feature-off service routes return HTTP 503 with static `internal_error`; existing issue detail remains readable before p19 binds owner lookup, with safe null refs. Per-edit intentions and the pre-node snapshot reference are in `tmp/telescreen-v11-20261010-c1/v11-p17-api-issues/session-step6/`; downstream wiring and formal review are not claimed complete.

### Session: 2026-10-10 18:27 — adversarial repair
**Finding addressed**: `P17-ADV-OWNER-MEMBER-REF-001`. `get_issue` now returns the single `member_for_owner` result directly as `owner.member_ref`; it no longer passes a `member:<handle>` through `member_ref()`. The regression test seeds `MemberIdentityRow` for `member-api-owner`, asserts `owner.present` is true and `owner.member_ref` is `member:api-owner`, and checks both `email:` plus the 64-character identity key and the key alone are absent from response bytes. Assignee reference lookups remain unchanged.
**Reconciliation request**: `src/telescreen/issues/routing.py:43-45` (v11-p05 ownership) has the same `member_for_owner` result being treated as a member ID. The p17 branch did not edit that file; v11-p05 or serial reconciliation should use the result directly and add a real `DirectoryOwnerResolver` routing test.
**Verification**: The six p17 gates were rerun after the repair. Format, Ruff, ty, scoped pytest, typelint, owned-path filter, and completion-checkbox gate all exited 0. Scoped pytest reports 5 passed. Logs: `/tmp/telescreen-v11-p17-api-issues-format-resume2-final1.log`, `-ruff-resume2-final1.log`, `-ty-resume2-final1.log`, `-pytest-resume2-final1.log`, `-typelint-resume2-final1.log`; empty owned-path output: `/tmp/telescreen-v11-p17-api-issues-typelint-owned-resume2-final1.log`. Typelint used `(^|/)` and reported zero owned diagnostics. Focused regression run: `/tmp/telescreen-v11-p17-owner-member-ref-regression.log`, 1 passed.
**Hashes after repair**: `issues.py` `621aec9abdea4d21fbc324b07e424299615efbfd886e003f9b2e96602ed722f8`; `exceptions.py` `e612335590f9084873f97383352e157adb07942ca7e0e688f8e611b8622d281c`; `policies.py` `72f34bff0dc025e92395f638b19a2ffcf8c61b28c971d068a6f9fcae18a2e19d`; `test_issues_router.py` `fc127a58a10376a3d2f4ed0256534b971c13ba3e7710fd96578700ce62174585`; unchanged `tests/issues/test_issues_api.py` `b9019c02389e179c63bc39467b243655b1d26c8f13d239481ffc3a68941c0ef1`.
**Integration note**: The earlier wide `tests/issues tests/e2e` aggregate remains nonzero due to the recorded v11-p18 human-tasks failure. Serial integration/v11-p21 must rerun it on the combined tree without tolerance after p18 and p19 land. This is not p17-owned.
**Evidence**: Per-edit intentions and the pre-node snapshot reference are recorded under `tmp/telescreen-v11-20261010-c1/v11-p17-api-issues/session-step6/`.
