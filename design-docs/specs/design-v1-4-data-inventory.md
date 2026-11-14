# v1.4: Data Inventory

This document specifies Telescreen v1.4. The owner asked (2026-10-13, translated) whether
Telescreen keeps a data management list of where data lives and its audit history (when it
was added, when it was deleted). It does not, so v1.4 adds it. The list also tracks partial
changes of list-shaped data and holds admin-defined metadata (purpose of use, responsible
person, planned deletion date). The accepted requirements are
`design-v1-4-data-inventory-requirements.md` (R1-R9, safety constraints, out of scope,
acceptance). Its evidence was re-verified against the code for this design; the
corrections are in section 1 here and in its section 7.

Status: implemented 2026-10-14 on `feat/data-inventory`; the 9 `v14-pNN` plans and the
f1-f3 dispatch manifests are archived under `impl-plans/completed/`. Written 2026-10-13 at
`07c975f` as the design for the `v14-pNN` plans. It extends the implemented v1-v1.3 designs and replaces none of their
decisions. Run 2 (2026-10-13, base `3c5c1dd`) adds section 17: the owner fixes O1 and O2
and the live defects L1-L7, with plan `v14-p09-live-fixes`. Run 3 (2026-10-14, base
`42864aa`) adds section 17.7: p08 re-verify and the finalization. D1-D24 are unchanged.
The summaries are in:

- `design-data-model.md` ("v1.4 additions");
- `design-api-and-ui.md` ("v1.4 endpoints");
- `design-collectors-and-ingestion.md` ("v1.4 inventory hooks");
- `design-police-contract.md` ("Contract 1.2");
- `design-masking.md` ("v1.4 additions");
- `architecture.md` section 4 ("v1.4 additions").

## Overview

| Req | Summary | Section |
|-----|---------|---------|
| R1 | Asset register `data_assets` with `DATA-<n>` short ids | 4, 9 |
| R2 | Discovery from every scan path; per-collector `inventory_mode` (`sensitive_only`, `all`, `off`) | 5 |
| R3 | Deletion and restoration from definitive removal signals only; manual mark deleted and restore | 5 |
| R4 | Item keys or counts on the collector port, push ingest and police contract 1.2; keyed item fingerprints; finding-fingerprint ("value") diffs; manual partial changes | 6 |
| R5 | Admin field schema `data_fields` | 7 |
| R6 | Validated values `data_field_values`, required enforcement, `incomplete`, `metadata_changed` events | 7 |
| R7 | Overdue computation, Overdue tab, dashboard tile, one notification per due date | 8 |
| R8 | Data area, schema editor, registration, issue link, masked CSV export | 9, 10 |
| R9 | Audit records for every member write; append-only lifecycle events | 4, 11 |

Baseline facts (checked at `07c975f`):

- Every scan path ends in `IssueService.record_scan(session, ResourceScan)`
  (`issues/service.py:111`):
  - fetched content, through `ResourceScannerImpl` (`issues/scan.py`);
  - removed and empty resources, through `collectors/tasks.py` `_record_empty` and
    `collectors/sweep.py` `_record_empty`;
  - push findings and police results, through `record_external` (`issues/service.py`).

  `ResourceScan` carries `source`, `resource_key`, the redacted locator and title,
  `collector_config_id`, `scan_job_id`, the masked findings with their fingerprints and key
  versions, `complete`, `sweep_covered`, `removed` and `observed_at`.
- `issues/service.py` has 974 lines, so the hook needs a split (section 13).
- Every push source (`custom:<name>`) resolves to an enabled `custom` row in
  `collector_configs` (`issues/ingest.py:57-59`).
- Removal reaches the issue path only from definitive signals:
  - `ResourceGoneError`;
  - the `removed` task mode;
  - `ChangePage.removed`;
  - full-listing reconciliation (`collectors/tasks.py:249-266`);
  - sweep `ShardEntryKind.REMOVED`;
  - `resolve_unchecked`, which is called only for a `SUCCEEDED` map job
    (`workflow/mapreduce/reduce.py:129`).
- Fetch failures are recorded as incomplete scans with no segments.
- No dependency is added. Charts are hand-rendered SVG, as in v1.3.

## Details

### 1. Requirement verification and corrections

The requirements doc's section 1 conclusion is confirmed: there is no data register, no
lifecycle history, no item tracking and no metadata schema. These corrections and
additions are recorded there in section 7:

| # | Requirements text | Finding at `07c975f` |
|---|-------------------|----------------------|
| C1 | Deletion evidence `issues/ingest_transitions.py:197` | That line is `resolve_auto`. The choice of `resource_deleted` versus `verified_removed` is made at `issues/service.py:403-406`. `resource_coverage` rows exist only while a resource is unscannable (`collectors/coverage.py:84-88`), so a clean resource leaves no row at all |
| C2 | Push evidence `api/routers/ingest.py:38`, `ports/collectors.py:112` | `ResourceBody` (`ingest.py:38-45`) has no `removed`, and `ResourceContent` is built positionally at `ingest.py:109-117` and `issues/ingest.py:68-77`. `/findings`, police results (`ExternalScan`) and the police result schema (`additionalProperties: false`) cannot report removal either |
| C3 | "the push contract, Police SDK" share versioned JSON Schemas (R4, intake constraint) | The push ingest API (`/api/ingest/v1`) is validated by pydantic dataclasses only; no JSON Schema exists for it, and the SDK has no ingest client. The versioned schemas shared by the Bureau and the SDK are the police contract. v1.4 therefore versions the police contract (1.2, section 6.4) and makes only additive, optional changes to the ingest v1 bodies (section 6.3) |
| C4 | "absence from a complete sweep" (R3) | It exists only for issues: `resolve_unchecked` after a `SUCCEEDED` map job, and the full-listing reconciliation of open-issue refs. v1.4 adds the same two reconciliations for assets (section 5.4) |
| C5 | Not in the requirements | The two push endpoints key the same locator differently. `/scan` computes `resource_key(source, locator)`. `/findings` computes `resource_key(source, source + ":" + locator)` (`issues/service.py:524,558`), which is what `design-collectors-and-ingestion.md` specifies for `custom`. Assets inherit issue identity, so one locator pushed through both endpoints becomes two assets. Fixing it would change existing issue identity, so v1.4 keeps it and asks the owner (`design-docs/user-qa/pending-custom-push-resource-identity.md`) |
| C6 | "falling back to the asset's issue owner rules" (R7) | Issue routing is: the resource owner (`issues.owner_ref`), then ownership rules (source plus a glob over `display_locator`), then the policy team (`issues/ownership.py:216-266`). For assets v1.4 uses the first two (section 8.2), because the policy team is a decision about a finding, not about a location |

