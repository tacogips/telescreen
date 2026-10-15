# Service Split Trade-offs

**Status**: Pending owner review (design defaults apply; implementation is not blocked)

**Created**: 2026-10-15

**Category**: Service split (`design-service-split.md`)

## Decision Needed

Confirm or override five defaults that the design author chose to satisfy R1-R8 under
D1-D5. Each default follows from a requirement. An override changes the design section
listed.

## Defaults

1. **API tokens in the directory projection** (sections 4.1, 6, 7). D1/D2 list OIDC only,
   but `/api/data/*` accepts API tokens today. Token ids, existing SHA-256 verifiers,
   scopes, member binding and revocation are therefore replicated. After revocation, a
   token keeps working in a service until that service receives the revocation (normally
   one engine poll interval).
   Alternative: reject API tokens at Inventory and Notification. This changes route
   permissions for token callers.
2. **Identity key shared with Notification** (section 7). OIDC lookups use the keyed
   identity HMAC, so Notification needs the same identity key reference as Telescreen and
   Inventory.
   Alternative: Notification public routes are reachable only through the Telescreen
   proxy, with a signed principal assertion. This weakens D1.
3. **Split-mode receipts are `pending`** (section 10). Workflow notify outputs always
   report `queued`. Alert fires report `delivered` and never `blocked`. Final DLP,
   opt-out and unreachable outcomes live on the Notification rows and in the
   Notification audit chain.
   Alternative: an outcome feed that Telescreen polls from Notification. This adds a
   contract and new tables.
4. **Audit read path** (section 11). Inventory and Notification chains are verified and
   exported through their CLIs. In split mode the UI audit page shows Telescreen actions only.
   Alternative: public per-service audit endpoints and UI changes.
5. **Topologies** (section 3). Only single-process and full split are supported. The
   inventory-only remote mode from `bde11e7` is removed, because R1 forbids its Bureau
   callbacks and its retention handoff target.

## Options

Accept the defaults, or name the items to override. Each override is a follow-up change
to the listed sections and plans.
