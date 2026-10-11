# V12-P03 UI Foundation: Auth (PKCE), API Client, Mock API, Shell, Login

**Status**: In Progress — assigned source deliverables are implemented; the full vitest gate has an out-of-scope legacy storage assertion pending reconciliation.
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

- [x] Deliverables 1-10 implemented, including PKCE/session auth, typed clients, v1.2 mocks, protected shell, login/callback, Vite port, and screenshots.
- [x] typecheck and build exit 0; `tests/leak/test_ui_fixtures.py` exits 0.
- [ ] Full vitest suite exits 0. Final run has 164 passed and one failure in `src/web/src/pages/settings/index.test.tsx:22`, which still asserts the removed `telescreen.token` key instead of the required `telescreen.session` object.
- [x] Screenshot smoke exits 0 with 43 images and no browser/page/console errors.
- [x] `package.json` and `bun.lock` unchanged.
- [x] Progress Log records implementation, checks, and the required reconciliation request.

## Progress Log

- Implemented the assigned UI foundation in `src/web/src/auth/{pkce,session,oidc,useAuth}.ts`, `src/web/src/api/{client,types}.ts`, `src/web/src/api/mock/{router,state}.ts`, `src/web/src/api/mock/fixtures/v12.ts`, `src/web/src/components/shell/{TopBar,SecondaryPanel}.tsx`, `src/web/src/{App,routes}.tsx`, `src/web/src/pages/login/`, `src/web/vite.config.ts`, and `src/web/scripts/screenshots.ts`. Added auth, login, mock, and shell regressions in the plan-owned test paths. Existing exports remain, static tokens are represented by `{kind: "token"}` sessions, and `package.json`/`bun.lock` were not edited.
- Reconciliation request (write-path ownership): `src/web/src/pages/settings/index.test.tsx:22` asserts `sessionStorage.getItem("telescreen.token")`. The accepted p03 contract requires `setToken()` to store `{kind: "token"}` under `telescreen.session`, while that test path is absent from this plan's `writePaths`. Do not duplicate a bearer token into the legacy key. Resume by authorizing the test assertion update under the owning plan or adding that exact test path to this plan's write paths; then rerun `cd src/web && bun run test`.
- Verification: `bun install --frozen-lockfile` exit 0, `/tmp/telescreen-v12-p03-ui-foundation-install.log`; `bun run typecheck` exit 0, `/tmp/telescreen-v12-p03-ui-foundation-typecheck-final2.log`; focused auth/client/mock/login/shell Vitest exit 0 (49 passed, 0 failed), `/tmp/telescreen-v12-p03-ui-foundation-focused-test-final.log`; `bun run build --outDir /tmp/telescreen-v12-p03-ui-foundation-web --emptyOutDir` exit 0, `/tmp/telescreen-v12-p03-ui-foundation-build-final.log`; `bun run screenshots` exit 0 and screenshot assertions found 43 files with no browser errors, `/tmp/telescreen-v12-p03-ui-foundation-shots-final2.log`; `uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py` exit 0 (1 passed), `/tmp/telescreen-v12-p03-ui-foundation-fixtures-final.log`; dependency-file unchanged check exit 0.
- Required full-suite gate remains failed: `cd src/web && bun run test` exit 1 (164 passed, 1 failed), `/tmp/telescreen-v12-p03-ui-foundation-test-final2.log`. The sole failure is the out-of-scope legacy storage assertion above. Earlier attempt logs are retained in `/tmp/telescreen-v12-p03-ui-foundation-test.log`, `/tmp/telescreen-v12-p03-ui-foundation-test-retry.log`, and `/tmp/telescreen-v12-p03-ui-foundation-test-retry2.log`.
- Verification command record: `test "$(find tmp/ui-screenshots -maxdepth 1 -name '*.png' | wc -l | tr -d ' ')" -eq 43 && ! grep -q 'Browser reported\\|\\[pageerror\\|\\[console\\|\\[http ' /tmp/telescreen-v12-p03-ui-foundation-shots-final2.log` exit 0; `test -z "$(git diff --name-only HEAD -- src/web/package.json src/web/bun.lock)"` exit 0.

