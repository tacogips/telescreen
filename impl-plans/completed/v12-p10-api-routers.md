# V12-P10 API Routers: Notification Templates, Users/Roles/Tokens, Auth

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md "v1.2 endpoints"; design-docs/specs/design-v1-2-notifications-and-identity.md sections 3.4, 5.1, 5.3, 5.4, 6.1, 6.3
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p10-api-routers`
- planPath: `impl-plans/active/v12-p10-api-routers.md`
- wave: 3
- dependsOn: `v12-p04-notification-templates`, `v12-p06-rbac-enforcement`, `v12-p07-directory-roles`, `v12-p08-oidc-identity`
- writePaths:
  - `impl-plans/active/v12-p10-api-routers.md`
  - `src/telescreen/api/routers/notification_templates.py` (new)
  - `src/telescreen/api/routers/users.py` (new)
  - `src/telescreen/api/routers/auth.py` (new)
  - `tests/api/v12_routers` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Expose the v1.2 services over REST. Each route uses exactly one
`require_permission(...)` or `declare_external(...)` (p06). Bodies are pydantic
dataclasses; lists return `{items, next_cursor}` (`next_cursor` null; the lists are
small and unpaginated).

Imitate:

- `api/routers/policies.py` (versions, validate, activate, 422 errors as a list of
  `{field|path, code}`);
- `api/routers/directory.py` (deps dataclass, admin writes, audit actor from
  `principal.actor_ref()`).

## Non-goals

- No service logic beyond request mapping. Missing service functionality -> a
  Reconciliation request to the owning plan (p04, p07 or p08).
- No wiring (p11). Routers are constructed in tests directly.

## Invariants

- Responses never contain tokens (except the one-time `token` in `POST /api/tokens`),
  template text in error bodies, `sub` or claims.
- The `POST /api/auth/session` error codes come from the services unchanged.

## Deliverables

1. **`notification_templates.py`**:
   - `NotificationTemplatesRouterDeps(ctx: ApiContext, service: TemplateService)` and
     `build_notification_templates_router(deps)`.
   - Routes, permissions and shapes exactly as in design-api-and-ui "v1.2 endpoints":
     - GET list (`templates.read`);
     - GET `{name}` (with `variables` = `allowed_paths(event)`);
     - GET `{name}/versions/{version}`;
     - POST `/validate` -> 200 `{valid, errors: [{field, code}]}`;
     - POST `/preview` (body: either texts with `event_kind`, or `{name, version}`;
       422 `validation_error` when both or neither are given);
     - POST create -> 201 `{name, version}`, or 422 `{"error": {"code":
       "validation_error", "detail": "template validation failed"}, "errors": [{field,
       code}]}` with the same shape as the policies router;
     - POST `{name}/activate {version}`.
   - Static routes (`/validate`, `/preview`) must be registered before `/{name}`.
2. **`users.py`**:
   - `UsersRouterDeps(ctx, roles: RoleService, tokens: TokenService,
     idp_role_mapping: bool)`;
   - GET `/api/users` (`users.read`) -> `UserView` items, ISO timestamps;
   - GET `/api/roles` (`users.read`) -> `{roles: [{role, permissions}], scopes:
     [{scope, permissions}]}` from `ROLE_PERMISSIONS` / `SCOPE_PERMISSIONS` (sorted);
   - PUT `/api/users/{handle}/role {role: str | null}` (`users.write`) -> `parse_role`,
     then `set_role(..., actor_ref=principal.actor_ref(), source=manual)` -> the user
     view;
   - GET `/api/tokens` (`tokens.read`);
   - POST `/api/tokens {label, scopes: list[str], member_handle: str | null}`
     (`tokens.write`): invalid scope -> 422; empty scopes -> 422; unknown handle -> 404.
     Responds 201 `{id, token}`;
   - POST `/api/tokens/{id}/revoke` (`tokens.write`) -> 204.
3. **`auth.py`**:
   - `AuthRouterDeps(ctx, oidc: OidcSettings | None, ui_token_login: bool, verifier:
     BearerVerifier | None, sign_in: SignInService | None)`.
   - GET `/api/auth/config` (external `public`) -> `{oidc: {issuer, client_id,
     authorization_endpoint, token_endpoint, end_session_endpoint, scopes} | null,
     token_login}`. The endpoints come from `verifier.metadata()`. When metadata fails
     with `IDP_UNAVAILABLE`, still return 200 with `oidc: null` and log a static warning,
     so token login keeps working.
   - POST `/api/auth/session` (external `oidc_sign_in`):
     - require `Authorization: Bearer <jwt>`;
     - OIDC off -> 404 `not_found`;
     - `result = await sign_in.sign_in(token)`;
     - respond `{member: {handle, display_name}, role, permissions: sorted(...)}`.
   - GET `/api/auth/me` (`auth.self`) -> `{kind, member: {handle, display_name} | null,
     role, permissions}`. Load the member by `principal.member_id` from `MemberRow`
     (read-only query).

## Pitfalls

- `POST /api/auth/session` is declared external because the principal does not exist
  yet; do not add `require_permission` to it.
- Never echo the submitted template text in a 422 response.
- The pydantic body for role: `role: str | None`. Map `None` and `"none"` to no role.
- The token create audit actor must be `principal.actor_ref()`; pass it to
  `TokenService.create(actor_ref=...)`.

## Tests (`tests/api/v12_routers/`; SQLite harness, real services from p04/p07, `SignInService` with a fake verifier, httpx `ASGITransport` app containing these routers plus the error handlers)

- Templates:
  - viewer token: list 200, create 403;
  - admin: create (201 v1), activate (200), get shows `active_version` 1;
  - create with `{{ issue.secret }}` -> 422 with `errors[0] == {field: "body", code:
    "unknown_variable"}`, and the response text does not contain the submitted body;
  - preview returns both channels; preview with both forms -> 422.
- Users:
  - admin sets bob viewer -> 200 and the audit `role.changed`;
  - demoting the only admin -> 409 `last_admin`;
  - a responder token -> GET /api/users 403;
  - `/api/roles` lists 3 roles and 4 scopes.
- Tokens: create returns the token once; list does not contain it; revoke -> 204, after
  which the token gets 401 on `/api/auth/me`.
- Auth:
  - `config` with OIDC off -> `{oidc: null, token_login: true}`;
  - with a fake verifier -> endpoints present; with metadata raising -> `oidc: null`;
  - `session` success -> role and permissions; no role -> 403 `no_role`;
  - `me` with a read token -> `kind: token`, `member: null`.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/api/routers/notification_templates.py
src/telescreen/api/routers/users.py src/telescreen/api/routers/auth.py` and `<tests>` =
`tests/api/v12_routers`.

