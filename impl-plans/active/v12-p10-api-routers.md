# V12-P10 API Routers: Notification Templates, Users/Roles/Tokens, Auth

**Status**: Not Started
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

- [ ] Three routers implemented with the permissions of design 5.1.
- [ ] Tests pass; scoped gates pass; typelint grep empty.
- [ ] Progress Log complete.

## Progress Log
