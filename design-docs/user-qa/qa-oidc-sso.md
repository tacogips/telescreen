# OIDC SSO and Role Mapping (v1.2)

**Status**: Decided (2026-10-11). The owner requested SSO; the details below are design
defaults that the owner may change through configuration. No decision is blocking.

**Created**: 2026-10-11

**Category**: Architecture / Security

## Decision Needed

How Telescreen v1.2 signs users in through OpenID Connect (Keycloak in tests) and maps them
to members and roles. This record supersedes the deferral in `qa-ui-authentication.md`
(Option B), and keeps that record's Option A as a fallback.

## Background

The owner asked (2026-10-11) to verify registered-user permission management and SSO
integration against a Keycloak container with test users. The dev realm
(`examples/compose/keycloak/telescreen-realm.json`) has realm roles
`telescreen-admin|responder|viewer`, groups `security|dev|ghe-admin`, a public client
`telescreen-ui` (S256 PKCE, direct access grants enabled), a `groups` claim mapper and an
audience mapper.

## Decisions

| # | Question | Decision | Alternatives considered |
|---|----------|----------|-------------------------|
| 1 | Auditor role | Not added. `viewer` already reads audit records (like the v1 `read` scope) | Add `auditor` (no requirement separates audit reading) |
| 2 | Role claim | `realm_access.roles` with map `telescreen-admin=admin,telescreen-responder=responder,telescreen-viewer=viewer`; highest wins. `TELESCREEN_OIDC_ROLE_CLAIM` / `_ROLE_MAP` override it | `groups` claim (groups are kept for team mapping) |
| 3 | Group to team mapping | Optional `TELESCREEN_OIDC_TEAM_MAP`, empty by default; only `idp`-origin memberships are added or removed | Always on (would edit teams the owner manages by hand) |
| 4 | No mapped role | 403 `no_role` unless `TELESCREEN_OIDC_DEFAULT_ROLE` is set; nothing is provisioned | Auto-viewer (grants access to everyone in the realm) |
| 5 | Provisioning | `link` (default: link by verified email to an existing member) or `create` (JIT). The dev walkthrough uses `create` | Always create (account sprawl) |
| 6 | Identity storage | `(issuer, sub)` as a keyed `member_identities` row (`oidc_subject`) | New table with raw `sub` |
| 7 | Role authority | The IdP is authoritative at each sign-in for OIDC-linked members. API changes are allowed but are overwritten at the next sign-in. The last-admin rule covers Telescreen-side changes; CLI recovery exists | Telescreen-pinned roles (two sources of truth) |
| 8 | Token storage in the UI | `sessionStorage` (access, refresh and ID tokens), as v1 does for the static token | Memory only (lost on reload); cookies with a backend session (CSRF, more code) |
| 9 | Refresh | Refresh-token grant 30 s before expiry; on failure, back to `/login` | Re-login at expiry (Keycloak access tokens default to 5 min) |
| 10 | Logout | Clear local storage, then RP-initiated logout at `end_session_endpoint` when advertised | Local clear only (the IdP session would sign the user straight back in) |
| 11 | Static tokens | Always accepted for API clients; `TELESCREEN_UI_TOKEN_LOGIN=true` keeps token entry on the login page | Remove UI token login |
| 12 | Existing tokens | Migration sets each member's role from the highest scope among its bound tokens; unbound tokens are unchanged | Leave roles empty (bound tokens would lose access) |
| 13 | JWT library | `pyjwt[crypto]` (already a dependency), through its typed entry points only | `joserfc` (new dependency) |

## Impact

`design-docs/specs/design-v1-2-notifications-and-identity.md` (sections 1, 5, 6),
`design-api-and-ui.md` (Authentication, v1.2 endpoints), `design-data-model.md` (v1.2
additions), `command.md` (OIDC variables, CLI).

## Decision

Accepted as the v1.2 design defaults. Configuration covers every item except 1, 6, 8 and
13, which can change later without a data migration other than adding a role.
