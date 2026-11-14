# Automated Remediation and Write Scopes

**Status**: Resolved (2026-10-09, decided by the repository owner)
**Created**: 2026-10-09
**Category**: Integrations / Security

## Decision Needed

May Telescreen hold write permissions on sources in order to remediate automatically
(restrict Drive sharing, revoke public links, hide or delete Slack messages, quarantine
files), always behind an approval step in a workflow?

## Background

Strac, Teleskope, Varonis, Metomic and Cyera remediate directly. Today Telescreen only
notifies, asks owners and verifies by rescan; collectors use read-only scopes.

## Alternatives

### Option A: Read-only (v1, v1.1)
### Option B: Approval-gated write actions per source, opt-in per collector config
- A `remediate` workflow node with explicit action, dry-run, approval, audit and a
  verification rescan afterwards.
### Option C: Remediation through Police only
- Write credentials stay in the private network; the Bureau dispatches a `remediate`
  police task and receives only the outcome.

## Decision

Rejected. Telescreen stays read-only on every source: no write scopes, no automated remediation (neither Bureau-side nor Police-side). Remediation is done by owners; Telescreen notifies, tracks and verifies by rescan.