## Completion Criteria

- [x] Three routers implemented with the permissions of design 5.1.
- [x] Tests pass; scoped gates pass; typelint grep empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-11 Step 6 implementation (manifest d4)
**Tasks Completed**: Added the notification-template, users/roles/tokens and auth routers; added ASGI coverage for route authorization, validation secrecy, role audit and last-admin protection, token creation/list/revoke and role intersection, OIDC metadata/sign-in/no-role behavior, and safe auth/me projections. Each route has exactly one permission or external-auth declaration.
**Tasks In Progress**: None in this plan. Wiring and composition-root integration belong to v12-p11; combined-tree review and shared final gates remain downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/api/routers/notification_templates.py` before=absent after=ca91653b2f139e006c9917d2d815d3f1e2b9ee1fcb37607c74a1662897a5fd25; `src/telescreen/api/routers/users.py` before=absent after=3eb25bcc153358468d10366feb457439c492974f358f53ddda489f04fbdf5b51; `src/telescreen/api/routers/auth.py` before=absent after=1e10047856add26c05812d4df0f3f6f939c14e0e9a98bcb8d3af1105db14f188; `tests/api/v12_routers/__init__.py` before=absent after=cd725cfdc19ca50f55ead0b2f1e9e1faac0a30462f265841df7f84b3a6493a93; `tests/api/v12_routers/test_routers.py` before=absent after=93a05de161cf8e85b0bce91d09282f735966dbe585ae748f0331971121f0b1da; plan before=ac3396a5c4e911f38bee448ecb3b05d995b68d660e30d2f5f00509f4cebb4161.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/api/routers/notification_templates.py src/telescreen/api/routers/users.py src/telescreen/api/routers/auth.py tests/api/v12_routers` -> exit 0 (log: `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/28-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/api/routers/notification_templates.py src/telescreen/api/routers/users.py src/telescreen/api/routers/auth.py tests/api/v12_routers` -> exit 0 (log: `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/29-ruff-final.log`); `uv run --frozen ty check src/telescreen/api/routers/notification_templates.py src/telescreen/api/routers/users.py src/telescreen/api/routers/auth.py tests/api/v12_routers` -> exit 0 (log: `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/30-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/api/v12_routers` -> exit 0, 4 passed (log: `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/31-pytest-final.log`); `uv run --frozen python tools/typelint.py` -> exit 0 and scoped grep empty (logs: `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/32-typelint-final.log`, `tmp/telescreen-v12-20261011-d4/v12-p10-api-routers/attempt-1/32-typelint-scoped-final.log`); `git diff --check -- src/telescreen/api/routers/notification_templates.py src/telescreen/api/routers/users.py src/telescreen/api/routers/auth.py tests/api/v12_routers` -> exit 0.
**Notes**: Intermediate gate attempts exposed and then resolved a service-view import mismatch, missing strict local annotations, test assumptions about revocation ordering and FastAPI's lazy included-router representation, and Ruff import ordering. The final gates above were rerun after those changes and pass on the final source. The first shell wrapper used zsh's read-only `status` name and returned a wrapper error; final verification used bash and recorded the actual command exits.