### 2. Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| D1 | Package | New L3 package `telescreen.inventory` owns the five new tables. UI label "Data", API prefix `/api/data` | Plain naming (architecture section 1); one owner per table set |
| D2 | Identity | One asset per `resource_key`, the identity issues already use (`HMAC(identity key v1, "resource\0" + source + "\0" + raw_locator)`). A registered asset uses `resource_key(f"registered:{source}", locator)`, so it never collides with a discovered one. `UNIQUE(resource_key)` makes a duplicate registration a 409 `conflict` | Issues and assets join on one key without a foreign key; key rotation never changes it |
| D3 | Inventory mode | `collector_configs.inventory_mode`: `sensitive_only` (default), `all`, `off`. A configless observation is treated as `sensitive_only` | R2. A new column, because `selector_json` drops unknown keys for built-in kinds and is returned to pushers for `custom` |
| D4 | Presence | Any observation with `removed = false` proves presence, complete or not. A fetch failure of an enumerated resource still proves presence | Keeps the sweep watermark sound (section 5.4) |
| D5 | Removal | Only `removed = true` observations and the two reconciliations of D6 mark an asset deleted. A partial or failed job never does | R3: incomplete scans never infer deletion |
| D6 | Absence | The asset reconciliations run where the issue ones run. After a `SUCCEEDED` map job, present assets of the config with `sweep_covered` and `last_seen_at < map_jobs.created_at` are deleted. After a full listing, present `sweep_covered` assets of the config that are not in the listed key set are deleted, but only when no listed ref was skipped | Same rule as issue reconciliation; no new completeness notion |
| D7 | Restore | A deleted asset observed with `removed = false` becomes present with a `restored` event. A member may restore a deleted asset; this is the only way back for registered assets | R3 |
| D8 | Mode `off` | Changing a config to `off` turns its present discovered assets `unknown` (`tracking_stopped` event). While `off`, observations are ignored. Turning the mode back on lets the next observation set `present` (`tracking_resumed` event) | Gives `unknown` (R1) one exact meaning: no longer tracked, existence not known |
| D9 | No per-scan events | Scans update `last_seen_at`, `sweep_covered`, the name and the counts. An event is written only when the state, the item set or count, or the value set changes | R2 |
| D10 | Item fingerprints | `item_secret = HMAC-SHA256(identity key v1, "telescreen:data-item:v1")` and `item_fingerprint = hex(HMAC-SHA256(item_secret, resource_key + "\0" + raw_item_key))`. This is computed in `Fingerprinter.item_key` before any persistence. Only 12-hex prefixes leave the Bureau | Domain separation from value fingerprints (R4, safety). Per-asset keying blocks cross-asset linking. Version 1 keeps identity stable across rotation |
| D11 | Removal on incomplete scans | Item and value additions are applied on every non-removed observation. Removals and count changes are applied only on complete ones | R4 with the R3 rule |
| D12 | Value diffs | The set of distinct finding fingerprints per asset is tracked like items (`kind = value`). When a complete observation's findings use a key version that differs from the stored rows, the stored set is replaced silently and no event is written | "3 of 5 values removed" for every asset, with no false removal after a key rotation |
| D13 | Deletion closes items | A deletion closes every open item and value row and sets `item_count` to 0 when it was tracked. The `deleted` event carries the removed counts | "how many items were deleted and when" |
| D14 | Metadata detector | Values, labels, option labels, names, locators and notes are checked with `Redactor.has_hits` (the rule detector), as for every persisted user text since v1.2. A hit returns 422 `raw_value_detected`; nothing is stored, echoed or logged | Same gate as templates, views and patterns. LLM detectors would add egress (`qa-llm-data-egress.md`) |
| D15 | Computed flags | `incomplete` and `overdue` are computed in SQL and not stored | They stay correct when the schema, required flags or roles change |
| D16 | Overdue | `overdue` = state is not `deleted`, a `deletion_due` value exists, and that date is before today (UTC, read through the `Clock`) | R7. `unknown` assets may still exist, so they stay visible |
| D17 | One-time notification | `data_assets.overdue_notified_for` stores the due date that was notified. The evaluator notifies when it is null or different, so a changed due date notifies again | "once" per due date |
| D18 | Notification channel | A member owner gets `send_to_member` with category `assigned`. A team owner gets a team-addressed `send` on email and Slack with `record_unreachable`. No new category | "through the existing notification settings" with no settings change |
| D19 | Dashboard | The dashboard page reads `GET /api/data/counts` for the overdue tile. `/api/dashboard` is unchanged | No issues-package edit; the tile hides on 403 |
| D20 | Issue link | `GET /api/data/resource-assets/{resource_key}`, using the `resource_key` the issue API already returns | No change to the issue API |
| D21 | Search | Assets are not indexed in Meilisearch. The register filters and searches with SQL | No new index; nothing new to guard |
| D22 | Route shapes | No literal segment under `/api/data/assets/`: counts are `/api/data/counts`, the export is `/api/data/export`, and the lookup is `/api/data/resource-assets/{key}` | Avoids the v1.3 `/api/issues/counts` ordering class of bug |
| D23 | Append-only | The event repository exposes insert only, and no route edits or deletes events. On PostgreSQL, `0005` installs a trigger that raises on `UPDATE` or `DELETE` of `data_asset_events`, like `audit_records` | R9 |
| D24 | Discovered name | A discovered asset's name follows the latest non-empty redacted scan title. Removed and failed-fetch observations carry an empty title and never change the name. An asset created from an observation with an empty title is named after its `display_locator`. Members can edit the name of registered assets only. `kind` is editable for every asset | No manual or scan conflict on the name |

### 3. Foundation: vocabulary, permissions, settings, ports, keys

New enums in `telescreen.core.vocabulary` (persisted as strings):

| Enum | Values |
|------|--------|
| `InventoryMode` | `sensitive_only`, `all`, `off` |
| `DataAssetOrigin` | `discovered`, `registered` |
| `DataAssetState` | `present`, `deleted`, `unknown` |
| `DataAssetKind` | `file`, `table`, `list`, `channel`, `object`, `folder`, `other` |
| `DataEventKind` | `discovered`, `registered`, `items_changed`, `values_changed`, `deleted`, `restored`, `metadata_changed`, `manual_change`, `tracking_stopped`, `tracking_resumed` |
| `DataItemKind` | `item`, `value` |
| `DataFieldType` | `text`, `long_text`, `integer`, `decimal`, `boolean`, `date`, `single_select`, `multi_select`, `reference`, `url` |
| `DataFieldRole` | `purpose`, `owner`, `deletion_due` |
| `DataAssetTab` | `all`, `present`, `deleted`, `incomplete`, `overdue` |
| `NotificationEvent` | adds `data_overdue` |
| `EngineTaskKind` | adds `data_overdue_check` |
| `ErrorCode` | adds `field_in_use` (409), `required_field_missing` (422), `invalid_field_value` (422) |

Permissions (`core/permissions.py`). V = viewer, R = responder, A = admin:

| Permission | V | R | A | Routes |
|-----------|---|---|---|--------|
| `data.read` | x | x | x | list, counts, detail, events, items, series, related lookups, fields list, export |
| `data.write` | | x | x | register, edit, values, validate values, manual event, mark deleted, restore |
| `data_fields.admin` | | | x | create, edit, reorder, archive, unarchive, delete fields |

`data.read` joins `READ_PERMISSIONS` automatically. `data.write` is added to
`WRITE_PERMISSIONS`. The `read`, `write` and `admin` scopes follow as in v1.2.

Settings (`core/config.py`): `TELESCREEN_DATA_OVERDUE_CRON`, default `0 9 * * *` (UTC);
an empty value disables it. It is validated with the existing cron helper.

Keys (`masking/fingerprint.py`): `Fingerprinter.item_key(resource_key: str, raw_item_key:
str) -> str` implements D10. The raw key parameter is never logged and never kept.

Ports (`telescreen.ports`; DTOs are frozen dataclasses, interfaces are Protocols):

- New `ports/inventory.py`:
  - `ItemObservation(fingerprints: tuple[str, ...] | None, count: int | None)`. Exactly
    one member is set.
  - `ValueFingerprint(fingerprint: str, key_version: int)`.
  - `ResourceObservation(source, resource_key, display_locator, display_title,
    collector_config_id: str | None, scan_job_id: str | None, observed_at, complete: bool,
    removed: bool, sweep_covered: bool, values: tuple[ValueFingerprint, ...], items:
    ItemObservation | None, kind_hint: DataAssetKind | None)`.
  - `InventoryObserver.observe(session, ResourceObservation) -> None`.
  - `InventoryReconciler`:
    - `reconcile_sweep(session, collector_config_id, seen_before) -> int`;
    - `reconcile_listing(session, collector_config_id, listed_keys: frozenset[str]) -> int`;
    - `stop_tracking(session, collector_config_id, actor_ref) -> int`.
  - `AssetOwnerResolver.fallback_owner(session, source, resource_key, display_locator) ->
    str | None`, which returns a `member:<handle>` or `team:<slug>` ref.
  - `NoopInventoryObserver`, `NoopInventoryReconciler`.
- `ports/collectors.py`:
  - `ItemReport(keys: tuple[str, ...] | None = field(default=None, repr=False), count: int
    | None = None)`;
  - `ResourceContent` gains `items: ItemReport | None = None` and `asset_kind:
    DataAssetKind | None = None`;
  - `CollectorConfig` gains `inventory_mode: InventoryMode = InventoryMode.SENSITIVE_ONLY`.
- `ports/findings.py`:
  - `ResourceScan` gains `items: ItemObservation | None = None` and `asset_kind:
    DataAssetKind | None = None`;
  - new `LocatorInventory(locator (repr=False), item_fingerprints: tuple[str, ...] | None,
    item_count: int | None, kind: DataAssetKind | None)`;
  - `ExternalScan` gains `inventories: tuple[LocatorInventory, ...] = ()`.
- `ports/notifications.py`: `NotificationContext` gains `data_short_id`, `data_name`,
  `data_locator`, `data_deletion_due` and `data_link`. All are `str | None = None`.

Every new field has a default, so existing constructors and tests keep working.

Built-in template `system.data_overdue` (event `data_overdue`):

- subject: "Data past its deletion date: {{ data.short_id }}";
- body: the name, locator, due date and link.

Its variable group `data` has the dotted paths `data.short_id`, `data.name`,
`data.locator`, `data.deletion_due` and `data.link`. They map to the `data_*` attributes of
`NotificationContext`. The synthetic preview constants are clean under the rule detector. This
follows the v1.2 template rules.

### 4. Data model (revision `0005_v1_4`)

DDL order (AGENTS.md "Database Schema (DDL)"):

1. Edit `db/schema/telescreen.nodetto.jsonl` directly.
2. Run `nodetto-cli validate`.
3. Run `nodetto-cli arrange db/schema/telescreen.nodetto.jsonl --write`.
4. Run `mise run ddl-export`.
5. Update the models.
6. Add one Alembic revision, `0005_v1_4` (`down_revision = "0004_v1_3"`).

The JSONL edit adds:

- a frame element "Data inventory" (`f-data-inventory`);
- five `erd.table` elements in the shape of the existing records;
- the `inventory_mode` column on `t-collector-configs`;
- six `erd.relation` elements with `logical: true`:
  - `data_assets.collector_config_id -> collector_configs.id`;
  - `data_asset_events.asset_id -> data_assets.id`;
  - `data_asset_events.scan_job_id -> scan_jobs.id`;
  - `data_asset_items.asset_id -> data_assets.id`;
  - `data_field_values.asset_id -> data_assets.id`;
  - `data_field_values.field_id -> data_fields.id`.

