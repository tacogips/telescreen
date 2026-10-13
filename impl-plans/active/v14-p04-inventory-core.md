# V14-P04 Inventory Core: Observer, Diffs, Lifecycle, Reconciliation, Queries, Export, Asset Routes

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 2 (D2-D13, D15-D16, D20-D24), 5, 6.1, 6.2, 6.5, 9, 9.1, 9.2, 11, 12
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p04-inventory-core`
- planPath: `impl-plans/active/v14-p04-inventory-core.md`
- wave: 2
- dependsOn: `v14-p01-foundation`, `v14-p02-db`
- writePaths:
  - `impl-plans/active/v14-p04-inventory-core.md`
  - `src/telescreen/inventory/__init__.py`
  - `src/telescreen/inventory/repository.py`
  - `src/telescreen/inventory/lifecycle.py`
  - `src/telescreen/inventory/items.py`
  - `src/telescreen/inventory/observer.py`
  - `src/telescreen/inventory/reconcile.py`
  - `src/telescreen/inventory/queries.py`
  - `src/telescreen/inventory/queries_series.py` (optional split of the trend and series helpers)
  - `src/telescreen/inventory/export.py`
  - `src/telescreen/inventory/assets.py`
  - `src/telescreen/api/routers/data_common.py`
  - `src/telescreen/api/routers/data_assets.py`
  - `src/telescreen/api/routers/data_asset_detail.py`
  - `tests/inventory/core`
  - `tests/api/v14_data_assets`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan implements the new L3 package `telescreen.inventory` for everything except the
field schema, values, registration and retention, which belong to p07. It covers:

- the `InventoryObserver` and `InventoryReconciler` port implementations (p01 contracts);
- item and value diffs;
- lifecycle transitions;
- list, count, detail, event, item and series queries, including the computed
  `incomplete` and `overdue` flags;
- the masked CSV export;
- the asset routes of design 9, except register, `PUT` fields and the field routes.

Routers are tested by building them directly. p08 registers them.

Imitate:

- `issues/repository.py:next_issue_number` (counter `FOR UPDATE`, create on demand);
- `collectors/coverage.py` lines 84-117 (`begin_nested` insert, `IntegrityError`
  re-read);
- `issues/views.py:213-219` (`Redactor.has_hits` -> `TelescreenError(RAW_VALUE_DETECTED,
  static detail)`);
- `issues/ownership.py:199-213` (audit append with `json_object` details);
- `api/routers/alerts.py` (`AlertsRouterDeps`, `build_alerts_router`,
  `require_permission`, hand-built `JsonValue` responses with `dump_json`);
- `api/common/pagination.py` (opaque cursor);
- `api/routers/issue_counts.py` (counts route).

## Non-goals

- No field CRUD, value writes, registration or overdue evaluation (p07).
- No hook calls in issues, collectors, police or mapreduce (p05). No wiring (p08).
- No Meilisearch indexing (D21). No edits to files of other packages.

## Contracts this plan pins (p07 and p08 import them)

- `inventory/repository.py`:
  - `DataEventDraft`, a frozen dataclass: `asset_id`, `kind: DataEventKind`,
    `recorded_at`, `actor_ref`; then, all defaulting to None: `occurred_at`,
    `scan_job_id`, `items_added`, `items_removed`, `item_count_after`, `values_added`,
    `values_removed`, `value_count_after`, `field_key`, `old_value`, `new_value`, `note`;
  - `InventoryRepository` (stateless dataclass):
    - `next_asset_number(session) -> int` (counter `data_asset_number`);
    - `get(session, asset_id, lock: bool = False)` and `get_by_resource_key(session,
      resource_key, lock: bool = False)`, each returning `DataAssetRow | None`;
    - `insert_asset(session, row) -> DataAssetRow` (handles the race by re-reading);
    - `append_event(session, draft) -> DataAssetEventRow` (insert only; there is no
      update or delete method anywhere).
- `inventory/queries.py`:
  - `AssetFilters` (frozen): `tab`, `q`, `source`, `kind`, `state`, `origin`, `owner`,
    `collector_config_id`, `fields: tuple[FieldFilter, ...]`, `sort`, `limit`, `cursor`;
  - `FieldFilter(key, value)`;
  - `AssetQueries(issues: ResourceIssueReader, actors: ActorDirectory)` with:
    - `list(session, filters, today) -> AssetPage`;
    - `counts(session, filters, today) -> CountsView`;
    - `detail(session, asset_id, today) -> AssetDetailView | None`;
    - `by_resource_key(session, key, today) -> AssetView | None`;
    - `events(session, asset_id, limit, cursor) -> EventPage`;
    - `items(session, asset_id, kind, state, limit, cursor) -> ItemPage`;
    - `item_series(session, asset_id, days, today) -> tuple[SeriesPoint, ...]`.

  Views are frozen dataclasses mirroring design 9.1.
- `api/routers/data_common.py`: `asset_json(view) -> JsonValue`,
  `asset_detail_json(view) -> JsonValue`, `event_json(view) -> JsonValue`,
  `item_json(view) -> JsonValue`, plus `parse_filters(request query) -> AssetFilters`,
  which raises 422 on an invalid value and on a `q` `Redactor` hit, with no echo.

## Deliverables

1. `repository.py`, as pinned above.
2. `items.py`:
   - `apply_fingerprints(session, asset, kind: DataItemKind, fingerprints:
     frozenset[str], key_version: int, complete: bool, at) -> ItemDelta(added, removed,
     open_count)`;
   - `apply_count(asset, count, complete) -> CountDelta | None`;
   - `apply_values(session, asset, values, complete, at) -> ItemDelta | None`, which
     implements the D12 re-baseline: when every open value row has a version other than
     the single version `v` of a complete observation, it closes them, inserts the new
     rows and returns None (no event);
   - `close_all(session, asset, at) -> (items_removed, values_removed)`.

   Rules (design 6.1, 6.2, D11): additions on every call; removals only when `complete`.
   A previously removed row is reopened (`removed_at = None`, `first_seen_at` kept), never
   re-inserted, because the unique constraint forbids it.
3. `lifecycle.py`:
   - `delete_asset(...)` (D13: close all, set `item_count` to 0 if not None, one
     `deleted` event with the counts);
   - `restore_asset`, `stop_tracking_asset` (`unknown`, `tracking_stopped`) and
     `resume_asset` (`present`, `tracking_resumed`).

   Each writes exactly one event through `append_event`.
4. `observer.py`: `InventoryObserverImpl(configs: CollectorConfigReader, repository:
   InventoryRepository)` implements the design 5.2 algorithm exactly:
   - mode `off`: return;
   - load the asset with `lock=True`;
   - removed: delete, or no-op;
   - new asset: `sensitive_only` with no values -> return; otherwise insert, apply items
     and values as additions, and write `discovered` with `item_count_after` and
     `value_count_after`;
   - existing asset: restore or resume, update `last_seen_at` to the max, update
     `sweep_covered` and the name, apply values then items, and write at most one
     `values_changed` and one `items_changed` event.

   Further rules:
   - Name (D24): update only from a non-empty `display_title`. On create with an empty
     title, use `display_locator`. Truncate to 512.
   - Default kind (no hint): `gdrive`, `gdocs` and `github` -> `file`; `gsheets` ->
     `table`; `slack` with `/file/` in the locator -> `file`, other `slack` -> `object`;
     everything else -> `other`.
   - The actor is `collector:<config id>`, or `system:inventory` when there is no config.
     `scan_job_id` comes from the observation.
   - A configless observation, or one whose config is missing, uses `sensitive_only`.
5. `reconcile.py`: `InventoryReconcilerImpl(configs, repository, clock)`:
   - `reconcile_sweep`: present, `sweep_covered`, `last_seen_at < seen_before`, this
     config, mode not `off` -> delete each and return the count;
   - `reconcile_listing`: the same, but with "`resource_key` not in `listed_keys`";
   - `stop_tracking`: present discovered assets of the config -> `unknown`, each with a
     `tracking_stopped` event carrying the given actor.
6. `queries.py`:
   - Filters and keyset paging. `tab` maps: `present`, `deleted` and `all` to state;
     `incomplete` and `overdue` to the computed SQL predicates.
   - `incomplete`: EXISTS a non-archived required field with NOT EXISTS a value row.
   - `overdue`: state != `deleted` AND EXISTS a value for the non-archived
     `deletion_due` role field with `value_date < today.isoformat()`.
   - `owner`: the owner-role value equals the ref.
   - `field=<key>:<value>`: equality on `value_json` canonical text; for `multi_select`,
     containment over the parsed array (filter in SQL by `LIKE`, then verify in Python
     on the page).
   - Sorts: `last_seen` (default; nulls last), `first_seen`, `name`, `item_count`,
     `deletion_due`, `short_id`, each optionally prefixed with `-`.
   - Page enrichment:
     - `open_issue_count` and `open_issues` come from one
       `ResourceIssueReader.open_issues_for_resources` call for the page's keys;
     - the owner display: collect the page's distinct owner-role values (`member:<handle>`
       or `team:<slug>`) and call `ActorDirectory.display(session, refs)` once per page
       (`ports/directory.py:84-97`; it resolves member and team refs and never raises,
       and unknown or stale refs come back as kind `unknown`). `owner = {ref,
       display_name}` from that result, with the stored ref kept as `ref`. The export uses
       the same display;
     - `item_trend` is 14 daily last-known counts ending today, from the events'
       `item_count_after` (forward-filled from the last event before the window), and
       `[]` when `item_count` is None.
   - `detail` adds `sweep_covered`, `created_by` (an actor display), `value_count` (open
     value rows), `fields` (non-archived, by position, with `value_json` or None),
     `missing_required` and `open_issues`.
   - `item_series`: points from events with non-null `item_count_after` within `days`,
     plus the last point before the window.
7. `export.py`: `AssetExporter.export(session, filters, today) -> CsvExport(text: str,
   row_count: int, truncated: bool)` implements design 9.2:
   - the columns, then `field:<key>`;
   - `Redactor.redact` on each cell;
   - a leading `'` for cells that start with `=`, `+`, `-`, `@`, `\t` or `\r`;
   - at most 10000 rows.

   Write RFC 4180 quoting by hand (`_csv_cell`). Do not use `csv.writer`: its untyped
   writer object fights strict typing.