- SHA-256 before/after (authored files): `src/web/src/api/client.ts` 02cd1dcc8488e4caacb7a16c67e3149649965c4775d47af73711c3e84c6e4fc3/d1e441885549b2eb8c17e89a7eac7a52c060c064415f76e3c09d6e2e3caebce9; `src/web/src/api/client.test.ts` e909a558fff904c32ec1deb19de45aa42e349f019885d72ec85dc4523bd67b1b/a704d18bb67ab9728e2f447f18d1ef5b34e198eff3088d0af6f8ef350ab09ee0; `src/web/src/api/types.ts` 1d5d33c18961a0aab90d8b159a8939bcf4cd67442ea0d1795933ad565882a8bc/705aeb4dd0c1568eae469466efe0ca13b90db7774678d57db23180de64f96609; `src/web/src/api/mock/router.ts` 8a5b291d3be12dd8620b95fd60234fb3fc1858e89c272221222ce002c528d5ea/53620268cd68f7dc6407cf8de788898f245b3411ccf20f43a2dc653c73b16a29; `src/web/src/api/mock/state.ts` fa867845d8fb7e9cec9e5c43314d47c6e6c458472720f2a04865b789201eb0a2/c9b51a59b20db839d0e051e31d9179a69b6505b0129e600f4b090f3ad488519f; `src/web/src/api/mock/mock.test.ts` 205e2569db046b0354146427341397f44891a693bb8c776ba12df7be846e2504/f4ab9a73dc09855dde6b5b64081bd22789b17317767e0dd0e460e9b2a1682d90.
- SHA-256 before/after (existing shell, routing and build files): `src/web/src/components/shell/TopBar.tsx` 9b5a45e25e75dbc6399313caa7d16e5c7ce54a1cbc885fe3c68085227c153e9d/4f95d8a2c245ae9baa77b2b6c6963eb8428fde4f04e10cf81132995d045d0a23; `src/web/src/components/shell/SecondaryPanel.tsx` e66217973793dbf2b32d92f82944dd0c101078f4d7e83b035ecf42bf9e86082e/73a1b99c539bfa1b240c091fd44e428c7750704e599dd9578043ef16c1487211; `src/web/src/routes.tsx` 25c65709f14282947e5c6e09f2024d96ebbcc44db205fab9dc349816ea42d4de/d699008bad2feb7017ee24c6e70d10aa54d3e734d47c318b6dac005cf2d3d21b; `src/web/src/App.tsx` d319be3cd75e6c6e7fa99f5c74ed2585f8ab5bfab93c953d83cb830f4e1cf5dd/219bd81cc798772b65cb67c8136abe77b2b3982dde34d586047f0410c3a00ce0; `src/web/src/shell.test.tsx` 7ea53d0beeb5559de80b08e095e24ce1c05ac5320df7f50595b2e21216175992/1d63a29fc0c7aade7a321b3a9160f16dc9aae3d19fcf587ee7aa77393b1630b7; `src/web/vite.config.ts` feb9f32525ad283515f66f9514dc9a280d4f2a39b6791ff4e18ea304eff7b006/c5ca51a3d4734797bcfe86e2c480e8667395628dacdb4707690e178798d46c6b; `src/web/scripts/screenshots.ts` c69c59468b0ba939bab56970f0b7d401234af1f9befb2e3033cf6f4b6d26ccc5/71c5e776e3818ed2bd27ba0be0a8ca046d1f2028fd07f846027d62918186f380.
- SHA-256 before/after (new files; `absent` before): `src/web/src/api/mock/fixtures/v12.ts` e67488dcd4e25b7cdd61cfedd724f27d363ee32d8304fee40f78281c3f2f41e5; `src/web/src/auth/pkce.ts` 86a432fa0f988739272757688661b80734ec00fb9921aedeaa5eb1a90d14fb9c; `src/web/src/auth/pkce.test.ts` 9adc3308017d28f527cfd55701cc5487ac129d4306324ef8831182cada94f722; `src/web/src/auth/session.ts` 4db725692cd33f2751685f453ab97eca2bb5a62dd771c8d26bb45c4ca564deef; `src/web/src/auth/session.test.ts` ed4cc37560e68e56cb7506774ab75a65a7fd213928b4f002a2e43f923d8f2da4; `src/web/src/auth/oidc.ts` b2ccc9cccd4f3c5eaffff434f0586b62a05e3813ec2adf647b68329defb07271; `src/web/src/auth/oidc.test.ts` afd6796fc5ff83cb0bf2b2c7036d3d30fbfa75fb58719b0f27ed0841b9dec2fa; `src/web/src/auth/useAuth.ts` 0f05a6e91ce81c9a5410f59c2487971ec0038c63c8e46152858f1e0584781848; `src/web/src/pages/login/index.tsx` bd2aa68f7cbe685930f4298926e9a978fe528b59f0a9a984a5adaa755befdfcc; `src/web/src/pages/login/Callback.tsx` 09282445096c41ab65c699e561e36c5d3d415fa985475474784b9a7b8ca592cb; `src/web/src/pages/login/login.css` 934b9461b4047f105a0bd12b90ab6ae24c546a5f00e7dceebdd519d889f35843; `src/web/src/pages/login/login.test.tsx` 0b881fb686e0b5d7bad983c668e11ecc0d7dce059f19dd8f0f48ef3864cf98d0.

**Owner resolution (2026-10-11, session 7)**: The only blockers were cross-plan test inventories outside this plan's writePaths. The owner updated `tests/db/test_models.py` (adds `notification_templates`, 34 tables) and `src/web/src/pages/settings/index.test.tsx` (reads the token through `getSession()` instead of the legacy `telescreen.token` key). The UI fixture leak test passes with the final `v12.ts`. Whole-repository gates on the combined wave-1 tree: ruff format/check, typelint, ty, pytest 589 passed / 5 skipped, web typecheck and vitest 165 passed. On redispatch, re-verify on the committed tree, change nothing unless review finds a defect, and report only final passing commands with an empty `priorVerification` (conventions 7a).