`*_ref` and `*_by` actor columns are not drawn (README convention). `mise run ddl-check`
and `tests/db/test_nodetto_schema.py` must pass.

Model module: `src/telescreen/db/models/inventory.py`. Types are portable.

| Table | Columns | Keys and indexes |
|-------|---------|------------------|
| `data_assets` | `id` varchar(36) PK; `number` integer; `resource_key` varchar(64); `source` varchar(64); `origin`, `kind`, `state` varchar(16); `name` varchar(512); `display_locator` text; `collector_config_id` varchar(36) null; `sweep_covered` boolean default false; `item_count` integer null; `first_seen_at` timestamptz; `last_seen_at`, `deleted_at` timestamptz null; `overdue_notified_for` varchar(10) null; `created_by` varchar(256) null; `created_at`, `updated_at` timestamptz | unique `number`; unique `resource_key`; `ix_data_assets_source`; `ix_data_assets_config_state` (`collector_config_id`, `state`, `last_seen_at`); `ix_data_assets_state_last_seen` (`state`, `last_seen_at`) |
| `data_asset_events` | `id` PK; `asset_id` varchar(36); `kind` varchar(32); `occurred_at` null; `recorded_at`; `actor_ref` varchar(256); `scan_job_id` varchar(36) null; `items_added`, `items_removed`, `item_count_after`, `values_added`, `values_removed`, `value_count_after` integer null; `field_key` varchar(48) null; `old_value`, `new_value`, `note` text null | `ix_data_asset_events_asset_recorded` (`asset_id`, `recorded_at`) |
| `data_asset_items` | `id` PK; `asset_id`; `kind` varchar(8); `fingerprint` varchar(64); `key_version` integer default 1; `first_seen_at`; `removed_at` null | `uq_data_asset_items_key` UNIQUE (`asset_id`, `kind`, `fingerprint`); `ix_data_asset_items_asset_state` (`asset_id`, `kind`, `removed_at`) |
| `data_fields` | `id` PK; `key` varchar(48); `label` varchar(80); `description` varchar(500) null; `field_type` varchar(16); `options_json` text null; `required` boolean default false; `position` integer; `role` varchar(16) null; `archived_at` null; `created_by` varchar(256); `created_at`, `updated_at` | unique `key` |
| `data_field_values` | `asset_id`, `field_id` varchar(36) (composite PK); `value_json` text; `value_date` varchar(10) null; `updated_by` varchar(256); `updated_at` | `ix_data_field_values_field_date` (`field_id`, `value_date`) |
| `collector_configs` (changed) | `inventory_mode` varchar(16) not null, server default `sensitive_only` | - |

`0005_v1_4` is idempotent like `0004`: it inspects the schema, creates the missing tables,
column and indexes, and seeds `counters('data_asset_number', 0)` when missing. On
PostgreSQL only, it installs the append-only trigger of D23. Downgrade drops what exists.
`tests/db/test_models.py` gains the five tables (53 in total).

Content rules:

- `name` and `display_locator` are the scan's redacted title and locator, or
  `Redactor`-clean member input.
- `fingerprint` is HMAC hex.
- `value_json` is canonical JSON of a validated, `Redactor`-clean value.
- `old_value` and `new_value` hold that value's display text, cut to 500 characters.
- `note` is `Redactor`-clean member text of at most 1000 characters.
- No column holds a raw item key, a raw locator or a detected value.

### 5. Observation and lifecycle (R1-R3)

#### 5.1 Hook points (no behavior change for issues)

| Path | Change |
|------|--------|
| `issues/scan.py` `_resource_scan` | Builds `ResourceScan.items` from `content.items`: keys -> `Fingerprinter.item_key` (deduplicated), or the count. Also sets `asset_kind`. Raw keys are dropped with the content |
| `IssueService.record_scan` | After the existing reconcile and `last_checked_at` block, in the same transaction, calls `self.inventory.observe(session, observation)`. The observation is built from the `ResourceScan`: values = distinct `(fingerprint, key_version)` of its findings. The field `inventory: InventoryObserver` defaults to the no-op |
| `IssueService.record_external` (moved to `issues/external_scan.py`, section 13) | For each locator, a matching `LocatorInventory` becomes `ResourceScan.items` and `asset_kind`. Item fingerprints are re-keyed with `Fingerprinter.item_key(resource_key, external_fingerprint)` |
| `workflow/mapreduce/reduce.py` | Right after `resolve_unchecked` (`SUCCEEDED` only), calls `inventory.reconcile_sweep(session, config_id, job.created_at)` |
| `collectors/tasks.py` `_changes` | Keeps the listed key set it already builds. When `full_listing` is set and no ref was skipped for a `Redactor` hit, it calls `inventory.reconcile_listing(session, config.id, frozenset(listed))` after the issue reconciliation |
| `collectors/service.py` update | When `inventory_mode` changes to `off`, calls `inventory.stop_tracking(session, config.id, actor_ref)` in the same transaction. Create and update accept `inventory_mode`, and the existing `collector.updated` audit details add it |
| `collectors/google/adapter.py` (gsheets branch of `fetch`, around line 205; `sheets.py` is only the API client) | A complete gsheets fetch sets `items = ItemReport(count = rows with any non-empty cell, summed over sheets)` |
| Push ingest and police results | Section 6.3 and 6.4 |

Default kinds when there is no hint:

- `gdrive`, `gdocs`, `github`, and Slack files (`/file/` locators): `file`;
- `gsheets`: `table`;
- Slack messages: `object`;
- `custom:*` and `police:*`: `other`.

#### 5.2 `observe` algorithm (`telescreen.inventory.observer`)

1. Read the config's `inventory_mode` through `CollectorConfigReader`. If it is `off`,
   stop.
2. Load the asset by `resource_key`, locked `FOR UPDATE` on PostgreSQL.
3. `removed = true`:
   - no asset, or the asset is already `deleted`: stop;
   - otherwise apply the deletion of section 5.3, with actor `collector:<config id>`
     (`system:inventory` when there is no config).
4. `removed = false` and no asset:
   - in mode `sensitive_only` with no values: stop;
   - otherwise insert the asset:
     - `origin = discovered`, `state = present`;
     - `number` from the `data_asset_number` counter, locked and incremented in the
       transaction, as `issue_number`;
     - `first_seen_at = last_seen_at = observed_at`;
     - kind from the hint or the default.

     Then apply values and items as additions and write one `discovered` event with
     `item_count_after` and `value_count_after`. A concurrent insert of the same key
     (`IntegrityError` inside `begin_nested`) re-reads the row and continues with the
     next step.
5. `removed = false` and the asset exists:
   - `deleted` -> `present`, clear `deleted_at`, `restored` event;
   - `unknown` -> `present`, `tracking_resumed` event;
   - set `last_seen_at = max(last_seen_at, observed_at)`, `sweep_covered`, the name (D24)
     and `collector_config_id` when it was null;
   - apply section 6.1 and 6.2. Each changes at most one event.

#### 5.3 Deletion, restoration and manual lifecycle

- Deletion sets `state = deleted` and `deleted_at`. It closes the open item and value rows
  (`removed_at`) and sets `item_count = 0` when it was not null. It writes one `deleted`
  event with `items_removed`, `item_count_after`, `values_removed` and
  `value_count_after = 0`.
- Manual mark deleted (`data.write`) applies the same deletion with the member actor, the
  given `occurred_at` (default now) and a note. A later observation of a discovered asset
  restores it (D7).
- Manual restore (`data.write`) is allowed only from `deleted`. It writes a `restored`
  event; items stay closed until the next observation.
- Registration writes a `registered` event (section 7.4).

#### 5.4 Reconciliation (D6)

- `reconcile_sweep` and `reconcile_listing` select present assets of the config with
  `sweep_covered = true`, using `ix_data_assets_config_state`. Neither runs when the
  config mode is `off`. Each selected asset is deleted as in 5.3, with actor
  `collector:<config id>` and `scan_job_id` null.
- `stop_tracking` sets present discovered assets of the config to `unknown` and writes
  `tracking_stopped` with the member actor.
- The sweep watermark is sound because every sweep shard records every enumerated resource
  (fetched, failed or too large) through `record_scan` with `removed = false`, and D4
  counts that as presence. Assets of `custom` sources are never `sweep_covered`, so only
  explicit removal deletes them.

### 6. Items and values (R4)

#### 6.1 Item diff (`telescreen.inventory.items`)

Fingerprint list (keys or police fingerprints):

- every fingerprint not open for the asset is inserted, or reopened with `removed_at = null`
  (`first_seen_at` is kept) -> `added`;
- if the observation is complete, every open item not in the list gets
  `removed_at = observed_at` -> `removed`;
- `item_count` = the number of open item rows.

Count only, and only when the observation is complete:

- `item_count` null -> set it, and write `items_changed` with `item_count_after` only (the
  baseline);
- otherwise, when the count differs, write `items_changed` with `items_added = max(d, 0)`,
  `items_removed = max(-d, 0)` and `item_count_after`. This is a net change; the design
  docs and UI say so.

