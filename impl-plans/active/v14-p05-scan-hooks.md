# V14-P05 Scan Hooks: Observer Call, Reconciliation Calls, Ingest 1.1, Police 1.2, Inventory Mode, Sheets Count

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 5.1, 5.4, 6.3, 6.4, 13; design-docs/specs/design-collectors-and-ingestion.md "v1.4 inventory hooks"
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p05-scan-hooks`
- planPath: `impl-plans/active/v14-p05-scan-hooks.md`
- wave: 2
- dependsOn: `v14-p01-foundation`, `v14-p02-db`
- writePaths:
  - `impl-plans/active/v14-p05-scan-hooks.md`
  - `src/telescreen/issues/scan.py`
  - `src/telescreen/issues/service.py`
  - `src/telescreen/issues/external_scan.py`
  - `src/telescreen/issues/ingest.py`
  - `src/telescreen/api/routers/ingest.py`
  - `src/telescreen/api/routers/collectors.py`
  - `src/telescreen/police/results.py`
  - `src/telescreen/workflow/mapreduce/reduce.py`
  - `src/telescreen/collectors/tasks.py`
  - `src/telescreen/collectors/service.py`
  - `src/telescreen/collectors/repository.py`
  - `src/telescreen/collectors/google/adapter.py`
  - `tests/issues/v14_hooks`
  - `tests/collectors/v14`
  - `tests/collectors/core/test_core.py`
  - `tests/police/v14`
  - `tests/workflow/v14`
  - `tests/api/v14_ingest`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan connects the existing scan paths to the inventory ports from p01, without
changing any issue, collector, sweep or police behavior. Every hook calls a Protocol
field whose default is the p01 no-op. p04 implements the real observer and reconciler,
and p08 binds them in wiring. So this plan is tested with fakes and shares no file with
p04.

Imitate:

- `issues/service.py:IssueService.findings_observer` (a defaulted port field set after
  construction);
- `police/results.py:_resource_exposures` (parse an optional envelope member, drop
  locators that are not allowed, and count the drops);
- `api/routers/ingest.py:ResourceBody` (`repr=False` on raw fields);
- `issues/ingest.py:IngestService.findings_push`.

## Non-goals

- Do not change issue grouping, resolution, `resource_key` derivation (C5 stays as is),
  sweep status rules, police result validation or response shapes beyond the additive
  members listed below.
- No inventory logic (p04). No wiring or router registration (p08). No UI.

## Invariants

- All existing tests under `tests/issues`, `tests/collectors`, `tests/police`,
  `tests/workflow` and `tests/api` still pass unchanged, except `tests/collectors/core/test_core.py`,
  which may be updated only to supply or assert `inventory_mode`.
- The hook runs inside the caller's transaction, after the issue processing, for every
  recorded scan: complete, incomplete, skipped, removed and external.
- Raw item keys exist only inside `ResourceContent.items` (`repr=False`) and the request
  body. They are keyed in `issues/scan.py` before `ResourceScan` is built, and never
  logged.
- `issues/service.py` ends below 1000 lines (`wc -l` recorded).

## Deliverables

1. Split first, mechanically and with unchanged behavior: move the body of
   `IssueService.record_external` into a new module function
   `issues/external_scan.py:record_external_scan(service: IssueService, session:
   AsyncSession, scan: ExternalScan) -> RecordOutcome`. `IssueService.record_external`
   becomes a one-line delegate, so the `IssueRecorder` Protocol is unchanged. Run
   `tests/issues` and confirm it passes before going on.
2. `IssueService` gains `inventory: InventoryObserver = field(default_factory=NoopInventoryObserver)`
   as the last field. At the end of `record_scan`, after the `last_checked_at` and
   `sweep_covered` loop and before returning, call `await self.inventory.observe(session,
   ResourceObservation(...))`:
   - the fields come from the `ResourceScan`;
   - `values` are the distinct `(finding.fingerprint, finding.fingerprint_key_version)`
     pairs, sorted for determinism;
   - `items` is `scan.items`, and `kind_hint` is `scan.asset_kind`.
3. `issues/scan.py:_resource_scan`: pass `asset_kind=content.asset_kind`. When
   `content.items` is set:
   - keys -> `ItemObservation(fingerprints=tuple(sorted({fingerprinter.item_key(resource_key,
     k) for k in keys})), count=None)`;
   - count -> `ItemObservation(None, count)`.

   Do this on the too-large path as well. That path is incomplete, so p04 applies only
   additions.
4. `issues/external_scan.py`: for each locator, a `LocatorInventory` whose `locator`
   equals the local locator gives:
   - `items`: fingerprints re-keyed with `item_key(resource_key, external_fp)`, or the
     count;
   - `asset_kind`: the inventory's `kind`.
5. Push ingest:
   - `api/routers/ingest.py` `ResourceBody` gains `removed: bool = False`, `item_keys:
     list[str] | None = field(default=None, repr=False)`, `item_count: int | None = None`
     and `kind: DataAssetKind | None = None`. Validation gives 422 `validation_error`,
     with a static detail and no echo, when:
     - `removed` is true and segments are non-empty, or `complete` is false;
     - `item_keys` and `item_count` are both set;
     - there are more than 100000 keys, or a key is empty or longer than 1024 characters;
     - `item_count` is negative or above 2^53-1.

     Build `ResourceContent` with keywords `removed`, `items=ItemReport(...)` and
     `asset_kind`.
   - `FindingsBody` gains `item_fingerprints: list[str] | None` (each matching
     `^[0-9a-f]{64}$`, at most 10000), `item_count` and `kind`, with the same
     exclusivity. They are passed as one `LocatorInventory` for `body.locator`.
   - `issues/ingest.py`: `scan_push` keeps these fields when it rebuilds the normalized
     `ResourceContent`. `findings_push(..., inventory: LocatorInventory | None = None)`
     puts it into `ExternalScan(inventories=...)`.
   - Response bodies are unchanged.
6. `police/results.py`: `_resource_inventories(value, allowed_locators) ->
   tuple[tuple[LocatorInventory, ...], int]`, with the same allowed-locator rule as
   exposures. It applies only to `detect` and `scan_shard`, and is passed into
   `ExternalScan(..., inventories=...)`. Add `inventories_dropped` to the existing
   result-accepted audit details, next to the exposure drop count. Schema validation
   (p01) already guarantees the shapes, and the existing `Redactor` scan of every string
   field also covers inventory locators.
7. `workflow/mapreduce/reduce.py`: `MapReduceHandler.inventory: InventoryReconciler =
   field(default_factory=NoopInventoryReconciler)`. Right after the
   `issues.resolve_unchecked(...)` call (SUCCEEDED only), in the same session, call
   `await self.inventory.reconcile_sweep(session, config_id, job.created_at)`.
8. `collectors/tasks.py`, `CollectorScanHandler`:
   - new field `inventory: InventoryReconciler = field(default_factory=NoopInventoryReconciler)`;
   - in `_changes`, track `ref_skipped` (set where `redactor.has_hits(ref.ref_json)`
     skips a ref);
   - after the existing `full_listing` issue reconciliation, when `full_listing and not
     ref_skipped`, call `reconcile_listing(session, config.id, frozenset(listed))` in a
     new `sessions.begin()` block.
9. Collector inventory mode:
   - `collectors/repository.py`: `to_config` maps `row.inventory_mode`; create and update
     write it.
   - `collectors/service.py`: `CollectorService` is frozen, so add
     `inventory: InventoryReconciler = field(default_factory=NoopInventoryReconciler)`.
     p08 passes it at construction.
     - `create` and `update` accept `inventory_mode: InventoryMode | None`. On create,
       None means `sensitive_only`; on update, None keeps the current value.
     - When an update changes the mode to `off` from any other mode, call
       `self.inventory.stop_tracking(session, config.id, actor_ref)` in the same
       transaction.
     - The `collector.updated` and `collector.created` audit details include
       `inventory_mode`.
   - `api/routers/collectors.py`: `CollectorBody.inventory_mode: InventoryMode | None =
     None`, and list and detail responses include `inventory_mode`.
10. `collectors/google/adapter.py`, gsheets branch of `fetch`: when the fetch is complete,
    set `items=ItemReport(count=<number of rows with at least one non-empty cell, summed
    over all sheets>)`. Partial or failed fetches set no items.

## Pitfalls

- Do not call the observer before the issue processing. Do not open a new transaction for
  it.
- `record_external` already calls `record_scan` per locator. Do not call the observer
  twice; the hook in `record_scan` covers external scans.
- Keep `IssueRecorder` and `ExternalScan` positional construction working; police and
  ingest build them positionally.
- `frozenset(listed)` must only contain keys computed with `config.kind.value` as the
  source (the existing `listed` set already does this).
- The `collectors.py` router update must not reset `inventory_mode` when an old client
  omits it.
- Never put raw keys in a log line, an exception or an audit record. `TelescreenError`
  details stay static.

## Tests

- `tests/issues/v14_hooks/test_observer_hook.py`, with a recording fake observer:
  - a fetched scan with 2 findings of the same value -> one observation with 1 value pair,
    `complete = True`, `removed = False`;
  - a too-large resource -> `complete = False`, and items passed through;
  - a removed `_record_empty`-style scan -> `removed = True`;
  - `record_external` with 2 locators and an inventory for one -> two observations; one
    has re-keyed fingerprints that do not equal the external input;
  - item keys `("k1", "k1", "k2")` -> 2 fingerprints, neither containing `k1`;
  - an exception in the fake observer rolls back the issue changes of the same
    transaction (documents that the hook is transactional).
- `tests/api/v14_ingest/test_ingest_v14.py`:
  - an old-style payload still returns 200 with the same response keys;
  - `removed: true` with segments -> 422;
  - `removed: true` with no segments -> the open issue resolves as `resource_deleted`,
    and the fake observer saw `removed`;
  - `item_keys` and `item_count` together -> 422;
  - 100001 keys -> 422;
  - a sample secret as an item key is absent from the response, logs (caplog) and DB text
    columns;
  - `/findings` with a bad fingerprint pattern -> 422.
- `tests/police/v14/test_inventories.py`:
  - a 1.2 `detect` result with inventories for an allowed and a disallowed locator ->
    one inventory passed, and the audit shows `inventories_dropped: 1`;
  - a 1.1 result is unchanged;
  - an inventory on a `verify_remediation` result is ignored.
- `tests/workflow/v14/test_reduce_inventory.py`: a SUCCEEDED job calls `reconcile_sweep`
  once with `job.created_at`; PARTIAL and FAILED jobs never call it.
- `tests/collectors/v14/test_listing_inventory.py`:
  - a full listing with no skips calls `reconcile_listing` with the listed keys;
  - one ref skipped for a `Redactor` hit -> not called;
  - an incremental (non-full) listing -> not called;
  - an exception mid-listing -> not called.
- `tests/collectors/v14/test_inventory_mode.py`:
  - create defaults to `sensitive_only`;
  - an update without the field keeps `all`;
  - an update to `off` calls `stop_tracking` once; off -> off does not call it again;
  - the API response includes `inventory_mode`;
  - the audit details include it.
- `tests/collectors/v14/test_sheets_items.py`: a gsheets fetch over 2 sheets with 3 and 2
  non-empty rows (plus blank rows) -> `items.count == 5`; a partial fetch -> no items.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`. `<src>` is
