# V14-P04 Inventory Core: Observer, Diffs, Lifecycle, Reconciliation, Queries, Export, Asset Routes

**Status**: Completed
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p04-typelint.log 2>&1; ! grep -E "^($PWD/)?(src/telescreen/inventory|src/telescreen/api/routers/data_|tests/inventory/core|tests/api/v14_data_assets)" /tmp/telescreen-v14-p04-typelint.log
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-v14-p04-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p04-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: the scoped checks exit 0 and the typelint grep is empty (typelint prints absolute paths). The full suite is
informational.

## Completion Criteria

- [x] Every deliverable is implemented with the pinned contracts unchanged.
- [x] Every test case above exists and passes.
- [x] No file exceeds 1000 lines (`wc -l` recorded).
- [x] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

### Session: 2026-10-13 20:40 JST
**Tasks Completed**: Added the repository, item/value diff, lifecycle, observer, reconciler, query, export, asset service, common serializers/parser, and 12 standalone asset routes. Added offline observer, value rebaseline, stale-owner/export, route-permission, query-parser, count and CSV safety tests (12 focused tests pass).
**Tasks In Progress**: The source deliverables are present, but the full listed test matrix is not yet covered. Missing focused cases include reconciler sweep/listing/stop-tracking, full tab/filter/sort/keyset coverage, item series/trend, manual event and mark/restore API audit/error cases, export truncation at 10000, and stale deactivated-member route coverage. p05 hooks and p08 wiring remain downstream plan ownership.
**Blockers**: None. The assigned predecessors `v14-p01-foundation` and `v14-p02-db` are admitted by dispatch `acceptedPlanIds`.
**Reconciliation requests**: None.
**Hashes**: Plan before=8f2a1736ca4099e51351f59e618e25e81a859c59ee7da25742b5e5981eaafc41; post-edit SHA-256 is recorded in `tmp/telescreen-v14-20261013-f1/v14-p04/final-hashes.txt` to avoid a self-referential in-file hash. New source files had boundary state absent; after hashes: `src/telescreen/inventory/__init__.py`=26e326a0d573d2fdf8a9ebef9ab34c5770a40c80ec33ecf3b9b9ed425b51af70; `assets.py`=25e5873407b44af56abcb60dd5958f590b65b5f54dda566732aa6983c21daee4; `export.py`=2acc6caf5c0e83dcb9cd67f99aac8e72618ddda6ee1b4f1fb7e08245dc4b1d7f; `items.py`=0a3dd7de08998d12e1f725aa636cb1f1bbd4a29e4f7d38def98211825f3884c3; `lifecycle.py`=74cf3c66246b321a7d84f747ad024739a87233c51ceb586002395cda1866761f; `observer.py`=eb5232848bb18092ae46afa8d15b5ff090b595316bfb2967456a43a927cd3a5b; `queries.py`=77fb8586e94932466aead7725639e2b8eba05013f4f451c67a17e8b61d4816d1; `reconcile.py`=2e790252788a7a3d3c0819ce0470732467ca42b8da09941ed1388e0b018e70ba; `repository.py`=92e0945d0316d261c98fcec29bce6a93f8da814082a637a73ca4b5151072e59c; `data_common.py`=0fa0e67b581ab93dbb1d6883549a20566d67660aa509c0dad5c2d147fc189c3b; `data_assets.py`=3dc4a415db85e263f808b59a4fb57c35ed1ce08af36a65de7f537cd4dd22fcdb; `data_asset_detail.py`=706f1591f3730e7c7200d1c259b70b10f5ac544a75fbc75a54c742aabc361dfb; `tests/inventory/core/test_items.py`=a6fc39a30e5761ea08d632609546515b96781929956e3e933eb1a91fa927599a; `test_observer.py`=0c7154e675980ec59d74617c4805d5990cc94fd697c38058c6ecccd40034518b; `test_queries.py`=197d31c5a160d3734a44ddafd9af5c28e18cc850e90c51cb16f10aa2acf4ecf1; `tests/api/v14_data_assets/test_data_common.py`=960d8fb9ed4e771d3816fc80b98df634c8c080310cb3be88804d97ee2dee6926; `test_routes.py`=478ce49a9da2adef1fc081b5735f890445548a04cff236da806531cff3ca3604.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-ruff.log`).
- `uv run --frozen ty check src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-ty.log`).
- `uv run --frozen python tools/typelint.py` plus empty p04-path grep -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-typelint.log`; filtered log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-typelint-filtered.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/inventory/core tests/api/v14_data_assets` -> exit 0, 12 passed, 0 failed (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-pytest.log`).
- `wc -l src/telescreen/inventory/*.py src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core/*.py tests/api/v14_data_assets/*.py` plus `<1000` check -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-wc.log`).
- `grep -rn assignee_of src/telescreen/inventory/queries.py src/telescreen/inventory/export.py src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py` -> no matches, exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/final-assignee-of.log`).
**Notes**: Earlier test attempts and repairs remain in `tmp/telescreen-v14-20261013-f1/v14-p04/logs/pytest-final2.log` and `pytest-route-test.log`; their failures were resolved by UTC timestamp normalization and the corrected FastAPI route import. The full repository suite was not run during this fanout wave, as this plan marks it informational while sibling plans are changing.