An `items_changed` event is written only when `added + removed > 0` or a baseline is set.
Additions on an incomplete observation are recorded with `items_removed = 0`.

#### 6.2 Value diff

Values are the distinct `(fingerprint, key_version)` pairs of the observation's findings.
They are stored as `data_asset_items` rows with `kind = value`, using the same rules as
6.1. The event is `values_changed`.

Re-baseline (D12): on a complete observation whose versions are all `v`, when every open
value row has a version other than `v`, those rows are closed and the new rows inserted,
and no event is written.

#### 6.3 Push ingest v1 (additive; "ingest 1.1" in the docs)

`POST /api/ingest/v1/scan` resource members (all optional; existing payloads are
unchanged):

| Member | Rule |
|--------|------|
| `removed: boolean = false` | When true, `segments` must be empty and `complete` true; otherwise 422 `validation_error`. It becomes `ResourceContent.removed`, so open issues of the resource auto-resolve as `resource_deleted` (the existing removal semantics) and the asset is deleted |
| `item_keys: string[]` | At most 100000 entries of 1-1024 characters, `repr=False`, keyed on receipt (D10) and never returned or logged |
| `item_count: integer` | 0 to 2^53-1. Sending both `item_keys` and `item_count` is 422 `validation_error` |
| `kind: DataAssetKind` | Kind hint |

`POST /api/ingest/v1/findings` gains `item_fingerprints: string[]` (pattern
`^[0-9a-f]{64}$`, at most 10000), `item_count` and `kind`, with the same exclusivity. They
become one `LocatorInventory` for the body's `locator`. The body size limit, the
`Redactor` re-scan and the response shapes are unchanged.

#### 6.4 Police contract 1.2

- New closed schema `resource-inventory.schema.json`:
  - `locator`: string, at most 1024, required;
  - `item_fingerprints`: array of at most 10000 strings matching `^[0-9a-f]{64}$`;
  - `item_count`: integer, at least 0;
  - `kind`: `DataAssetKind`;
  - `not: {required: [item_fingerprints, item_count]}`.
- `result-envelope.schema.json` gains the optional member `resource_inventories`, an array
  of at most 1000 `$ref` entries.
- Rules, as for 1.1 exposures:
  - accepted on `detect` and `scan_shard`, ignored on other task types;
  - each `locator` must also be a finding locator or in `complete_locators`; other
    entries are dropped, and the count goes in the audit details;
  - every string passes the `Redactor` scan (a hit gives 422 `raw_value_detected`);
  - for `scan_shard` with `resources_failed > 0`, `complete_locators` is already emptied,
    so the inventories only add.
- The Bureau accepts 1.0, 1.1 and 1.2 envelopes against the same files. `openapi.json`
  references the schema, and `examples/result-envelope-1.2.json` is added.
- SDK changes:
  - frozen `ResourceInventory`;
  - `ResultEnvelope.resource_inventories: tuple[ResourceInventory, ...] = ()`, serialized
    only when non-empty;
  - `item_fingerprint(item_key: str, key: bytes) -> str` in `masking.py`: hex
    HMAC-SHA256 of the item key with the worker's own stable secret. It follows the
    existing `external_fingerprint(value, key)`, so workers never send raw keys or an
    unkeyed hash that a low-entropy key could be brute-forced from. The secret must stay
    stable across scans, or every item looks replaced;
  - `CONTRACT_VERSION = "1.2"`.

  The SDK still imports only stdlib and httpx.
- Contract tests validate SDK-serialized 1.0, 1.1 and 1.2 envelopes against the same
  schemas.

#### 6.5 Manual partial change

`POST /api/data/assets/{id}/events` takes:

- `occurred_at`: required, not after now + 5 minutes;
- `items_added?`, `items_removed?`, `item_count_after?`: integers of at least 0;
- `note`: 1-1000 characters, `Redactor`-checked.

It writes a `manual_change` event and audit `data_asset.event_recorded`. On a registered
asset it also updates `item_count`: `item_count_after` when given, otherwise
`max(0, (item_count or 0) + added - removed)`. On a discovered asset it is an annotation
only; the scans own the count.

### 7. Field schema and values (R5, R6)

#### 7.1 Field rules (`telescreen.inventory.fields`)

- `key`: `^[a-z][a-z0-9_]{0,47}$`, unique including archived fields, and immutable.
- `label`: 1-80 characters. `description`: at most 500 characters. Both are
  `Redactor`-checked.
- `options` (`single_select`, `multi_select` only, required there): 1-100 entries of
  `{value: ^[a-z0-9][a-z0-9_-]{0,63}$ (unique), label: 1-80, Redactor-checked}`.
- `role` compatibility:
  - `purpose`: `text`, `long_text`, `single_select` or `multi_select`;
  - `owner`: `reference`;
  - `deletion_due`: `date`.

  At most one non-archived field per role (409 `conflict`).
- At most 100 fields. A new field gets `position = max + 1`. Reorder takes the exact id
  list of the non-archived fields; archived fields keep their positions and are listed
  after them.
- Edit changes `label`, `description`, `options`, `required` and `role`.
  - `field_type` may change only while the field has no values (409 `field_in_use`).
  - Removing an option value that some value uses is 409 `field_in_use`.
- Archive sets `archived_at`. The field disappears from forms, columns, required checks
  and role use; its values are kept. Unarchive re-checks the role conflict.
- Hard delete is allowed only when the field has no values (409 `field_in_use`).
- Every change is audited (section 11).

#### 7.2 Value encoding and validation

`value_json` on the wire is a JSON-encoded string, following "Free-form JSON on the wire"
in `design-api-and-ui.md`.

| Type | JSON value | Rule |
|------|------------|------|
| `text` | string | 1-500 characters, single line |
| `long_text` | string | 1-4000 characters |
| `integer` | integer | -2^53+1 to 2^53-1 |
| `decimal` | string | `^-?[0-9]{1,18}(\.[0-9]{1,6})?$` (exact; never a float) |
| `boolean` | boolean | - |
| `date` | string | `YYYY-MM-DD`, a valid calendar date; also stored in `value_date` |
| `single_select` | string | one option value |
| `multi_select` | array of strings | 1-20 distinct option values |
| `reference` | string | `member:<handle>` of an active member, or `team:<slug>` of an existing team (`RecipientResolver.assignee_of`) |
| `url` | string | `https://`, at most 2048 characters, no userinfo (the v1.3 external-link rule) |

Every string leaf is checked with `Redactor.has_hits`. A hit returns 422
`raw_value_detected` with a static detail; the value is not echoed, logged or audited.
Type errors return 422 `invalid_field_value`.

`POST /api/data/fields/validate-values {values}` (`data.write`) returns `{valid, errors:
[{field, code}]}`, where `code` is one of `required`, `invalid_type`, `invalid_option`,
`too_long`, `unknown_field`, `archived_field`, `unknown_reference`, or
`raw_value_detected`. Only the admin-defined key is echoed. The UI calls it before saving,
so the user sees errors per field.

#### 7.3 Writing values

`PUT /api/data/assets/{id}/fields {values: [{key, value_json: string | null}]}`
(`data.write`):

- null clears the value; keys of archived or unknown fields are 422;
- after the change, every required non-archived field must have a value, otherwise 422
  `required_field_missing` (R6);
- the change is atomic;
- each changed field writes one `metadata_changed` event (`field_key`, old and new display
  text, actor);
- the request writes one audit record `data_asset.fields_changed` with the field keys.

Values of deleted assets stay editable.

`incomplete` (D15) means some non-archived required field has no value row for the asset.
Discovered assets are never rejected; they are only flagged.

#### 7.4 Manual registration

`POST /api/data/assets` (`data.write`) takes:

- `name`: 1-200 characters;
- `source`: `^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,63}$`;
- `locator`: 1-1024 characters;
- `kind`;
- `item_count?`;
- `values`.

`name`, `source` and `locator` are `Redactor`-checked. Values follow 7.2 and 7.3 (required
enforced). On success it returns 201 with the detail. It writes a `registered` event (with
`item_count_after` when given), `metadata_changed` events for the values, and audit
`data_asset.registered`. A duplicate `(source, locator)` is 409 `conflict` (D2).
`created_by` is the actor ref.

### 8. Retention (R7)

#### 8.1 Evaluation

`DataOverdueProvider.tick(session, now)` follows `WeeklyReportProvider`. On each
`TELESCREEN_DATA_OVERDUE_CRON` fire it enqueues a `data_overdue_check` task with
idempotency key `data-overdue:<fire minute ISO>`.

The handler selects overdue assets (D16) whose `overdue_notified_for` is null or differs
from the due date, in batches of 200 until none are left. Each asset is handled in its own
transaction:

1. Resolve the recipient (8.2).
2. Send (D18) with `NotificationContext(data_short_id, data_name, data_locator,
   data_deletion_due, data_link = TELESCREEN_PUBLIC_BASE_URL + "/data/<id>")`.
3. Set `overdue_notified_for` to the due date.
4. Append audit `data_asset.overdue_notified` (`asset_id`, `recipient_kind`
   `member|team|none`, `notification_count`).

