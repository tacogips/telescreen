# V12-P06 RBAC Enforcement: Permission Dependency on Every Route

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1 (D10, D11, D18), 5.1, 5.2, 6.2 step 1, 6.4; design-docs/specs/design-api-and-ui.md "Authentication and errors" (v1.2 paragraph)
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p06-rbac-enforcement`
- planPath: `impl-plans/active/v12-p06-rbac-enforcement.md`
- wave: 2
- dependsOn: `v12-p01-foundation`, `v12-p02-db`
- writePaths:
  - `impl-plans/active/v12-p06-rbac-enforcement.md`
  - `src/telescreen/api/common`
  - `src/telescreen/api/routers/audit.py`
  - `src/telescreen/api/routers/chat.py`
  - `src/telescreen/api/routers/collectors.py`
  - `src/telescreen/api/routers/directory.py`
  - `src/telescreen/api/routers/escalation.py`
  - `src/telescreen/api/routers/evidence.py`
  - `src/telescreen/api/routers/exceptions.py`
  - `src/telescreen/api/routers/health.py`
  - `src/telescreen/api/routers/human_tasks.py`
  - `src/telescreen/api/routers/ingest.py`
  - `src/telescreen/api/routers/issues.py`
  - `src/telescreen/api/routers/map_jobs.py`
  - `src/telescreen/api/routers/police.py`
  - `src/telescreen/api/routers/policies.py`
  - `src/telescreen/api/routers/runs.py`
  - `src/telescreen/api/routers/search.py`
  - `src/telescreen/api/routers/workflows.py`
  - `tests/api/common`
  - `tests/api/v12_rbac` (new directory)
  - `tests/workflow/engine/test_engine_extended.py` (Principal construction lines only)
  - `tests/evidence/test_evidence.py` (fixture-only: bound member role seeding)
  - `tests/api/v11_issues/test_issues_router.py` (fixture-only: bound member role seeding)
  - `tests/api/v11_ops/test_api_ops.py` (fixture-only: bound member role seeding)
  - `tests/leak/flows_v11.py` (fixture-only: bound member role seeding)
- sharedPaths: none
- sharedPathNotes (the intent for the four fixture files): fixture-only. Give the
  token-bound member the role that matches its v1 scopes (admin scope -> admin, write ->
  responder, read -> viewer). Change no assertion and no token scope.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Replace scope checks with per-route permissions through ONE dependency. Accept OIDC
access tokens as well as API tokens. The table below reproduces v1 behavior exactly for
every existing token, because the scope permission sets of p01 mirror the v1 scopes. The
route-walk and scope-compatibility tests that need the full app are in v12-p11. This
plan unit-tests the dependency and the router choices.

Imitate: `api/common/auth.py:require_scope` (structure and static error texts) and
`api/common/errors.py:http_status_for`.

## Non-goals

- No JWT validation code; it sits behind the `BearerVerifier` port (v12-p08).
- No member provisioning; it sits behind `MemberProvisioner.member_for_identity`
  (v12-p07).
- No new routers (v12-p10), no wiring (v12-p11).
- No change to route paths, bodies, responses or non-auth behavior.

## Invariants

- `ApiContext` keeps its first three positional fields and appends `verifier:
  BearerVerifier | None = None` and `identities: MemberProvisioner | None = None`, so
  every existing `ApiContext(sessions, clock, audit)` call compiles.
- `hash_token` stays exported (`directory/tokens.py` imports it).
- Error details are static strings; tokens are never logged or echoed.
- For every existing route, the set of v1 scopes that pass is unchanged for unbound
  tokens, and for bound tokens whose member role covers the token's scopes (admin role
  covers every scope, responder covers read and write, viewer covers read). A bound token
  whose member has role NULL, or no `MemberRow`, gets no human permissions (D11). This is
  intended. Pre-existing fixtures that bind tokens are therefore given matching roles
  (deliverable 6) instead of changing D11.

## Deliverables

1. **`api/common/auth.py`**:
   - `Principal(kind: PrincipalKind, token_id: str | None, member_id: str | None, role:
     Role | None, permissions: frozenset[Permission])`, frozen. Methods: `allows(p) ->
     bool`; `actor_ref()` -> `member:<member_id>` when set, else `token:<token_id>`
     (unchanged semantics).
   - `async def authenticate(ctx: ApiContext, request: Request) -> Principal`:
     - a missing or non-`Bearer ` header -> 401 `UNAUTHORIZED` "authentication
       required";
     - a value with exactly two `.` -> JWT path: no `ctx.verifier` -> 401; otherwise
       `verified = await ctx.verifier.verify(token)` (it raises 401 `INVALID_TOKEN` or 503
       `IDP_UNAVAILABLE`), then `ctx.identities.member_for_identity(session,
       verified.issuer, verified.subject)` in a read session. None -> 401 `UNAUTHORIZED`
       "sign-in required". Inactive or role None -> 403 `FORBIDDEN`. Permissions =
       `role_permissions(role)`;
     - any other value -> token path: hash lookup as today; revoked -> 401; then
       `bound = row.member_id is not None`; load `MemberRow` when bound (role None when
       the member is missing or inactive); permissions =
       `effective_token_permissions(scopes, role, bound)`.
   - `@dataclass(frozen=True) class PermissionDependency`: fields `ctx` and
     `permission`; `async def __call__(self, request: Request) -> Principal` runs
     `authenticate`, then `allows` or 403 "permission is not granted".
   - `require_permission(ctx, permission) -> PermissionDependency`.
   - `@dataclass(frozen=True) class ExternalAuthDeclaration`: field `kind:
     ExternalAuthKind`; `async def __call__(self) -> None` is a no-op marker.
     `declare_external(kind) -> ExternalAuthDeclaration`.
   - Remove `require_scope` and `Principal.has`; update `api/common/__init__.py`
     exports.
2. **`api/common/context.py`**: the two appended fields.
3. **`api/common/errors.py`** `http_status_for`:
   - `INVALID_TOKEN` -> 401;
   - `NO_ROLE`, `NOT_PROVISIONED` -> 403;
   - `IDENTITY_CONFLICT`, `LAST_ADMIN` -> 409;
   - `UNKNOWN_TEMPLATE`, `UNKNOWN_VARIABLE`, `TEMPLATE_EVENT_MISMATCH` -> 422;
   - `IDP_UNAVAILABLE` -> 503.
4. **Routers**: replace each `require_scope(deps.ctx, ApiScope.X)` with
   `require_permission(deps.ctx, Permission.Y)` per the table, and add external
   declarations with `dependencies=[Depends(declare_external(kind))]` on the route
   decorator:

| Router | Route(s) | Permission |
|--------|----------|-----------|
| `health.py` | GET /api/health | external `public` |
| `issues.py` | GET list, stats, detail, events, history, occurrences | `issues.read` |
| | POST remediation, resolve, release; PATCH; POST comments | `issues.write` |
| | POST /api/issues/{id}/exceptions | `exceptions.write` |
| `exceptions.py` | GET /api/exceptions | `exceptions.read` |
| | POST approve, reject, revoke | `exceptions.write` |
| `workflows.py` | GET list, schema, version, detail | `workflows.read` |
| | POST validate, POST create | `workflows.write` |
| | POST activate | `workflows.activate` |
| | POST /api/workflows/{name}/runs | `runs.write` |
| `runs.py` | GET list, detail / POST cancel | `runs.read` / `runs.write` |
| `human_tasks.py` | GET / POST complete | `human_tasks.read` / `human_tasks.complete` |
| `evidence.py` | PUT / GET x3 | `evidence.upload` / `evidence.read` |
| `policies.py` | GET x3, POST validate / POST create, activate, disable | `policies.read` / `policies.write` |
| `escalation.py` | GET / POST, PUT | `escalation.read` / `escalation.write` |
| `directory.py` | GET members, teams, identities / POST members, teams, identities; PUT team members, lead; DELETE identity | `directory.read` / `directory.write` |
| `collectors.py` | GET list, coverage, coverage resources / POST create, run, sweep; PATCH | `collectors.read` / `collectors.write` |
| | POST /api/collectors/{kind}/webhook | external `provider_signature` |
| `map_jobs.py` | GET x2 / POST cancel, retry-failed | `map_jobs.read` / `map_jobs.write` |
| `police.py` | GET /api/police/v1/openapi.json, POST register, claim, heartbeat, result, callbacks | external `police_contract` |
| | GET /api/police/workers / POST disable | `police.read` / `police.write` |
| `audit.py` | GET search, verify | `audit.read` |
| `search.py` | GET /api/search | `search.read` |
| `chat.py` | POST /api/chat | `chat.use` |
| `ingest.py` | POST scan, findings; GET sources config | `ingest.push` |

   In-handler admin checks (6 sites):
   - `human_tasks.py:91` (`assignee == "all"`), `:126` (complete any) and `:187` ->
     `principal.allows(Permission.HUMAN_TASKS_MANAGE_ANY)`;
   - `issues.py:525` (resolve `manual_unverified`) -> `ISSUES_RESOLVE_UNVERIFIED`;
   - `issues.py:565` (exception request admin path) -> `EXCEPTIONS_MANAGE_ANY`;
   - `exceptions.py:128` -> `EXCEPTIONS_MANAGE_ANY`.
5. **`tests/workflow/engine/test_engine_extended.py`**: update only the
   `Principal(...)` constructions (lines around 680-684) to the new shape with
   `permissions=scope_permissions(frozenset((ApiScope.WRITE,)))`.
6. **Bound-token fixtures** (fixture-only; read each file fresh; p02 adds
   `MemberRow.role`; keep every assertion and every token scope unchanged):
   - `tests/evidence/test_evidence.py`: the tokens at about :408 and :513 bind
     `member_id="member-1"` with read+write, and no `MemberRow` exists. Insert
     `MemberRow(id="member-1", ..., role="responder")` in the same session, following the
     `MemberRow` field style used in `tests/api/v11_issues/test_issues_router.py:87`.
   - `tests/api/v11_issues/test_issues_router.py`: the `MemberRow` at about :87
     (`member-api-owner`, bound read+write token at :135) gets `role="responder"`. The
     `MemberRow` at about :317 (`member-lifecycle`, bound read+write token at :342 and
     read+write+admin token at :348) gets `role="admin"`. Its admin token keeps ALL
     permissions, and its write token gets the WRITE set, as in v1.
   - `tests/api/v11_ops/test_api_ops.py`: the member bound to `member_token` (about :488,
     read scope) gets `role="viewer"`, set on its `MemberRow` where the fixture creates
     it.
   - `tests/leak/flows_v11.py:exception_flow` (about :305-322): after `POST
     /api/members` returns `member_id`, set `MemberRow.role = "responder"` through
     `world.database.sessions` in the same `begin()` block that creates the read+write
     `member_token`.
   - Then run `grep -rn "member_id=\|TokenService" tests` and confirm that no other
     fixture binds a token to a member. The planner checked this on 2026-10-11: every
     other `TokenService.create` / `ApiTokenRow` fixture passes `None`. Report any new hit
     as a Reconciliation request.

## Pitfalls

- FastAPI route-level `dependencies=[...]` with a no-op callable is the marker. Do not
  change handler signatures for external routes.
- Police contract routes keep their own enrollment, worker-token and signature checks.
  The marker only declares them.
- JWT detection is "exactly two dots". API tokens are base64url and contain no dots.
- Do not catch `TelescreenError` from the verifier and re-wrap it; it already has the
  right code.
- Open the DB session for the token and member lookup with `ctx.sessions()` (read), as
  today; do not hold it across the verifier call.
- `human_tasks.py` `assignee=me` uses `principal.member_id`. It is unchanged for OIDC
  principals because the member id is set.
- D11: a member-bound token whose member has role NULL, or no `MemberRow`, gets no human
  permissions; only `ingest.push` survives when its scopes carry it. Do not "fix" this in
  `authenticate` or `effective_token_permissions` (p01). Seed roles in the fixtures
  instead (deliverable 6).

## Tests (`tests/api/v12_rbac/`; update `tests/api/common/test_api.py` for the new API)

Use the SQLite harness (`tests.support.db`), real tokens through
`directory/tokens.py:TokenService`, a fake `BearerVerifier` (returns a `VerifiedToken`
for the token `a.b.c`, raises `INVALID_TOKEN` otherwise) and a fake `MemberProvisioner`.

- No header -> 401; `Basic x` -> 401; revoked token -> 401.
- Read token on GET /api/issues -> 200; on PATCH -> 403. Write token on PATCH ->
  passes auth.
- Bound write token, member role viewer -> PATCH 403; role responder -> allowed; member
  inactive -> 403.
- Bound ingest token, member role None -> ingest route passes auth.
- D11 record: a read+write token bound to a member with role NULL -> GET /api/issues 403.
  The same member with a bound `{INGEST}` token -> POST /api/ingest/v1/scan passes auth.
  A bound token whose `member_id` has no `MemberRow` -> 403 on GET /api/issues.
- JWT with verifier None -> 401. Fake verifier and member with role viewer -> GET 200,
  PATCH 403. Unknown identity -> 401. Role None -> 403. Verifier raising
  `IDP_UNAVAILABLE` -> 503.
- Each router module: build it with minimal deps (or inspect the `APIRouter.routes`
  dependants) and assert that every route has exactly one `PermissionDependency` with
  the table permission, or one `ExternalAuthDeclaration` with the table kind. This is a
  per-router table test; the full-app walk is in p11.
- In-handler checks: an admin-scope token completes another member's human task; a
  write-scope token gets 403 (existing behavior preserved).
- `http_status_for` mapping for the new codes.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/api` and `<tests>` = `tests/api
tests/workflow/engine/test_engine_extended.py tests/evidence/test_evidence.py
tests/leak/flows_v11.py`. Then run these regressions with `set -o pipefail`. Each must
exit 0, and its exit status and log path go in the Progress Log:

```bash
uv run --frozen pytest -p no:cacheprovider tests/api tests/contract tests/evidence tests/assistant > /tmp/telescreen-v12-p06-rbac-enforcement-regress.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/api/v11_issues tests/api/v11_ops > /tmp/telescreen-v12-p06-rbac-enforcement-v11api.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/leak > /tmp/telescreen-v12-p06-rbac-enforcement-leak.log 2>&1; echo "exit=$?"
```

These suites build routers or the container with real tokens, so scope compatibility and
the fixture role seeding are proven there. `tests/leak` also runs code owned by
concurrent wave-2 plans. A failure caused by another plan's in-progress file is
recorded as such; a 403 or auth failure is a p06 failure and must be fixed.

## Completion Criteria

- [ ] `require_scope` is gone (`grep -rn require_scope src` prints nothing).
- [ ] Every route in the 17 routers uses `require_permission` or `declare_external` per
      the table.
- [ ] The four bound-token fixtures seed roles; `git diff` on them shows only role
      seeding (no assertion or scope change).
- [ ] Tests pass; the scoped gates and the three regression commands exit 0; typelint
      grep empty.
- [ ] Progress Log complete.

## Progress Log
