# Custom Push Resource Identity

**Status**: Pending Decision

**Created**: 2026-10-13

**Category**: Ingestion / data inventory (v1.4)

## Decision Needed

Should the two push ingest endpoints derive the same `resource_key` for the same custom
locator?

## Background

Found while re-checking the v1.4 requirements (`design-v1-4-data-inventory.md` section 1,
C5).

- `POST /api/ingest/v1/scan` keys a resource as `resource_key("custom:<name>", locator)`
  (`issues/ingest.py:68-77`).
- `POST /api/ingest/v1/findings` keys it as `resource_key("custom:<name>",
  "custom:<name>:" + locator)` (`issues/service.py:524,558`). This matches the `custom`
  row of the resource identity table in `design-collectors-and-ingestion.md`.

The same locator pushed through both endpoints therefore becomes two issues per rule
class, and from v1.4 also two data assets. Pushers that use only one endpoint per resource
are not affected.

## Alternatives

### Option A: Keep as is (v1.4 default)

- No change to existing issue identity.
- Document that a custom resource must be pushed through one endpoint.

### Option B: Change `/scan` to the documented `custom:<name>:` prefix

- Both endpoints agree, and so does the design.
- Existing `/scan` issues and assets are keyed under the old form. New pushes would open
  new issues, unless a migration re-keys them. That migration needs the raw locators,
  which are not stored, so it is impossible.

## Impact

Option B changes issue grouping for existing custom sources. v1.4 does not depend on the
answer.

## Awaiting

Owner preference: keep (A), or accept new identities for `/scan` pushes (B).