An asset without a recipient is still marked and audited with `none`, so it is evaluated
once per due date. It stays in the Overdue view. When no `deletion_due` field exists, the
handler does nothing.

#### 8.2 Recipient

1. The value of the non-archived `owner` role field.
2. Otherwise `AssetOwnerResolver.fallback_owner`, implemented in `issues/asset_owner.py`
   (C6):
   - the resource owner of an open issue with the same `resource_key` (`issues.owner_ref`
     -> `OwnerDirectory.member_for_owner` -> `member:<handle>`);
   - else the first matching enabled ownership rule over (`source`, `display_locator`) ->
     `team:<slug>`.

   Registered assets use the rules only.
3. Otherwise none.

Member recipients follow the member's `assigned` setting (an `off` setting gives a
`skipped` row). Team recipients get one email and one Slack `send`; an unreachable channel
records a `skipped` row. Everything passes the template rendering, `Redactor` and the DLP
gate.

### 9. API (`/api/data`, R8, R9)

Every route declares exactly one permission. Responses contain only the columns of section
4, masked by construction.

| Endpoint | Permission | Body / response |
|----------|-----------|-----------------|
| `GET /api/data/assets` | `data.read` | Query below -> `{items: DataAsset[], next_cursor}` |
| `GET /api/data/counts` | `data.read` | Same filters without `tab` -> `{all, present, deleted, incomplete, overdue}` |
| `GET /api/data/export` | `data.read` | Same filters -> `text/csv` (9.2) |
| `GET /api/data/resource-assets/{resource_key}` | `data.read` | `^[0-9a-f]{64}$` -> `{asset: DataAsset or null}` |
| `POST /api/data/assets` | `data.write` | 7.4 -> 201 `DataAssetDetail` |
| `GET /api/data/assets/{id}` | `data.read` | `DataAssetDetail` |
| `PATCH /api/data/assets/{id}` | `data.write` | `{name? (registered only, else 422), kind?}` -> `DataAssetDetail`; audit `data_asset.updated` (field names) |
| `PUT /api/data/assets/{id}/fields` | `data.write` | 7.3 -> `DataAssetDetail` |
| `GET /api/data/assets/{id}/events` | `data.read` | `?limit&cursor`, newest first -> `{items: DataEvent[], next_cursor}` |
| `POST /api/data/assets/{id}/events` | `data.write` | 6.5 -> `DataEvent` |
| `POST /api/data/assets/{id}/mark-deleted` | `data.write` | `{occurred_at?, note?}` -> `DataAssetDetail`; 409 `conflict` when already deleted |
| `POST /api/data/assets/{id}/restore` | `data.write` | `{note?}` -> `DataAssetDetail`; 409 `conflict` unless deleted |
| `GET /api/data/assets/{id}/items` | `data.read` | `?kind=item|value&state=present|removed&limit&cursor` -> `{items: DataItem[], next_cursor}` |
| `GET /api/data/assets/{id}/item-series` | `data.read` | `?days=1-365` (default 90) -> `{points: [{at, item_count}]}` from `item_count_after` of the events, plus the last point before the range |
| `GET /api/data/fields` | `data.read` | `{items: DataField[]}` (archived included, flagged) |
| `POST /api/data/fields` | `data_fields.admin` | `{key, label, description?, field_type, options?, required, role?}` -> `DataField` |
| `PATCH /api/data/fields/{id}` | `data_fields.admin` | 7.1 -> `DataField` |
| `DELETE /api/data/fields/{id}` | `data_fields.admin` | 204; 409 `field_in_use` |
| `POST /api/data/fields/{id}/archive`, `/unarchive` | `data_fields.admin` | `DataField` |
| `POST /api/data/fields/reorder` | `data_fields.admin` | `{ids}` -> `{items: DataField[]}` |
| `POST /api/data/fields/validate-values` | `data.write` | 7.2 |

`POST /api/data/fields/reorder` and `/validate-values` are registered before the
`{id}` routes of the same router.

List query (`assets`, `counts`, `export`):

- `tab`;
- `q` (at most 200 characters, a substring over name, locator and source; a `Redactor` hit
  is 422 `raw_value_detected` with no echo);
- `source`, `kind`, `state`, `origin`, `collector_config_id`;
- `owner` (`member:<handle>` or `team:<slug>`, matched on the owner role value);
- `field` (repeatable, at most 5, `<key>:<value>`; equality on the canonical value,
  containment for `multi_select`);
- `sort` (`last_seen` default, `first_seen`, `name`, `item_count`, `deletion_due`,
  `short_id`; `-` prefix for descending);
- `limit` (1-200, default 50) and an opaque keyset `cursor`.

#### 9.1 Response shapes (binding for the UI types and mock)

- `DataAsset`:
  - `id`, `short_id` (`DATA-<n>`), `number`, `name`, `source`, `display_locator`, `kind`,
    `origin`, `state`;
  - `item_count` (number or null);
  - `item_trend`: 14 daily last-known counts ending today; `[]` when `item_count` is null;
  - `open_issue_count`;
  - `owner`: `{ref, display_name}` or null, from the owner role value through
    `ActorDirectory`;
  - `deletion_due`: `YYYY-MM-DD` or null;
  - `overdue`, `incomplete`;
  - `first_seen_at`, `last_seen_at`, `deleted_at` (string or null);
  - `collector_config_id` (string or null).
- `DataAssetDetail` = `DataAsset` plus:
  - `sweep_covered`;
  - `created_by`: `{kind, ref, display_name}` or null;
  - `value_count`;
  - `fields`: `[{key, label, field_type, required, role, options, value_json (string or
    null)}]`, in position order, non-archived only;
  - `missing_required`: field keys;
  - `open_issues`: `[{issue_id, short_id}]`, from `ResourceIssueReader`.
- `DataEvent`: `id`, `kind`, `occurred_at`, `recorded_at`, `actor {kind, ref,
  display_name}`, `items_added`, `items_removed`, `item_count_after`, `values_added`,
  `values_removed`, `value_count_after`, `field_key`, `old_value`, `new_value`, `note`.
  Unused members are null.
- `DataItem`: `id`, `kind`, `key_prefix` (the first 12 hex characters), `first_seen_at`,
  `removed_at`.
- `DataField`: `id`, `key`, `label`, `description`, `field_type`, `options [{value,
  label}]`, `required`, `position`, `role`, `archived`, `value_count`, `created_at`,
  `updated_at`.

The key sets are recorded once in `src/web/src/api/mock/fixtures/v14-shapes.json` (section
10.3). Both the UI tests and a Python contract test compare against it.

#### 9.2 CSV export

- Columns:
  - `short_id`, `name`, `source`, `display_locator`, `kind`, `origin`, `state`;
  - `item_count`, `open_issue_count`, `owner`, `deletion_due`, `overdue`, `incomplete`;
  - `first_seen_at`, `last_seen_at`, `deleted_at`;
  - then `field:<key>` for each non-archived field, in position order (`multi_select`
    joined with `;`).
- Every cell passes `Redactor.redact` as a final guard (the stored data is already clean).
- A cell that starts with `=`, `+`, `-`, `@`, a tab or a CR gets a leading `'`
  (formula injection).
- At most 10000 rows, with `X-Telescreen-Truncated: true` when the limit cuts the export.
- `Content-Disposition: attachment; filename="telescreen-data-<YYYYMMDD>.csv"`.
- Audit `data_asset.exported` (`row_count`, `truncated`, the names of the filters used).

### 10. UI (R8)

Follows `design-ui-sentry.md` and the existing components. No new dependency.

#### 10.1 Shell and routes

- `AreaKey` `data` (label "Data", after Alerts) with the lucide `Database` rail icon.
  `shell.test.tsx` and its rail order are updated.
- Secondary nav: All data, Overdue, Incomplete, Register data and, for
  `data_fields.admin`, Field schema.
- Routes:
  - `/data` (list, `?tab=`);
  - `/data/new`;
  - `/data/:id`;
  - `/settings/data-fields` (a `Match` in the settings page; the nav link is gated by
    `data_fields.admin`).
- A generic `CountTabs` component (tab plus count badge, typed by a string union) is added.
  `StreamTabs` stays as it is.
- `ItemCountChart` is a hand-rendered SVG step line over `item-series` points, with an
  accessible label.

#### 10.2 Pages

