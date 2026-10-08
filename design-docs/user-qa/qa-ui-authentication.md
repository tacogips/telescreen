# UI and API Authentication Model

**Status**: Resolved (2026-10-08, decided by the repository owner)

**Created**: 2026-10-08

**Category**: Architecture

## Decision Needed

How should human users authenticate to the Reporting Bureau UI and API?

## Background

The issue requires a Sentry-like UI but does not specify identity integration. Human task
assignment and completion need to know which member acts.

## Alternatives

### Option A: Bearer API tokens (v1 default)

- Admin creates tokens with `telescreen token create`, optionally linked to a member.
- The UI asks for the token on the Settings page and keeps it in `sessionStorage`.
- No new dependencies; simple to test offline.

### Option B: OIDC SSO (Google Workspace, Okta, Entra ID)

- Proper user sessions and group-to-team mapping.
- Needs an OIDC client, session cookies, CSRF handling; out of scope for v1.

## Impact

`design-docs/specs/design-api-and-ui.md` (Authentication), `api_tokens` table, UI Settings
page. Option B can be added later without changing the scope model.

## Decision

Option A accepted: bearer API tokens for v1; OIDC SSO is deferred to a later version.