the Python files in writePaths. `<tests>` is
`tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest`.

```bash
uv run --frozen ruff format --check --no-cache <src> <tests>
uv run --frozen ruff check --no-cache <src> <tests>
uv run --frozen ty check <src> <tests>
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p05-typelint.log 2>&1; grep -E '^(src/telescreen/(issues|api/routers/(ingest|collectors)|police/results|workflow/mapreduce/reduce|collectors)|tests/(issues/v14_hooks|collectors|police/v14|workflow/v14|api/v14_ingest))' /tmp/telescreen-v14-p05-typelint.log
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-v14-p05-pytest.log; echo "exit=$?"
wc -l src/telescreen/issues/service.py src/telescreen/issues/external_scan.py
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p05-pytest-all.log 2>&1; echo "exit=$?"
```

Expected:

- the scoped checks exit 0 and the typelint grep is empty;
- `<tests>` covers the existing issue, collector, police and workflow suites, which proves
  no regression;
- `service.py` is under 1000 lines.

The full suite is informational. A failure in a file outside writePaths is a
Reconciliation request.

## Completion Criteria

- [ ] The split is done with behavior unchanged, and the hook is in `record_scan` only.
- [ ] The ingest, police, reduce, listing, mode and sheets changes are done, with the
  tests above.
- [ ] The existing suites under `tests/issues`, `tests/collectors`, `tests/police` and
  `tests/workflow` pass.
- [ ] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

(none yet)