| Page | Behavior |
|------|----------|
| Data list | `CountTabs` (All, Present, Deleted, Incomplete, Overdue) from `/api/data/counts`. Filter bar: search, source, kind, state, origin, owner, field chips. Columns: name with locator, source, state tag, item count with `Sparkline` of `item_trend`, open issues, owner `Avatar`, deletion due (danger style when overdue), last seen. Export CSV downloads `/api/data/export` with the current filters. Register data button (`data.write`) |
| Data detail | Header: short id, name, state tag, origin, kind, source, locator. Actions (`data.write`): Edit (name for registered assets, kind), Record event, Mark deleted or Restore. An incomplete banner lists the missing fields. Metadata form: inputs per type, required markers, `validate-values` before save, per-field errors. Timeline from `/events` with `Avatar`, kind labels, counts and field changes. `ItemCountChart`. Items section with Items and Values tabs and a present/removed filter (key prefix, first seen, removed at). Related issues as `TELE-n` links |
| Register data | Name, source, locator, kind, item count, and the metadata form with required fields |
| Settings > Field schema | Ordered table with move up and down. A create/edit drawer: key editable only on create, type disabled when `value_count > 0`, options editor, required toggle, role select. Archive, unarchive, and delete (disabled when `value_count > 0`) |
| Issue detail | A sidebar section "Data asset" linking `DATA-n`, from `/api/data/resource-assets/{resource_key}`. Hidden without `data.read` or when there is no asset |
| Dashboard | An "Overdue data" tile from `/api/data/counts`, linking `/data?tab=overdue`. Hidden on 403 |
| Collectors | The editor gains an "Inventory mode" select. The body sends `inventory_mode`; the API treats a missing value as "keep" |

#### 10.3 Mock API and screenshots

- `src/web/src/api/mock/fixtures/v14.ts` holds the seed:
  - at least 24 assets covering all states, origins and kinds, overdue and incomplete
    ones, list-shaped assets with item series and removed items;
  - six fields covering the three roles and the select, date and reference types;
  - events of every kind.

  Values follow the Masker formats and reserved example names. `tests/leak/test_ui_fixtures.py`
  must pass.
- `v14-shapes.json` lists the exact key set of each type in 9.1. `fixtures.test.ts`
  asserts every v14 fixture object has exactly those keys.
- `scripts/screenshots.ts` adds the following, in light and dark with zero page or console
  errors:
  - `/data` (each tab), detail (timeline, items, values tab), `/data/new`;
  - `/settings/data-fields` (with the editor open);
  - the issue detail asset link, the dashboard tile, and the collector editor.

### 11. Audit (R9)

All writes append `audit_records` in the same transaction as the change. The details hold
ids, enums, counts and field keys only, never values, names, locators or notes:

- `data_asset.registered`, `data_asset.updated`, `data_asset.fields_changed`;
- `data_asset.event_recorded`, `data_asset.marked_deleted`, `data_asset.restored`;
- `data_asset.exported`, `data_asset.overdue_notified`;
- `data_field.created`, `data_field.updated`, `data_field.reordered`;
- `data_field.archived`, `data_field.unarchived`, `data_field.deleted`.

Automatic lifecycle events are recorded in `data_asset_events` only. They are not audited
per scan, because that would mean one audit row per scanned resource.

### 12. Data handling and leak matrix

| Threat | Control |
|--------|---------|
| Raw item key persisted, logged or returned | Keyed in `issues/scan.py` or `external_scan.py` before `ResourceScan` exists; `repr=False` on every raw holder; only 12-hex prefixes are returned |
| Item linking across assets or with value fingerprints | The per-asset key input and a separate domain label (D10) |
| Secret typed into metadata, a name, a locator, a note, a label or a query | `Redactor` reject (422, nothing stored, no echo, no log of the value) |
| Secret in a scan title or locator | Already redacted by the scan path |
| CSV leak or formula injection | Clean sources, a `Redactor` pass per cell, formula prefixing, audit |
| Overdue notification leak | Masked context, template `Redactor` pass, DLP gate |
| False deletion | D5, D6 and D11; tests cover partial sweeps, failed fetches, skipped refs and incomplete item reports |
| Event tampering | Insert-only repository, no route, PostgreSQL trigger (D23) |

The leak matrix is `tests/leak/flows_v14.py` and `test_leak_matrix_v14.py`. It uses the
real `build_container` and the v1.3 sinks (tables, logs, search documents, outbound bodies,
API bodies, SMTP messages):

1. Push `/scan` with samples in segments, the locator and the title, and samples as
   `item_keys`, then a reduced key list. Covers the asset, event and item rows and the
   list, detail, events, items and series responses.
2. Police 1.2 inventories (clean) and a result whose inventory locator holds a sample
   (422).
3. Field values, labels and option labels with samples (422, no echo, logs clean).
   Registration name, locator and `q` with samples (422).
4. A manual event note with a sample (422), then a clean note.
5. CSV export of the seeded register.
6. Overdue notification through a scheduler tick (email and Slack payloads).
7. A `data.read` principal's responses, including the issue link lookup.

### 13. Implementation plan shape

Plans follow `impl-plans/conventions.md`, including 7a. Each plan file stays under 400
lines and every Python file under 1000. There are 8 plans in one run.

- No dependency changes. `v14-p01` owns the root files and the web package files.
- `issues/service.py` (974 lines): p05 first moves `record_external` unchanged into the
  new `issues/external_scan.py` (a module function called by a thin
  `IssueService.record_external` delegate, so `IssueRecorder` is unchanged), then adds
  the hook.
- `wiring.py` (777 lines) gains only the call into a new `wiring_v14.py`, which builds the
  inventory services, binds the ports, registers the `data_overdue_check` handler and
  provider, and returns the routers.

| Wave | Plan | Owns (writePaths) | Depends on |
|------|------|-------------------|-----------|
| 1 | v14-p01-foundation | `src/telescreen/core/{vocabulary,permissions,config}.py`, `src/telescreen/ports/{inventory,collectors,findings,notifications}.py`, `src/telescreen/masking/fingerprint.py`, `src/telescreen/api/common/errors.py` (status mapping of the new codes), `src/telescreen/contracts/validator.py` (schema list), `src/telescreen/notifications/templates/{builtins,variables,synthetic}.py`, `src/telescreen/contracts/police/v1/**`, `src/telescreen_police_sdk/{models,masking}.py`, `examples/compose/.env.example`, `README.md` (v1.4 feature section, written from this design), root files and `src/web/{package.json,bun.lock}` (expected unchanged), `tests/core/v14/**`, `tests/ports/v14/**`, `tests/masking/v14/**`, `tests/contract/**`, `tests/police_sdk/**`, plus each existing permission, vocabulary, template-inventory and examples exact-count test its change breaks (exact paths listed after a grep at the base) | - |
| 1 | v14-p02-db | `db/schema/telescreen.nodetto.jsonl`, `db/schema/telescreen.{postgresql,sqlite}.sql`, `src/telescreen/db/models/**`, `src/telescreen/db/migrations/versions/0005_v1_4.py`, `tests/db/**` | - |
| 1 | v14-p03-ui-foundation | `src/web/src/api/**` (types, `dataApi` client, the v14 mock router, `fixtures/v14.ts`, `v14-shapes.json`, `inventory_mode` in the collector types and fixtures, fixture and mock tests), `src/web/src/components/**` (rail icon, nav, `CountTabs`, `ItemCountChart`), `src/web/src/routes.tsx`, `src/web/src/shell.test.tsx`, stub `src/web/src/pages/data/index.tsx`, `src/web/scripts/screenshots.ts`, `tests/leak/test_ui_fixtures.py` (only if the new fixture file needs it) | - |
| 2 | v14-p04-inventory-core | new `src/telescreen/inventory/{__init__,observer,items,lifecycle,reconcile,repository,queries,queries_series,export,assets}.py`, new `src/telescreen/api/routers/{data_common,data_assets,data_asset_detail}.py` (list, counts, export, lookup, detail, PATCH, events, manual event, mark deleted, restore, items, series), `tests/inventory/core/**`, `tests/api/v14_data_assets/**` | p01, p02 |
| 2 | v14-p05-scan-hooks | `src/telescreen/issues/{scan,service,ingest}.py`, new `src/telescreen/issues/external_scan.py`, `src/telescreen/api/routers/{ingest,collectors}.py`, `src/telescreen/police/results.py`, `src/telescreen/workflow/mapreduce/reduce.py`, `src/telescreen/collectors/{tasks,service,repository}.py`, `src/telescreen/collectors/google/adapter.py`, `tests/issues/v14_hooks/**`, `tests/collectors/v14/**`, `tests/collectors/core/test_core.py`, `tests/police/v14/**`, `tests/workflow/v14/**`, `tests/api/v14_ingest/**`, plus each existing test that asserts the exact collector response or body keys (exact paths listed after a grep at the base) | p01, p02 |
| 2 | v14-p06-ui-pages | `src/web/src/pages/data/**` (replacing the stub), `src/web/src/pages/settings/{index.tsx,index.test.tsx,DataFields.tsx,DataFields.test.tsx}`, `src/web/src/pages/issues/detail/IssueDetailSidebar.tsx` and its test, `src/web/src/pages/dashboard/{index.tsx,index.test.tsx}`, `src/web/src/pages/collectors/{index.tsx,index.test.tsx}` | p03 |
| 3 | v14-p07-fields-retention | new `src/telescreen/inventory/{fields,values,registration,retention}.py`, new `src/telescreen/issues/asset_owner.py`, new `src/telescreen/api/routers/{data_fields,data_asset_values}.py` (field routes, validate values, PUT values, register), `tests/inventory/fields/**`, `tests/inventory/retention/**`, `tests/issues/v14_asset_owner/**`, `tests/api/v14_data_fields/**` | p04 |
| 4 | v14-p08-wiring-verification | `src/telescreen/wiring.py`, new `src/telescreen/wiring_v14.py`, `src/telescreen/api/app.py`, `tests/e2e/{test_api_routes,test_route_permissions}.py`, new `tests/e2e/{test_wiring_v14,test_route_permissions_v14}.py`, new `tests/e2e/test_flow_v14_*.py`, new `tests/leak/{flows_v14,test_leak_matrix_v14}.py`, new `tests/contract/test_data_api_shapes.py`, `src/web/scripts/screenshots.ts` (strict-run fixes only), `impl-plans/README.md`, and the v1.4 status lines of this document and the summary docs | p04-p07 |

