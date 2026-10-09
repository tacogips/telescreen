# Credential Validity Check

**Status**: Resolved (2026-10-09, decided by the repository owner)
**Created**: 2026-10-09
**Category**: Detection / Data handling

## Decision Needed

Should Telescreen check whether a detected credential is still valid by calling the provider
(for example GitHub `GET /user` with the token, Slack `auth.test`)?

## Background

TruffleHog, GitGuardian and detect-secrets verify secrets against provider APIs, which helps
prioritize live credentials and confirm rotation. Verification sends the raw secret to the
provider over the network, which conflicts with "raw values never leave the scan coroutine"
unless it happens inside the trusted scanning boundary.

## Alternatives

### Option A: No validity checks (v1.1 default)
- Rotation is confirmed by the owner (human task) and the content removal by rescan.

### Option B: Opt-in, Police-side only
- A Police worker in the private network performs the check against an allowlisted set of
  provider endpoints and returns only `valid` / `invalid` / `unknown`.
- The Bureau never sees the value; the result feeds the risk score (F8) and the
  "credential revoked" verification step.

### Option C: Bureau-side checks for built-in collectors
- Simpler, but the Bureau process sends secrets to third parties.

## Decision

Rejected. Telescreen does not check credential validity against providers in any form (no Bureau-side or Police-side checks). Rotation is confirmed by the owner and content removal by rescan.
