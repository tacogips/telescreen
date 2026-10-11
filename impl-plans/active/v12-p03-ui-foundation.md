# V12-P03 UI Foundation: Auth (PKCE), API Client, Mock API, Shell, Login

**Status**: Not Started
**Design Reference**: design-docs/specs/design-ui-sentry.md section 10 (10.1, 10.2, 10.4, 10.5); design-docs/specs/design-api-and-ui.md "v1.2 endpoints"; design-docs/specs/design-v1-2-notifications-and-identity.md sections 6.1, 9
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p03-ui-foundation`
- planPath: `impl-plans/active/v12-p03-ui-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v12-p03-ui-foundation.md`
  - `src/web/src/api`
  - `src/web/src/auth` (new)
  - `src/web/src/components`
  - `src/web/src/routes.tsx`
  - `src/web/src/App.tsx`
  - `src/web/src/main.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/src/pages/login` (new)
  - `src/web/scripts`
  - `src/web/vite.config.ts`
  - `src/web/node_modules` (artifact)
  - `tmp/ui-screenshots` (artifact)
- sharedPaths: none
- artifactRoots: `src/web/node_modules`, `tmp/ui-screenshots`

Read `impl-plans/conventions.md` first (sections 2 and 7 apply to web too).

## Intent and Context

Give the UI the v1.2 auth layer and data plumbing so that v12-p09 can build the pages in
parallel:

- a hand-written OIDC Authorization Code + PKCE flow (WebCrypto, no new package);
- a session store that also covers the static token;
- a login page and a callback page;
- a permission-aware shell;
- typed API clients and mock endpoints for every v1.2 endpoint.

Imitate:

- `src/web/src/api/client.ts` (`apiFetch`, `rawFetch`, `*Api` objects, `MOCK` switch);
- `src/web/src/api/mock/router.ts:routeMockRequest` and `mock/fixtures/v11.ts`;
- `src/web/src/routes.tsx` (`AREAS`, `AppRoutes`);
- `src/web/src/components/shell/TopBar.tsx` and `SecondaryPanel.tsx`;
- `src/web/scripts/screenshots.ts` (the `pages` array).

## Non-goals

- No change to `src/web/package.json` or `bun.lock`. Use `bun install --frozen-lockfile`
  only.
- No settings, workflow or run page content; that is v12-p09.
- No backend change. No OIDC library. No cookies.
- Never write `src/web/dist`.

## Invariants

- `getToken()` and `setToken()` stay exported with their current signatures, because
  `pages/settings/index.tsx` (owned by p09) imports them until p09 rewrites it.
  `setToken(token)` now stores a `{kind: "token"}` session.
- Production builds without `VITE_TELESCREEN_MOCK=1` must not include fixtures (keep the
  dynamic `import("./mock")`).
- Fixture value rules of design-ui-sentry 8.3/9.4 apply. No JWT-shaped or token-shaped
  strings in fixtures. `tests/leak/test_ui_fixtures.py` runs the rule detector over
  `src/web/src/api/mock/**`.

## Deliverables

1. **`src/web/src/auth/pkce.ts`**:
   - `randomUrlSafe(bytes: number): string` (`crypto.getRandomValues`, base64url without
     padding);
   - `createVerifier(): string` (64 characters);
   - `challengeS256(verifier: string): Promise<string>` (`crypto.subtle.digest("SHA-256")`
     and base64url).
2. **`src/web/src/auth/session.ts`**:
   - `type Session = { kind: "token" | "oidc"; access_token: string; refresh_token?:
     string; id_token?: string; expires_at?: number }`, stored under the
     `sessionStorage` key `telescreen.session`;
   - `getSession()`, `setSession()`, `clearSession()`;
   - a one-time migration of the legacy `telescreen.token` key into `{kind: "token"}`;
   - pending login under `telescreen.oidc.pending`: `{state, nonce, verifier,
     returnTo}`;
   - `safeReturnTo(value: string | null): string` (only paths starting with `/` and not
     `//`; otherwise `/issues`).
3. **`src/web/src/auth/oidc.ts`**:
   - `beginLogin(config, returnTo)`: redirect to `authorization_endpoint` with
     `response_type=code`, `client_id`, `redirect_uri=<origin>/auth/callback`, `scope`,
     `state`, `nonce`, `code_challenge`, `code_challenge_method=S256`;
   - `completeLogin(url)`: state must match pending; POST
     `application/x-www-form-urlencoded` to `token_endpoint` with
     `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`,
     `code_verifier`; decode the ID token payload (base64url JSON, no signature check)
     and require `nonce` equality; store the session with `expires_at = now +
     expires_in*1000`; call `POST /api/auth/session`; return `returnTo`;
   - `refreshIfNeeded()`: within 30 s of `expires_at` and a refresh token present ->
     `grant_type=refresh_token`; on failure, `clearSession()` and throw
     `ApiError(401, ...)`;
   - `logout(config)`: `clearSession()`, then, when `end_session_endpoint` is set,
     `location.assign(end_session?client_id&post_logout_redirect_uri=<origin>/login&id_token_hint)`;
     else navigate to `/login`.
4. **`src/web/src/api/client.ts`**:
   - `rawFetch` uses `getSession()?.access_token` and calls `refreshIfNeeded()` first
     for `oidc` sessions.
   - On a 401 response (outside mock mode and outside `/api/auth/*`), clear the session
     and redirect to `/login?returnTo=<current path>`.
   - New `authApi` (`config()`, `session()` POST, `me()`), `templatesApi` (`list`, `get`,
     `version`, `validate`, `preview`, `create`, `activate`), `usersApi` (`list`, `roles`,
     `setRole`) and `tokensApi` (`list`, `create`, `revoke`).
   - `throwApiError` also recognizes a 422 body with `errors: [{field, code}]` and throws
     a new `TemplateValidationError(errors)`. This is the shape returned by
     `POST /api/notification-templates` (p10).
   - Paths and bodies come from design-api-and-ui "v1.2 endpoints".
5. **`src/web/src/api/types.ts`**: `AuthConfig`, `AuthSession`, `AuthMe`,
   `TemplateSummary`, `TemplateDetail`, `TemplateVersionText`, `TemplateValidation`
   (`{valid, errors: {field, code}[]}`), `TemplatePreview`, `UserSummary`, `RoleMatrix`,
   `TokenSummary`, `CreatedToken`, and v2 workflow notify output target counts
   (`NotifyTargetCount {index, channel, queued, blocked, skipped}`).
6. **Mock API** (`src/web/src/api/mock/router.ts`, new `mock/fixtures/v12.ts`):
   - all v1.2 endpoints with in-memory mutations: template create (new version,
     inactive), activate, and role change (409 `last_admin` when demoting the only
     admin);
   - auth config: issuer `https://idp.example.test/realms/telescreen`, `token_login:
     true`;
   - `me`: an admin member;
   - ten built-in templates (one per event kind) plus one custom `workflow_notify`
     template with two versions;
   - four users: admin, responder, viewer, none (one IdP-managed);
   - two tokens (labels only);
   - the FIRST workflow fixture becomes an `apiVersion: telescreen.workflow/v2` source
     with two `notification_sets`, so the screenshot of "one workflow" shows sets;
   - one run with a v2 notify step output carrying `targets` counts.
7. **Shell and routes**:
   - `routes.tsx` adds `/login` and `/auth/callback` (rendered outside `AppShell`; handle
     this in `App.tsx`) and a generic `${area.path}/:id/:sub` route, needed for
     `/settings/templates/:name`.
   - An auth guard: without a session (and not in mock mode), go to `/login?returnTo=`.
   - A `useAuth()` store in `src/web/src/auth/useAuth.ts` loads `/api/auth/me` and
     exposes `can(permission: string): boolean`.
   - The TopBar user menu shows handle, role and "Sign out" (calls `logout`).
   - The Settings secondary panel lists General (`/settings`), Notification templates
     (`/settings/templates`) and Users and roles (`/settings/users`, hidden without
     `users.read`).
8. **`src/web/src/pages/login/index.tsx`** and **`Callback.tsx`**:
   - The login card shows "Sign in with SSO" when `oidc` is set, and the token form when
     `token_login` is true.
   - Error banners for `no_role`, `not_provisioned`, `identity_conflict`,
     `idp_unavailable` and `invalid_token`, with the exact texts of design-ui-sentry
     10.1.
   - The callback shows a spinner, then navigates to `safeReturnTo`.
9. **`src/web/vite.config.ts`**: `server.port = 5174`, `strictPort: true`; keep the
   proxy.
10. **`src/web/scripts/screenshots.ts`**: add `/login`, `/settings/templates`,
    `/settings/templates/system.workflow_notify` and `/settings/users` (43 images
    total).

## Pitfalls

- `crypto.subtle` exists in vitest (Node 20+). Use `globalThis.crypto`; do not polyfill
  with a package.
- The token endpoint call is a form POST, not JSON. It must not go through `apiFetch`,
  which adds Authorization and JSON headers.
- Never log tokens to the console. The screenshot smoke fails on any console error, so
  the auth guard must not throw in mock mode.
- Clear `telescreen.oidc.pending` after the callback, on success or failure.
- `/login` must not be wrapped by the guard (redirect loop).

## Tests (vitest, colocated `*.test.ts(x)`)

- `pkce.test.ts`:
  - the RFC 7636 appendix B vector: verifier `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk`
    -> challenge `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`;
  - `createVerifier` length 64, url-safe charset.
- `session.test.ts`: legacy key migration; `safeReturnTo("//evil")` -> `/issues`;
  `safeReturnTo("https://x")` -> `/issues`; `safeReturnTo("/runs/1")` -> `/runs/1`.
- `oidc.test.ts` (fake `fetch`, fake `location`):
  - state mismatch -> error, no token request;
  - nonce mismatch -> error, session not stored;
  - success stores the session and calls `/api/auth/session`;
  - refresh near expiry posts `grant_type=refresh_token`;
  - a failed refresh clears the session;
  - logout builds an end-session URL with `id_token_hint`, or goes to `/login` when
    there is none.
- `login.test.tsx`: renders the SSO button only with oidc; the token form only with
  `token_login`; the `?error=no_role` banner text.
- `client.test.ts`: the bearer comes from the session; 401 clears the session.
- `mock` tests: the new endpoints return the expected shapes; the fixture raw-value scan
  test still passes.
- `shell.test.tsx`: the Users and roles link is hidden without `users.read`.

## Verification

From the repository root, with `set -o pipefail`:

```bash
cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v12-p03-ui-foundation-install.log 2>&1; echo "exit=$?"
cd src/web && bun run typecheck > /tmp/telescreen-v12-p03-ui-foundation-typecheck.log 2>&1; echo "exit=$?"
cd src/web && bun run test > /tmp/telescreen-v12-p03-ui-foundation-test.log 2>&1; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-v12-p03-ui-foundation-web --emptyOutDir > /tmp/telescreen-v12-p03-ui-foundation-build.log 2>&1; echo "exit=$?"
cd src/web && bun run screenshots > /tmp/telescreen-v12-p03-ui-foundation-shots.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py > /tmp/telescreen-v12-p03-ui-foundation-fixtures.log 2>&1; echo "exit=$?"
```

Each command must exit 0. Screenshots: 43 PNGs under `tmp/ui-screenshots` and zero
page or console errors in the log. The settings pages may still show the v1.1 Settings
content until p09 lands, which is acceptable. If no browser is available, record it as a
verification gap and do not report it as a pass.

## Completion Criteria

- [ ] Deliverables 1-10 implemented.
- [ ] typecheck, vitest and build exit 0; the UI fixture leak test exits 0.
- [ ] Screenshot smoke exits 0 with 43 images (or a recorded browser gap).
- [ ] `package.json` and `bun.lock` unchanged.
- [ ] Progress Log complete.

## Progress Log