Notes:

- Shared tests: each plan owns every exact-count or inventory test that its change breaks,
  listed by exact path in its manifest. These are:
  - `tests/db/test_models.py` (p02);
  - the permission, vocabulary and template counts (p01);
  - the collector response shape tests (p05);
  - the page tests (p06);
  - `screenshots.ts` (p03 in wave 1, p08 in wave 4);
  - the route walk and `test_route_permissions_v14.py` (p08).

  `tests/api/v12_rbac/test_v12_rbac_router_permissions.py` filters to a fixed `MODULES`
  tuple, so the new `data_*` modules do not enter it. p05 adds no route to a registered
  module, so it stays read-only.
- p04 and p07 test their routers by building them directly. They stay unregistered until
  p08. Composition copies `APIRoute` objects, never `include_router`.
- p05 tests the hooks with a fake `InventoryObserver` and `InventoryReconciler`. p04 tests
  the observer with `ResourceObservation` values, so p04 and p05 share no file.
- p07 reuses p04's repository and lifecycle helpers and adds only new modules.
- Finalization (serial, after p08 is accepted):
  - move `impl-plans/active/v14-p*.md` to `impl-plans/completed/`, and the manifest to
    `impl-plans/completed/dispatch/`;
  - move the v1.4 rows of `impl-plans/README.md` to its completed section;
  - rerun the section 14 gates;
  - commit and push to `feat/data-inventory` only.

### 14. Verification

- **Python**: `uv run ruff format --check . && uv run ruff check . && uv run python
  tools/typelint.py && uv run ty check && uv run pytest && uv build` (`mise run ci`).
- **DDL**: `mise run ddl-check`.
- **UI**: `cd src/web && bun install --frozen-lockfile && bun run typecheck && bun run test
  && bun run build && bun run screenshots`.
- **E2E flows** (p08, offline, real `build_container`):
  - E1 discovery per mode, through push, a fake collector scan and a sweep;
  - E2 list items: keys 5 -> 3 complete (`items_removed = 2`), an incomplete report (no
    removal), count-only deltas, item prefixes in the API;
  - E3 deletion and restoration: push `removed`, issue `resource_deleted`, re-observation
    restores, a `SUCCEEDED` sweep absence deletes, a `PARTIAL` sweep does not, a
    full-listing absence, mode `off` -> `unknown` -> `tracking_resumed`;
  - E4 police 1.2 inventories through the SDK `PullWorker` in process;
  - E5 field schema, required, incomplete, a type change blocked by values, a 422 secret,
    `metadata_changed` events and audit;
  - E6 overdue: a `FakeClock` past the due date, a tick, one notification; a second tick
    sends none; a changed due date notifies again;
  - E7 masked CSV export.
- **Live** (owner only, after the run, on `examples/compose/dev-services.yml`): discovery,
  list-item removal, deletion, and the overdue notification through Mailpit and the Slack
  test channel. Skipped live tests are not listed as verification.

### 15. Open questions

- C5: unify the custom push resource identity
  (`design-docs/user-qa/pending-custom-push-resource-identity.md`). v1.4 does not depend on
  the answer.

### 16. Author self-check against the request

| Request item | Section |
|--------------|---------|
| Verify the requirements evidence and correct it | 1, requirements section 7 |
| R1-R9 | 4-11 |
| Backward-compatible, versioned ingest change | 6.3, 6.4, C3 |
| Raw values, keys and content never stored, logged, indexed, notified, exported or returned | D10, D14, 9.2, 12 |
| Read-only on sources; append-only events; no deletion from incomplete scans | D5, D6, D11, D23 |
| Nodetto first, one Alembic 0005 | 4 |
| Every route declares a permission | 3, 9, 13 (p08) |
| At most 8 plans, disjoint writePaths, shared tests owned | 13 |
| Run 2: live defects L1-L7 with regression tests, run-2 manifest | 17 |

### 17. Run 2: live fixes (2026-10-13)

The owner ran the live stack after run 1 (requirements section 8). Run 2 keeps D1-D24
and the run-1 plans p01-p07 (accepted dependencies, not redispatched). It records the
owner fixes and specifies the fixes for the live defects. No DDL, API route, permission,
response key or dependency changes.

#### 17.1 Owner fixes already in `3c5c1dd`

- **O1**: `DataOverdueProvider` now uses `data-overdue:<epoch minute>` as
  `engine_tasks.ref_id` (under `varchar(36)`; the ISO idempotency key is unchanged).
  `SqlTaskQueue.enqueue` rejects a `ref_id` over 36 or an idempotency key over 256
  characters, so SQLite tests catch this class.
- **O2**: the integrated E3 full-listing flow
  (`tests/e2e/test_flow_v14_inventory.py::test_e3_full_listing_deletes_absent_asset_only_when_authoritative`)
  resolves the p08 blocker. p08 runs in mode re-verify and follows its "Owner resolution"
  section.

#### 17.2 Defects, causes and fixes

| Id | Cause at `3c5c1dd` | Fix |
|----|--------------------|-----|
| L1 | `DataDetailSections.tsx` passes `event.occurred_at` to `RelativeTime`. The API returns `occurred_at: null` for every scan and system event (it is set only when a person supplies it), and `new Date(null)` is the epoch. The UI type declares `DataEvent.occurred_at: string`, so the type checker and the mock (which always set it) hid the null. The same mismatch exists for `DataAsset.last_seen_at` (the API model `AssetView.last_seen_at` is `datetime or None`; 9.1 says "string or null") | The UI types follow 9.1: `DataEvent.occurred_at: string \| null` and `DataAsset.last_seen_at: string \| null`. The timeline shows `occurred_at ?? recorded_at`. A null `last_seen_at` renders as "—" in the list and the detail |
| L2 | The observer and the reconciler write `collector:<config id>` as `actor_ref` (`inventory/observer.py:53-57`, `inventory/reconcile.py`). `DirectoryActorDirectory` resolves only `member:`, `team:`, `token:` and `system:` refs, so these become `{kind: "unknown", display_name: "Unknown"}`. Issue activity never hits this because it writes `system:scanner` | Read-time resolution (the stored refs are append-only and stay as they are; see 17.3) |
| L3 | `MetadataForm.tsx` renders the reference `<select value=...>` before the member and team options load (`createResource` on the directory). The value is applied while no option matches, and the options added later do not reselect it, so the control shows "Choose..." while the form state still holds `"member:alice"` | Each option of the reference select sets `selected` from the current value, so the selection follows options that arrive later. When the stored value matches no loaded option (an inactive member, a deleted team), a first option with that exact value and the label "Current value (not in directory)" keeps it selectable. Saving an unchanged form sends the stored value unchanged; the server already writes no `metadata_changed` event for an unchanged canonical value (`inventory/values.py`) |
| L4 | `ItemCountChart.tsx` returns early from the component body when `props.points` is empty. A Solid component body runs once, and the series resource is still loading at that moment, so the empty state stays after the points arrive | The chart is reactive (a `Show` over the points with derived coordinates). No points shows "No item count history". One point draws a flat line. Two or more draw the step line |
| L5 | The new markup puts adjacent text in inline elements with no separator and no styles: `.count-tabs` and `.count-badge`, the timeline row (avatar initials, name, label, time), the list name cell (`<strong>` then `<small>`), and the breadcrumb (two inline children of `.row-actions`). None of these classes has a CSS rule | Explicit separators in the DOM: a space between tab label and count badge; spaces between avatar, actor name, event label and time in a timeline row; the locator on its own line under the name (block `<small>` plus a space node); the breadcrumb `Data / DATA-n` with an `aria-hidden` "/" separator. CSS rules for these classes go in `components/ui/ui.css` (count tabs, item chart) and a new `pages/data/data.css` (timeline row, name cell, breadcrumb), using the existing tokens. Accessible names stay the same |
| L6 | `components/shell/nav.ts` `entriesFor` takes no permission input, so the Data nav has no "Field schema" entry. `SecondaryPanel.tsx` builds the Settings entries inline without it; only the settings tab strip in `pages/settings/index.tsx` has it | `entriesFor(area, can)` receives the permission check. The Data nav adds "Field schema" -> `/settings/data-fields` for `data_fields.admin`, as section 10.1 specifies. The Settings secondary nav adds the same entry under the same check. Other areas are unchanged |
| L7 | The list State column renders only the state tag and the Incomplete tag; `asset.overdue` only colors the due date | A `danger` "Overdue" tag after the state tag when `asset.overdue` is true |

#### 17.3 L2 actor resolution

