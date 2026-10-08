# P10 API Common, Directory and Tokens Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md (Authentication and errors, Free-form JSON on the wire, Endpoints: Health, Directory), design-docs/specs/design-masking.md (API error echo prevention), design-docs/specs/architecture.md section 5 item 6
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p10-api-common`
- planPath: `impl-plans/active/p10-api-common.md`
- wave: 3
- dependsOn: `p02-db-schema`, `p04-ports`
- writePaths:
  - `impl-plans/active/p10-api-common.md`
  - `src/telescreen/directory`
  - `src/telescreen/api/__init__.py`
  - `src/telescreen/api/common`
  - `src/telescreen/api/routers/__init__.py`
  - `src/telescreen/api/routers/health.py`
  - `src/telescreen/api/routers/directory.py`
  - `tests/api/common`
- sharedPaths: none
- artifactRoots: none

This plan does not import `telescreen.masking`; error ids come from
`telescreen.core.ids.new_id()`. Log masking is installed globally by p28.

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan provides the shared FastAPI plumbing that every router plan uses (auth
dependency, error handlers, pagination, the `ApiContext`), plus the directory feature:
members, teams, memberships and API tokens. The directory implements the
`RecipientResolver` port (recipient refs to contacts).

## Non-goals

- No `create_app` (p28 assembles the app). No OIDC (deferred by owner decision).

## Deliverables

### `src/telescreen/api/common/`
- `context.py`: `ApiContext(sessions: async_sessionmaker[AsyncSession], clock: Clock, audit: AuditLog)`
  (frozen dataclass).
- `auth.py`:
  - `Principal(token_id: str, member_id: str | None, scopes: frozenset[ApiScope])`
    with `has(scope) -> bool` (`admin` implies all) and `actor_ref() -> str`
    (`member:<id>` or `token:<id>`).
  - `require_scope(ctx: ApiContext, scope: ApiScope) -> Callable[[Request], Awaitable[Principal]]`,
    a dependency factory used as `Depends(require_scope(ctx, ApiScope.READ))`. It reads
    `Authorization: Bearer <token>`, computes sha256 hex, loads a non-revoked
    `ApiTokenRow` and checks the scope.
  - Errors: missing or invalid token -> `TelescreenError(unauthorized)` (401); missing
    scope -> `forbidden` (403).
  - `hash_token(token: str) -> str`.
- `errors.py`:
  - `http_status_for(code: ErrorCode) -> int`: validation_error 422, raw_value_detected
    422, evidence_rejected 422, unauthorized 401, token_invalid 401, signature_invalid
    401, forbidden 403, not_found 404, unknown_source 404, conflict 409,
    invalid_transition 409, evidence_required 409, payload_too_large 413,
    unsupported_media_type 415, rate_limited 429, evidence_scanner_unavailable 503,
    everything else 500.
  - `install_error_handlers(app: FastAPI) -> None` registers three handlers.
    `TelescreenError` -> JSON `{"error":{"code","detail"}}`. `RequestValidationError` ->
    422 `{"error":{"code":"validation_error","detail":"request validation failed","errors":[{"loc":[...],"type":"..."}]}}`,
    with ONLY `loc` and `type`. Any other `Exception` -> 500
    `{"error":{"code":"internal_error","detail":"internal error","error_id":<id>}}`,
    logged with `logger.exception` and the error id (the masking formatter is installed
    by p28).
- `pagination.py`: `PageParams(limit: int, cursor: str | None)`;
  `page_params(limit: int = 50, cursor: str | None = None) -> PageParams` (a FastAPI
  dependency; limit 1-200); `encode_cursor(parts: Sequence[str]) -> str` (base64url of
  canonical JSON); `decode_cursor(text: str) -> tuple[str, ...]` (invalid ->
  validation_error).
- `body.py`: `async def read_limited_body(request: Request, max_bytes: int) -> bytes`
  (raises `payload_too_large`; it reads the stream so it never holds more than
  `max_bytes + 1` bytes).

### `src/telescreen/directory/`
- `repository.py`: CRUD over `MemberRow`, `TeamRow`, `TeamMembershipRow`, `ApiTokenRow`.
- `service.py`: `DirectoryService(audit: AuditLog, clock: Clock)`:
  - `create_member(session, handle, display_name, email, slack_user_id) -> MemberRow`;
  - `create_team(session, slug, name, email, slack_channel)`;
  - `set_team_members(session, slug, handles: Sequence[str])`;
  - list methods.
  - Every mutation appends an audit entry: `directory.member_created`,
    `directory.team_created`, `directory.team_members_set`.
- `tokens.py`: `CreatedToken(token_id: str, token: str = field(repr=False))`;
  `TokenService(audit, clock)`:
  - `create(session, label: str, scopes: frozenset[ApiScope], member_id: str | None) -> CreatedToken`
    (32 random bytes, base64url; only the hash is stored; audit `token.created` without
    the token);
  - `revoke(session, token_id)` (audit `token.revoked`).
- `resolver.py`: `DirectoryRecipientResolver` implements `RecipientResolver`:
  - `member:<handle>` -> email and slack user id;
  - `team:<slug>` -> team mailing-list email, team slack channel and members' contacts;
  - `email:<addr>` -> email;
  - `slack:<channel>` -> channel;
  - an unknown or inactive member, or an unknown team -> `recipient_unknown`.
  - `is_member_of` and `assignee_of` as specified in the ports plan.

### Routers
- `routers/__init__.py`: docstring.
- `routers/health.py`: `build_health_router() -> APIRouter`; `GET /api/health` ->
  `{"status":"ok"}` (no auth).
- `routers/directory.py`: `DirectoryRouterDeps(ctx: ApiContext, directory: DirectoryService)`;
  `build_directory_router(deps) -> APIRouter`:
  - `GET /api/members` and `GET /api/teams` (read);
  - `POST /api/members` and `POST /api/teams` (admin);
  - `PUT /api/teams/{slug}/members` with body `{handles: [...]}` (admin).
  - Bodies are pydantic dataclasses.

## Key Pitfalls

- Never put the token, the Authorization header or request bodies in logs or errors.
- Compare token hashes with a single indexed lookup on `token_hash`; also reject revoked
  tokens.
- FastAPI dependency factories return an async function typed
  `async def dependency(request: Request) -> Principal`. Annotate everything; no
  `**kwargs`.
- The `RequestValidationError` handler must not include `msg`, `input` or `ctx`.
- Clock comes from `ApiContext.clock`.

## Tests (`tests/api/common/`)

Build a small FastAPI app in the test with the routers and handlers. Use the
`tests.support.db` harness and a fake `AuditLog` that records entries. Call it through
`httpx.AsyncClient(transport=ASGITransport(app))`.

- No token -> 401 `unauthorized`; a read token on an admin route -> 403; a revoked token
  -> 401; an admin token -> 200.
- A POST with a body field of the wrong type whose value is a sample secret -> 422, and
  the response text does not contain the sample (`assert_no_leaks`); the errors list has
  only `loc` and `type`.
- A route raising `RuntimeError(sample)` -> 500 with an `error_id`; the response has no
  leak.
- `TokenService.create` -> the stored row has only the hash; the audit entry details
  contain no token; `repr(CreatedToken)` lacks the token.
- The resolver handles all four ref kinds; an unknown team raises `recipient_unknown`;
  `is_member_of` is true for a team member and false otherwise.
- Cursor round trip; a garbage cursor -> 422.
- `read_limited_body` over the limit -> 413.
- `GET /api/health` -> 200.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/directory src/telescreen/api/__init__.py src/telescreen/api/common src/telescreen/api/routers/__init__.py src/telescreen/api/routers/health.py src/telescreen/api/routers/directory.py tests/api/common"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/api/common 2>&1 | tee /tmp/telescreen-p10-api-common-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p10-api-common-typelint.log 2>&1; grep -E '^(src/telescreen/directory|src/telescreen/api/__init__.py|src/telescreen/api/common|src/telescreen/api/routers/(__init__|health|directory)\.py|tests/api/common)' /tmp/telescreen-p10-api-common-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] ApiContext, Principal, require_scope, error handlers, pagination, body limiter delivered with the exact names
- [ ] Directory service, tokens, resolver and router delivered
- [ ] No-echo tests pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
