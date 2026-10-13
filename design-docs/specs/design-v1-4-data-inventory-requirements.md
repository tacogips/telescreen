# Telescreen v1.4: Data Inventory Requirements

Status: implemented 2026-10-14 (`design-v1-4-data-inventory.md`, plans `v14-p01` to
`v14-p09` archived under `impl-plans/completed/`). Accepted requirements, owner request of
2026-10-13. Input to the v1.4 design and plans.
Verified against the code at `d328fbb` (branch `feat/diagra-ddl`) on 2026-10-13, and
re-checked at `07c975f` by the v1.4 design author (corrections in section 7).

The owner asked (translated): "Does Telescreen hold, as a data management list, where data
lives and its audit history (when it was added, when it was deleted)? If not, design and
implement it. Data is sometimes list-shaped, so also manage partial changes, such as how
many of its items were deleted and when. Also make it possible to register information
attached to the data, for example its purpose of use, who is responsible and the planned
deletion date, by registering a free schema (including whether each field is required) and
then registering values against it."

## 1. Investigation result: not present

Telescreen has no data inventory today.

| Question | Today | Evidence |
|----------|-------|----------|
| Is there a register of data locations? | No. A location exists only indirectly as an issue `(source, resource_key, rule_class)`. A resource with no findings leaves no row. | `db/models/issues.py:11`, `issues/keys.py:8` |
| When was a location first seen and last seen? | Only per issue (`first_seen`, `last_seen`), not per location | `db/models/issues.py` |
| When was a location deleted? | Not recorded. Deletion only auto-resolves open issues with `resolution=resource_deleted`. `resource_coverage` rows are deleted when a resource is removed, so no history survives. | `issues/ingest_transitions.py:197`, `collectors/coverage.py:84-126` |
| Partial changes inside a resource (N items removed at T) | Not detected or recorded. Reconciliation is per rule class only. | `issues/service.py:398-411` |
| Can push ingest report a deleted resource? | No. `ResourceContent.removed` is always false for push. | `api/routers/ingest.py:38`, `ports/collectors.py:112` |
| User-defined metadata or a field schema | None. Issue attributes are fixed columns. | `db/models/issues*.py` |
| Audit | Hash-chained `audit_records`, but no resource-level events | `audit/service.py:40` |

## 2. Concepts

- **Data asset**: one entry of the data register, a place where data lives. It is either
  *discovered* (from a scan, keyed by `(source, resource_key)`, the same identity issues
  use) or *registered* manually by a member (a source label plus a locator, for data no
  collector reaches: a database table, a SaaS export, a paper archive).
- **Lifecycle event**: an append-only record of what happened to an asset and when:
  discovered, registered, items added, items removed, deleted, restored, metadata changed,
  manual event (for example "purged 120 records under ticket X"). Each event carries
  `occurred_at` (when it happened at the source, if known) and `recorded_at` (when Telescreen
  recorded it), the actor, and for list changes the counts.
- **Items**: the members of a list-shaped asset (rows of a sheet or table, records of an
  export, messages of a channel). Telescreen tracks item identity only as keyed HMAC
  fingerprints (domain-separated from finding fingerprints), never raw keys or content.
- **Field schema**: admin-defined metadata fields (key, label, description, type, options,
  required flag, order, optional semantic role) whose values members register per asset.

## 3. Required behaviour

R1 Register. Every asset has: id, short id (`DATA-<n>`), name, source, display locator,
kind (file, table, list, channel, object, folder, other), origin (discovered, registered),
state (present, deleted, unknown), first_seen/registered_at, last_seen_at, deleted_at,
current item count (nullable), related open issue count, collector link when discovered.

R2 Discovery. Scans upsert discovered assets. Each collector chooses an inventory mode:
`sensitive_only` (default: register resources that produced at least one finding),
`all` (register every scanned resource) or `off`. Push ingest follows the collector of
its source. Scans update `last_seen_at`; they do not write an event per scan.

R3 Deletion and restoration. An asset becomes `deleted` with a `deleted` event when a
pull collector reports the resource gone (`ResourceGoneError`, removed task mode, sweep
`REMOVED`, absence from a complete sweep) or push ingest reports it removed (new
`removed: true` on the resource, or a dedicated removal endpoint; the design chooses).
A deleted asset that is seen again becomes `present` with a `restored` event. Incomplete
or failed scans never infer deletion. A member may also mark an asset deleted manually
with a date and a note.

R4 Partial changes of list-shaped data. Ingest (push contract, Police SDK and the
collector port) may carry, per resource, either the item identity keys or only an item
count. The Bureau HMACs keys immediately, keeps the current fingerprint set per asset
with `first_seen_at` and `removed_at` per item, and on each complete scan writes one
event with `items_added`, `items_removed` and `item_count_after`. With only counts it
writes the count delta. Independently of items, the set of distinct finding fingerprints
per asset is diffed the same way, so "3 of the 5 sensitive values were removed at T" is
recorded for every asset. The detail view shows the item count over time and which item
fingerprints (short prefix only) were removed when. Members can record a manual partial
change (for example "removed 120 rows", with `occurred_at` and a note).