- New module `telescreen.inventory.actors` with `InventoryActorDirectory`, an
  `ActorDirectory` implementation that wraps the bound directory and the
  `CollectorConfigReader` port (both already in `WiringV14Parts`):
  - `collector:<id>` resolves to kind `system` with the collector config's current
    `name` as `display_name`. When the config no longer exists it resolves to
    `Telescreen`. `ref` stays the stored ref;
  - every other ref is delegated unchanged and in one batch, so `system:inventory` stays
    `Telescreen`, and members, teams and tokens keep their v1.3 resolution;
  - each distinct collector id is read once per call. Length and order of the input are
    preserved, as the port requires.
- `wiring_v14.py` passes `InventoryActorDirectory(parts.actors, parts.collector_configs)`
  to `AssetQueries`. `AssetQueries` and its tests are unchanged, and both the events
  timeline and `DataAssetDetail.created_by` use the wrapper.
- The shared `DirectoryActorDirectory` and the issue activity are unchanged; no package
  outside `collectors` reads `collector_configs` directly.
- Exposure: every role and scope that holds `data.read` also holds `collectors.read`
  (`core/permissions.py` `READ_PERMISSIONS`), and `GET /api/collectors` already returns
  these names. No new data reaches a principal. Collector names are admin-set
  configuration, not scanned content; the leak matrix (`tests/leak/*v14*`) and the
  route permission tests stay unchanged and green.

#### 17.4 Mock fixtures and screenshots

The defects passed the run-1 mock because the fixtures never had the live shapes. The
fixtures (`src/web/src/api/mock/fixtures/v14.ts`) gain these shapes and keep the key sets
of `v14-shapes.json` and the 9.1 nullability:

- scan and system events with `occurred_at: null` (manual events keep a value);
- collector actors `{kind: "system", ref: "collector:col-01", display_name: <the col-01
  name from the operations fixture>}` and a system actor `{kind: "system", ref:
  "system:inventory", display_name: "Telescreen"}`;
- `reference` values as `member:<handle>` and `team:<slug>` of directory fixture
  entries (already present; asserted now);
- `data-asset-01` with a non-null `item_count` and an `item-series` of at least 3 points.

`fixtures.test.ts` asserts each of these states exists. The strict screenshots of
`/data` and `/data/data-asset-01` (light and dark, zero browser errors) then show the
Overdue badge, separated text, actor names, a recorded-at time, the preselected owner and
a drawn chart.

#### 17.5 Regression tests (each fails on `3c5c1dd`)

| Id | Test |
|----|------|
| L1 | vitest: a timeline event with `occurred_at: null` and a `recorded_at` two hours ago renders "2 hours ago", never a day count; a list row with `last_seen_at: null` renders "—" |
| L2 | pytest, offline: `InventoryActorDirectory` over SQLite with a real collector repository resolves `collector:<id>` to the config name with kind `system`, a missing config to `Telescreen`, `system:inventory` to `Telescreen`, and delegates a `member:` ref in order. An integrated test through `build_container` (new `tests/e2e/test_flow_v14_live_fixes.py`) pushes a scan for a configured collector and asserts that `GET /api/data/assets/{id}/events` shows the collector name, not "Unknown" |
| L3 | vitest: the directory response resolves after the first render; the owner select then shows the stored `member:<handle>` option as selected; submitting the form unchanged sends the same `value_json`; a stored value missing from the directory stays selected |
| L4 | vitest: `ItemCountChart` rendered with an empty points signal that later receives 3 points draws a path with 3 steps; the detail page with a 3-point mock series shows no "No item count history" |
| L5 | vitest: normalized `textContent` reads "All 3" for a count tab, "<name> restored" in a timeline row, "<name> <locator>" in a list name cell, and "Data / DATA-1" in the breadcrumb |
| L6 | vitest (`shell.test.tsx`): an admin sees "Field schema" in the Data and Settings secondary navs; a member sees it in neither |
| L7 | vitest: an overdue list row shows the "Overdue" tag in the State column; a row that is not overdue does not |

#### 17.6 Run-2 plans and manifest

- One plan, `v14-p09-live-fixes` (under 400 lines). The backend part (L2) is one new
  module, one line of wiring and its tests, so no backend/UI split is needed.
- `v14-p09-live-fixes` writePaths:
  - `src/telescreen/inventory/actors.py` (new), `src/telescreen/wiring_v14.py`,
    `tests/inventory/actors/**` (new), `tests/e2e/test_flow_v14_live_fixes.py` (new);
  - `src/web/src/pages/data/**` (including the new `data.css`),
    `src/web/src/components/{CountTabs,ItemCountChart}.tsx` and their existing tests
    `{CountTabs,ItemCountChart}.test.tsx`,
    `src/web/src/components/ui/ui.css`, `src/web/src/components/shell/{nav.ts,SecondaryPanel.tsx}`,
    `src/web/src/shell.test.tsx`;
  - `src/web/src/api/types.ts`, `src/web/src/api/mock/fixtures/v14.ts`,
    `src/web/src/api/mock/v14.ts`, `src/web/src/api/mock/fixtures.test.ts`,
    `src/web/src/api/mock/mock.test.ts` (only assertions broken by the fixture changes),
    `src/web/scripts/screenshots.ts`;
  - nothing else: at `3c5c1dd` only `api/types.ts`, the v14 mock files and
    `pages/data/**` read `occurred_at` or `last_seen_at` of these types (`api/client.ts`
    uses `occurred_at` only in a request body and is unchanged).
- `v14-p08-wiring-verification` (mode re-verify) run-2 writePaths: its plan file,
  `src/telescreen/wiring.py`, `src/telescreen/api/app.py`, its `tests/e2e` files
  (`test_api_routes.py`, `test_route_permissions.py`, `test_wiring_v14.py`,
  `test_route_permissions_v14.py`, `test_flow_v14_inventory.py`,
  `test_flow_v14_fields.py`), `tests/leak/{flows_v14,test_leak_matrix_v14}.py` and
  `tests/contract/test_data_api_shapes.py`. It gives up `wiring_v14.py` and
  `screenshots.ts` to p09. The design status lines and `impl-plans/README.md` move to the
  serial finalization. The two plans' writePaths are disjoint.
- Waves: wave A = p09, wave B = p08 re-verify. The writePaths are disjoint, but workers
  share one working directory, so a concurrent p08 would run its whole-tree gates (full
  pytest, leak matrix, strict screenshots) on p09's partial edits. Run serially, p08's
  final passing commands verify the integrated tree, including the p09 fixes (conventions
  7a). p01-p07 are `acceptedDependencies` in the run-2 manifest.
- Serial finalization after p08 is accepted:
  - `git mv impl-plans/active/v14-p*.md impl-plans/completed/` (p01-p09);
  - move `telescreen-v14-20261013-f1-dispatch.json` and the run-2 manifest to
    `impl-plans/completed/dispatch/`;
  - move the v1.4 rows of `impl-plans/README.md` to its completed section;
  - set the v1.4 status lines of this document, the requirements document and the
    summary docs (architecture, data model, API and UI, collectors and ingestion, police
    contract, masking, UI sentry) to implemented;
  - check the README v1.4 feature section against sections 9, 10 and 17;
  - rerun the section 14 gates on the final tree, then commit and push to
    `feat/data-inventory` only.

#### 17.7 Run 3: re-verify and finalize (2026-10-14, base `42864aa`)

- Run 2 accepted p09. The owner fixes O3 (`sql_queue.py` rewrap) and O4 (item-count chart
  height) are in requirements section 8.1. D1-D24 and sections 1-17.6 are unchanged.
- Manifest `telescreen-v14-20261014-f3`: one wave, `v14-p08-wiring-verification` in mode
  re-verify. p01-p07 and p09 are `acceptedDependencies`. No new plans.
- p08 code is complete. It changes code only if a section 14 gate fails on the combined
  tree. Run 2 stopped on a file that no plan owned (O3), so in run 3 p08 owns, for gate
  failures only, the v1.4 surface and the O3 file: wiring, `api/app.py`,
  `src/telescreen/inventory`, `src/telescreen/workflow/queue/sql_queue.py`,
  `src/telescreen_police_sdk`, `tests/{e2e,leak,contract,inventory,db}`, `db/schema`
  (export drift only; Nodetto first, as in section 4), `src/web/src/{pages/data,api,components}`,
  `src/web/src/shell.test.tsx` and `src/web/scripts/screenshots.ts`. The whole-tree globs
  would exceed the 512-entry fanout snapshot limit. A failure outside this list, or one
  that needs a dependency, lockfile, model or Alembic change, is a Reconciliation request
  fixed in the serial finalization. Existing Alembic revisions stay untouched. Nothing
  else runs concurrently.
- The serial finalization follows 17.6. It also moves the f2 and f3 manifests to
  `impl-plans/completed/dispatch/`. `design-docs/user-qa/pending-custom-push-resource-identity.md`
  stays pending (owner decision).

## References

See `design-docs/references/README.md`. Internal: `design-v1-4-data-inventory-requirements.md`,
`design-v1-3-sentry-parity.md` (section 15 plan conventions), `db/schema/README.md`.
