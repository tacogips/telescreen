# P10 API Common, Directory and Tokens Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (Authentication and errors, Free-form JSON on the wire, Endpoints: Health, Directory), design-docs/specs/design-masking.md (API error echo prevention), design-docs/specs/architecture.md section 5 item 6
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

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

- [x] ApiContext, Principal, require_scope, error handlers, pagination, body limiter delivered with the exact names
- [x] Directory service, tokens, resolver and router delivered
- [x] No-echo tests pass
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 00:00
**Tasks Completed**: Resumed p10 typing pass; retained pydantic dataclass request bodies with module-qualified decorators; annotated SQLAlchemy scalar results, recipient partitions, clock timestamps and test locals; confirmed API no-echo behavior.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: src/telescreen/api/routers/directory.py before=154ac7b2babbc0d1d81d9539653e21be88814e86919e8d27957938f0caf452e1 after=d4b3f2d41036d6927cf7acea59c5708db4f00174f52651b5b0bc97d18d819961; src/telescreen/directory/repository.py before=2d7edd5307b21ada6abc325f6d01be3ae374ad606ee7e7d4197ead8ea1cbce80 after=af2302e3e71c7d9a2b67ecb9acf413666f5108261d63e1e833a1fae915f40595; src/telescreen/directory/resolver.py before=392894df8adaa07da898b9047c69aede3d7078a095aff10f5914dff9b7cc88c9 after=5db69951a1d79ef3b8a678921cb14a4c96c1a60e3e0c63a2d1f9eb5d4bfab947; src/telescreen/directory/service.py before=29a9b75c30e046552e670287d5567e42fdd531d6e86d1ca62258a652bbfe011b after=b50c592e3e320a5170d63b8a7e05d45434d3209a2d190ecfde7160dc1d77d402; tests/api/common/test_api.py before=82bdbff86d347a1bf8726e14369c7ff9418b32ba722b580cc67f340e623cbe36 after=21ad6592809fc33941efe27ccc2f7e83264a44901f7c139cec3eb7346dade49d
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/directory src/telescreen/api/__init__.py src/telescreen/api/common src/telescreen/api/routers/__init__.py src/telescreen/api/routers/health.py src/telescreen/api/routers/directory.py tests/api/common` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/ruff-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/directory src/telescreen/api/__init__.py src/telescreen/api/common src/telescreen/api/routers/__init__.py src/telescreen/api/routers/health.py src/telescreen/api/routers/directory.py tests/api/common` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/ruff-check-final.log`); `uv run --frozen ty check src/telescreen/directory src/telescreen/api/__init__.py src/telescreen/api/common src/telescreen/api/routers/__init__.py src/telescreen/api/routers/health.py src/telescreen/api/routers/directory.py tests/api/common` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/ty-check-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/api/common` -> exit 0, 1 passed (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/pytest-final.log`); `uv run --frozen python tools/typelint.py` -> exit 0, 133 files clean (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/typelint-final.log`); `grep -E '^(src/telescreen/directory|src/telescreen/api/__init__.py|src/telescreen/api/common|src/telescreen/api/routers/(__init__|health|directory)\.py|tests/api/common)' tmp/telescreen-v1-20261008-s319/p10-api-common/logs/typelint-final.log` -> exit 1 (no matching p10 diagnostics; transcript: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/typelint-filter-evidence.txt`); `uv run --frozen pytest -p no:cacheprovider tests/tools/test_typelint.py` -> exit 0, 27 passed (log: `tmp/telescreen-v1-20261008-s319/p10-api-common/logs/typelint-tests.log`).
**Notes**: Initial shell attempts passed a quoted path list as one argument and are preserved in the evidence logs; corrected exact-path verification above passed. Review finding `P10-INCOMPLETE` is implemented and verified. Formal test-integrity, adversarial and branch-evidence reviews remain downstream; no review approval is claimed.