8. `assets.py`: `AssetService(repository, audit: AuditLog, redactor, clock)`:
   - `patch(session, asset_id, name, kind, actor_ref)`: a name change on a discovered
     asset is 422 `validation_error`; `name` is 1-200 characters and `Redactor`-checked;
     audit `data_asset.updated` (field names);
   - `record_event(...)` (design 6.5): `occurred_at` is at most `now + 5 min`; `note` is
     1-1000 characters and `Redactor`-checked; counts are at least 0; `item_count` is
     updated only for registered assets; audit `data_asset.event_recorded`;
   - `mark_deleted(...)`: 409 `conflict` when already deleted; optional `occurred_at`
     and note; audit `data_asset.marked_deleted`;
   - `restore(...)`: 409 `conflict` unless deleted; audit `data_asset.restored`.

   Audit details hold ids, enums, counts and field names only. `actor_kind` follows
   `issues/ownership.py`.
9. Routers, each route declaring exactly one permission:
   - `data_assets.py`: `DataAssetsRouterDeps(ctx: ApiContext, queries: AssetQueries,
     exporter: AssetExporter, audit: AuditLog, redactor: Redactor)` and
     `build_data_assets_router(deps)`, with these routes, all `DATA_READ`:
     - `GET /api/data/assets`;
     - `GET /api/data/counts`;
     - `GET /api/data/export`: `text/csv`, `Content-Disposition`,
       `X-Telescreen-Truncated`, audit `data_asset.exported`;
     - `GET /api/data/resource-assets/{resource_key}`: 422 unless 64 lowercase hex;
       `{asset: null}` when missing.
   - `data_asset_detail.py`: `DataAssetDetailRouterDeps(ctx, queries, assets)` and
     `build_data_asset_detail_router(deps)`, with these routes:
     - `DATA_READ`: `GET /api/data/assets/{asset_id}`,
       `GET /api/data/assets/{asset_id}/events`,
       `GET /api/data/assets/{asset_id}/items`,
       `GET /api/data/assets/{asset_id}/item-series`;
     - `DATA_WRITE`: `PATCH /api/data/assets/{asset_id}`,
       `POST /api/data/assets/{asset_id}/events`,
       `POST /api/data/assets/{asset_id}/mark-deleted`,
       `POST /api/data/assets/{asset_id}/restore`.

     Unknown ids return 404 `not_found`.

   Bodies are `pydantic.dataclasses` with `repr=False` on free text, as in
   `api/routers/ingest.py`. Validation errors go through the existing non-echoing
   handler.

