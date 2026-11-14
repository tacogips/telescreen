# On-Demand Reveal of a Matched Value

**Status**: Resolved (2026-10-09, decided by the repository owner)
**Created**: 2026-10-09
**Category**: Data handling / UI

## Decision Needed

Should privileged users be able to view the raw matched value for triage (true or false
positive), fetched live from the source at request time and never stored?

## Background

Amazon Macie "Reveal" re-reads the source object on demand and returns a few matched values
without storing them. Google Workspace DLP and Nightfall instead keep snippets for 180 or 30
days, which the survey identifies as a leak risk. Telescreen currently never returns raw
values from any API.

## Alternatives

### Option A: Never reveal (v1.1 default)
- Triage uses masked snippets, rule ids and the link to the source location.

### Option B: Live reveal, admin-only, audited, not stored
- `POST /api/issues/{id}/reveal` with a mandatory reason; the Bureau (or the Police for
  police sources) fetches the resource, returns at most 3 matched values in the response
  body only, writes an audit record (no value), and never logs or caches them. Disabled by
  default via configuration.

### Option C: Reveal through the source only
- The UI deep-links to the exact location in Slack, Drive or GitHub; the user views the
  value with their own source permissions. No raw value passes through Telescreen.

## Decision

Rejected. Telescreen never reveals raw matched values, not even transiently. Triage uses masked snippets, rule ids and deep links to the source location (Option C deep links remain allowed because no raw value passes through Telescreen).