### Continuation: 2026-10-13
**Tasks Completed**: Completed the assigned observer and reconciler test matrix; added query tab/filter/sort/keyset, computed incomplete/overdue, detail, stale member/team owner, item trend/series coverage; added 10,000-row CSV truncation and API RBAC, status, non-echo, audit, lookup, and response-shape checks. Fixed the required-field EXISTS correlation, SQLite watermark timezone comparison, and datetime keyset cursor comparison exposed by those tests. p05 hooks and p08 wiring remain downstream.
**Tasks In Progress**: None for p04.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: Plan before=82756c1d76e008ae8c6105900655737307356569713aff35bf4f4059efd2c9f2; final plan SHA-256 is recorded after this edit in `tmp/telescreen-v14-20261013-f1/v14-p04/final-hashes.txt`. Existing source before/after: `src/telescreen/inventory/queries.py` 77fb8586e94932466aead7725639e2b8eba05013f4f451c67a17e8b61d4816d1 -> d2c7eafb1971f299b4fb748b3506a0e7e649555de3945e55e53b4ad37523903b; `src/telescreen/inventory/reconcile.py` 2e790252788a7a3d3c0819ce0470732467ca42b8da09941ed1388e0b018e70ba -> 253568be53ea52bd8ffb79316a8ef80d6f701f700c56d0a0218d9e51acf66033; `tests/inventory/core/test_observer.py` 0c7154e675980ec59d74617c4805d5990cc94fd697c38058c6ecccd40034518b -> b3543732fe227fd901e36335c6ada8ad574d83da44a0f6acb6524472957843ed; `tests/api/v14_data_assets/test_routes.py` 478ce49a9da2adef1fc081b5735f890445548a04cff236da806531cff3ca3604 -> 89ebcd1afe676725046e3585970d4befa0261bf90ab4bed21dfe5c6853e0fc28. New test files were absent at the node boundary; after hashes: `tests/inventory/core/test_reconcile.py`=29600a403071d8ddb4187161bc47c2fd16bfd62735f284382164fee51c797236; `test_query_contract.py`=f98d871d5ee39276fa41892c97ec5c03c23a04a84dc485f5881204f3107d822d; `test_export.py`=c496e9506151298777251b8db42956586d85156d8eda70c843623f9d4413e3e3; `test_asset_service.py`=34db996a14e9ed396726a9aa8f73c08444d2c1761bdd97f0bef761d2d0cb998e.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-format4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-ruff4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/inventory src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core tests/api/v14_data_assets` -> exit 0 (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-ty4.log`).
- `export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-typelint4.log 2>&1; ! grep -E '^(src/telescreen/inventory|src/telescreen/api/routers/data_|tests/inventory/core|tests/api/v14_data_assets)' tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-typelint4.log > tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-typelint-filtered4.log` -> exit 0; filtered diagnostics empty.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/inventory/core tests/api/v14_data_assets` -> exit 0, 22 passed, 0 failed (log: `tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-pytest4.log`).
- `wc -l src/telescreen/inventory/*.py src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py tests/inventory/core/*.py tests/api/v14_data_assets/*.py > tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-wc4.log; ! awk '$2 != "total" && $1 >= 1000' tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-wc4.log | grep -q .` -> exit 0; no selected file reached 1000 lines.
- `! grep -rn assignee_of src/telescreen/inventory/queries.py src/telescreen/inventory/export.py src/telescreen/api/routers/data_common.py src/telescreen/api/routers/data_assets.py src/telescreen/api/routers/data_asset_detail.py > tmp/telescreen-v14-20261013-f1/v14-p04/logs/step6-final-assignee-of4.log` -> exit 0; no matches.

### Adversarial repair: 2026-10-13 (comm-000807)
**Tasks Completed**: P04-ADV-1 uses timezone-aware cursor binds, naive-cursor normalization, and asyncpg compilation coverage; P04-ADV-2 uses binary-search fingerprint indexes and SQL counts; P04-ADV-3 adds canonical typed filters, escaped multi-select matching, and JSON-safe owner matching. Added 3 tests; p04 focused suite is 25 passed.
**Review Decision**: All assigned findings corrected; independent Opus re-review is pending. No acceptance is claimed here.
**Hashes**: Pre-edit hashes and per-edit intents are in `tmp/telescreen-v14-20261013-f1/v14-p04/intents/067-076-*.md`; final source and plan SHA-256 values are in `tmp/telescreen-v14-20261013-f1/v14-p04/logs/review4-final-hashes.log`.
**Verification**: Review3 focused pytest -> exit 0, 25 passed (`logs/review3-focused-pytest.log`); Ruff format/check, ty, typelint -> exit 0 (`logs/review3-format.log`, `review3-ruff.log`, `review3-ty.log`, `review3-typelint.log`; filtered diagnostics empty). File-size and no-assignee_of checks -> exit 0 (`review3-wc.log`, `review3-assignee-of.log`). `PYTHONPATH=. uv run --frozen python tmp/telescreen-v14-20261013-f1/v14-p04/scripts/review1_item_perf.py` -> exit 0; second passes at 2.5k/5k/10k=0.0106/0.0343/0.0603s (`logs/review5-fingerprint-timing.log`).
**Notes**: `items.py` has no per-fingerprint row scan; the only remaining `next()` selects the sole key version. Downstream p05 hooks and p08 wiring remain separate ownership.

### Serial reconciliation: 2026-10-13 (P04-BE-1, reconcile attempt-3)
**Tasks Completed**: Annotated the 13 TL003 locals in `tests/inventory/core/test_query_contract.py` (additive only; no assertion change). The Verification typelint grep now accepts typelint's absolute paths and must find nothing.
**Hashes**: test_query_contract.py before=a1a1c284b6b188dbec1af1eb8c5a2ecefc3731797c7dec922ab4a716cc9344ab after=b46a48b4ac5f49527d129e63702ce41312bead6d7f3dfb82f5b33cb3d7194fad; plan before=9bef1e414dd5dda4c1c0707e58ae4f6f4739d4af2fa6ca8956a152b7adc3040c (after in `tmp/telescreen-v14-20261013-f1/reconcile/attempt-3/post-sha256.txt`).
**Verification**: whole-tree typelint exit 0 (578 files clean); p04 ruff format/check, ty exit 0; focused pytest exit 0, 25 passed (logs under `tmp/telescreen-v14-20261013-f1/reconcile/attempt-3/`).