## Pitfalls

- Never edit or delete `data_asset_events` rows.
- A removed observation for an unknown or already-deleted asset is a no-op: do not create
  an asset to delete it.
- An incomplete observation must never close items or values, and must never change
  `item_count` in count mode.
- The value event is skipped on re-baseline. Do not count closed old-version rows as
  `values_removed`.
- `last_seen_at = max(...)`: observations can arrive out of order.
- `today` comes from `clock.now().date()` (UTC); pass it in from the router or the caller.
  Never call `datetime.now()`.
- No raw item key ever reaches this package; only fingerprints arrive.
- Never call `RecipientResolver.assignee_of` in queries, detail or export.
  - It raises `TelescreenError(RECIPIENT_UNKNOWN)` for a deactivated member or a deleted
    team (`directory/resolver.py:83-97`).
  - That code is unmapped in `http_status_for`, so it would turn every list, detail and
    export into a 500 once an owner goes stale.
  - Read paths use `ActorDirectory.display` only. Do not add an error mapping either.
- Keep each file under 1000 lines. `queries.py` is the biggest risk; split
  `queries_series.py` out if it nears 800 (inside this plan, as a new file; record it in
  the Progress Log).

## Tests (`tests/inventory/core`, `tests/api/v14_data_assets`; SQLite sessions from `tests.support.db`, `FakeClock`, fake ports)

