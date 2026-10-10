# V11-P17 API: Issues (v1.1 fields, remediation, resolve, occurrences), Exceptions and Policies Routers

**Status**: Ready
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
-> `exit=0`. The only tolerated failures are tests that exercise
`src/telescreen/api/routers/{collectors,police,directory,human_tasks}.py` while the
concurrent wave-3 plan v11-p18 is still in progress; record their test ids in the
Progress Log as informational. Every failure in a path this plan owns blocks completion.

## Completion Criteria

- [ ] All endpoints of the three rows exist with the stated scopes and shapes.
- [ ] Tests pass; scoped gates pass; the typelint grep is empty; the wide pytest exit is
      0.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