R5 Field schema. Admins create, edit, reorder and archive fields. Types: text, long text,
integer, decimal, boolean, date, single select, multi select, member or team reference,
URL. Each field has a required flag. Optional semantic roles let features use a field:
`purpose`, `owner`, `deletion_due`. Field keys are immutable; a type change is allowed
only while no values exist; archiving hides a field without deleting stored values; hard
delete only when no values exist. Schema changes are audited.

R6 Field values. Members with write permission set values per asset; values are validated
against the field type and options. Required fields are enforced when a member saves the
metadata form or registers an asset manually; assets (for example newly discovered ones)
that lack a required value are flagged `incomplete`, not rejected. Every value change
writes a `metadata_changed` event with field, old and new value and actor.

R7 Retention. When a field has role `deletion_due`, assets that are still present after
that date are `overdue`. The register has an Overdue view, the dashboard shows the count,
and an overdue asset notifies its owner (the `owner` role field, falling back to the
asset's issue owner rules) once, through the existing notification settings.

R8 Register views. A "Data" area in the UI: list with tabs and counts (All, Present,
Deleted, Incomplete, Overdue), search and filters (source, kind, state, origin, owner,
field values), columns for name and locator, source, state, item count with trend, open
issues, owner, deletion due and last seen; detail page with header, metadata form, event
timeline, item-count chart, items section, related issues and actions (edit, record event,
mark deleted); manual registration form; a schema editor under Settings; a link from
issue detail to its asset; CSV export of the register (masked).

R9 Audit. Every write (asset registration and edits, values, manual events, schema
changes, deletion marks) also appends `audit_records`. Lifecycle events are append-only:
no API edits or deletes them.

## 4. Safety constraints (binding)

- Raw detected values, raw item keys and source content are never persisted, logged,
  indexed, notified, exported or returned. Item keys are HMAC'd on receipt with a key
  derived from the fingerprint key under a separate domain label.
- Metadata values are free text entered by people: run them through the detectors on
  write and reject a value that contains a detected sensitive value (422, without echoing
  or logging it).
- Telescreen stays read-only on sources: the register records deletions, it never deletes.
- The CSV export and every new API response go through the existing leak matrix.

## 5. Out of scope

Data lineage between assets, automatic classification beyond the existing detectors,
deletion execution, per-item metadata, and versioned field schemas (the current schema
applies to all assets).

## 6. Acceptance

- R1-R9 implemented with unit, API, UI and E2E tests; every new route declares a permission.
- The DDL is changed in the Nodetto document first (`db/schema/telescreen.nodetto.jsonl`),
  exports regenerated, models and one Alembic revision (`0005`) match it.
- Leak matrix covers assets, events, items, field values, export and overdue notifications.
- Screenshot smoke covers the new pages in light and dark with zero browser errors.
- `mise run ci`, `mise run ddl-check` and the web checks pass; live verification on the dev
  stack shows discovery, list-item removal, deletion and the overdue notification.

## 7. Evidence re-check (v1.4 design author, 2026-10-13, at `07c975f`)

The conclusion of section 1 holds: no register, lifecycle history, item tracking or
metadata schema exists. Corrections and additions (details in
`design-v1-4-data-inventory.md` section 1):

| # | Item | Correction |
|---|------|-----------|
| C1 | Deletion evidence | `issues/ingest_transitions.py:197` is `resolve_auto`. `resource_deleted` versus `verified_removed` is chosen at `issues/service.py:403-406`. `resource_coverage` holds rows only while a resource is unscannable (`collectors/coverage.py:84-88`) |
| C2 | Push removal evidence | `ResourceBody` is at `api/routers/ingest.py:38-45`, and `ResourceContent` is built positionally at `ingest.py:109-117` and `issues/ingest.py:68-77`. `/findings`, police results and the police result schema cannot report removal either |
| C3 | Shared versioned schemas | `/api/ingest/v1` has no JSON Schema; the Bureau-SDK shared schemas are the police contract. v1.4 versions the police contract (1.2) and adds only optional members to the ingest v1 bodies |
| C4 | "Absence from a complete sweep" | Exists only for issues (`resolve_unchecked` after a `SUCCEEDED` map job; full-listing reconciliation of open-issue refs). v1.4 adds the asset equivalents |
| C5 | New finding | `/scan` and `/findings` key the same custom locator differently (`issues/service.py:524,558`). Kept in v1.4; owner question in `design-docs/user-qa/pending-custom-push-resource-identity.md` |
| C6 | R7 owner fallback | "Issue owner rules" means: the resource owner of the asset's open issues, then the ownership rules. The policy team is not used for assets |

## 8. Owner live verification (2026-10-13, after run 1)

Run 1 (session 15) accepted p01-p07 and blocked p08 on one missing E2E flow. The owner
then ran the live stack (PostgreSQL 55432 migrated to `0005_v1_4`, `telescreen serve`,
`telescreen worker`, the UI dev server, Keycloak SSO as alice, Mailpit). A scripted API
flow passed 29 of 30 checks:
- discovery with `inventory_mode=all`;
- 5 item keys, then 2 removed (one event with `items_removed=2`, removed items listed by
  12-hex prefix, item series);
- an incomplete scan that changed nothing;
- field schema creation with the incomplete flag, secret rejection without echo and
  required enforcement;
- manual registration and a manual `-120` change;
- push removal closing 3 items, then restore and CSV export;
- no raw sample or item key in any API response.

Owner fixes already applied in the run-2 checkpoint:

| Id | Defect | Fix |
|----|--------|-----|
| O1 | `DataOverdueProvider` used the 38-character `data-overdue:<ISO minute>` as `engine_tasks.ref_id` (`varchar(36)`). PostgreSQL rejected every enqueue (`schedule provider failed type=DBAPIError` each minute), so no overdue notification was ever sent. SQLite does not enforce varchar lengths, so every offline test passed | `ref_id` is now `data-overdue:<epoch minute>`; the ISO idempotency key is unchanged. `SqlTaskQueue.enqueue` rejects a `ref_id` over 36 or an idempotency key over 256 characters, so SQLite tests catch this class. Tests: `tests/inventory/retention/test_handler.py::test_overdue_provider_enqueues_through_the_sql_queue`, `tests/workflow/queue/test_queue_limits.py`. Live: alice received "Data past its deletion date: DATA-1" with no raw values |
| O2 | p08 blocker: no integrated full-listing E3 flow | `tests/e2e/test_flow_v14_inventory.py::test_e3_full_listing_deletes_absent_asset_only_when_authoritative`. Production-wired app, fake Slack adapter: incremental, skipped-ref and failed listings never delete, a completed full listing deletes once, a later sighting restores, and inventory `off` gives `unknown` with `tracking_stopped` and no deletion. A mutation of the skipped-ref guard fails it |

Defects found in the live UI that must be fixed in run 2 (the mock screenshots hid all
of them):

| Id | Defect | Evidence |
|----|--------|----------|
| L1 | The timeline shows "20,740 days ago" for every scan or system event: the UI formats `occurred_at`, which is null unless a person supplied it, instead of falling back to `recorded_at` | `/data/<id>` timeline; `GET /api/data/assets/<id>/events` returns `occurred_at: null` with a valid `recorded_at` |
| L2 | Events recorded by a collector show the actor "Unknown": `collector:<config id>` refs resolve to `{kind: "unknown", display_name: "Unknown"}` | same events response. Expected: the collector name (for example "Collector live-v14-...") or "Telescreen" for system work, as v1.3 did for issue activity |
| L3 | A `reference` field (Owner) shows "Choose..." although `value_json` is `"member:alice"` and the list shows "Alice Test" as owner. Saving the form without touching it must keep the value | `/data/<id>` metadata form; option values must be `member:<handle>` / `team:<slug>` to match the stored value |
| L4 | The Item count section shows "No item count history" while `GET /api/data/assets/<id>/item-series` returns 3 points (5, 3, 0) | `DataDetailSections.tsx` series handling |
| L5 | Text runs together: tab label and count ("All3", "Overdue1"), the timeline actor initials, name and text ("ATAlice Testrestored"), the list name and locator ("Clean docdocs/clean-...md"), the breadcrumb ("DataDATA-1") | `/data` and `/data/<id>` |
| L6 | The admin-only "Field schema" entry is missing from the Data secondary nav (design 10.1) and from the Settings secondary nav (the page is reachable only from the settings tab strip) | `/data`, `/settings/data-fields` |
| L7 | An overdue asset's row shows no Overdue badge in the State column (only `present`), although the Overdue tab counts it | `/data` |

The mock fixtures must also cover null `occurred_at`, collector actors, `reference`
values and a multi-point item series, so the strict screenshots show these states.

### 8.1 After run 2 (session 16)

Run 2 integrated and accepted v14-p09-live-fixes (L1-L7). Its integration review stopped
because owner fix O1 left one 101-character line in `src/telescreen/workflow/queue/sql_queue.py`
(ruff E501), a file no v14 plan owns. Owner fixes in the run-3 checkpoint:

| Id | Defect | Fix |
|----|--------|-----|
| O3 | `sql_queue.py` line over 100 characters broke repo-wide `ruff format --check` and `ruff check` | `ruff format` rewrap only; no behavior change |
| O4 | Live UI: after the L4 fix the item-count SVG had `width: 100%` and a viewBox but no height, so a 3-point series (viewBox 48x72) rendered about 1500 px tall | `ItemCountChart` sets the `height` attribute (CSS `max-height: 160px`); vitest `has a fixed pixel height so a few points cannot stretch it to the viewBox ratio` |

Owner live re-check on the run-2 tree: the scripted API flow passed 31 of 31 checks,
including the overdue email to the owner in Mailpit with no raw values. The SSO browser
smoke as alice showed L1-L7 fixed and no page, console or API errors on the Data list,
tabs, registration, both detail pages, the field schema, the dashboard, collectors and
issues.