Observer:

- `sensitive_only`, a clean observation -> no asset;
- an observation with values -> asset `DATA-1`, `present`, one `discovered` event with
  `value_count_after`;
- mode `all`, a clean observation -> asset created;
- mode `off` -> nothing;
- keys `a`..`e` complete -> 5 open items; then `a`,`b`,`c` complete -> `items_changed`
  `items_removed = 2`, `item_count_after = 3`; then `a` incomplete -> no removal and no
  event; then `f` incomplete -> `items_added = 1` with `items_removed = 0`;
- count-only: 10 -> 7 (complete) gives `items_removed = 3`; an incomplete 2 is ignored;
  the first count is a baseline event;
- values: 5 fingerprints -> 2 complete gives `values_removed = 3`; a version change with a
  disjoint set gives no event, and the open rows hold the new version;
- removed -> `deleted` (`items_removed`, `item_count_after = 0`); then seen again ->
  `restored` and `present`;
- a removed observation of an unknown asset -> no asset;
- an empty title on a failed-fetch observation keeps the name; creating with an empty
  title uses the locator;
- an observation older than `last_seen_at` does not move `last_seen_at` back.

Reconciler:

- sweep: a covered asset seen before the watermark -> deleted; seen after -> kept; an
  uncovered asset is kept; another config's asset is kept; mode `off` -> 0;
- listing: an unlisted key is deleted, a listed key is kept;
- `stop_tracking` -> `unknown` with events; a later observation -> `tracking_resumed`.

Queries:

- each tab's predicate, including incomplete (required field without a value) and overdue
  (the due date before today, and not deleted);
- owner and field filters; sorts; keyset paging is stable across pages;
- `item_trend` has length 14 and is forward-filled;
- stale owners:
  - an owner value `member:<handle>` of a deactivated member gives 200 from list, detail
    and export, with `owner.ref` equal to the stored ref and the `ActorDirectory` display
    name;
  - an owner value `team:<slug>` of a deleted team gives 200 everywhere, with
    `owner.ref` kept and the display name "Unknown";
  - the fake `RecipientResolver`, if present in the test world, is never called;
- the detail fields order and `missing_required`.

Export:

- a header plus rows;
- a cell `=cmd` becomes `'=cmd`;
- an embedded quote and comma are quoted;
- a truncated export at 10000 sets the flag.

API:

- every route with the permission (viewer read passes, viewer write is 403);
- `q` containing a `tests.support.samples` secret -> 422, and the body does not contain
  it;
- lookup returns null and found cases; a bad key is 422;
- `PATCH` of a discovered name is 422;
- `mark-deleted` twice gives 409; `restore` of a present asset gives 409;
- a manual event with a future `occurred_at` is 422 and with a secret note is 422 (no
  echo);
- a registered asset's count changes with a manual event, a discovered one's does not;
- each write has its audit record;
- response key sets equal design 9.1. Assert them against literal key lists; p08
  cross-checks `v14-shapes.json`.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`. `<src>` is
`src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py`.
`<tests>` is `tests/inventory/core tests/api/v14_data_assets`.

```bash
uv run --frozen ruff format --check --no-cache <src> <tests>
uv run --frozen ruff check --no-cache <src> <tests>
uv run --frozen ty check <src> <tests>
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p04-typelint.log 2>&1; grep -E '^(src/telescreen/inventory|src/telescreen/api/routers/data_|tests/inventory/core|tests/api/v14_data_assets)' /tmp/telescreen-v14-p04-typelint.log
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-v14-p04-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p04-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0 and the typelint grep is empty. The full suite is
informational.

## Completion Criteria

- [ ] Every deliverable is implemented with the pinned contracts unchanged.
- [ ] Every test case above exists and passes.
- [ ] No file exceeds 1000 lines (`wc -l` recorded).
- [ ] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

(none yet)
