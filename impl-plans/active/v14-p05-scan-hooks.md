# V14-P05 Scan Hooks: Observer Call, Reconciliation Calls, Ingest 1.1, Police 1.2, Inventory Mode, Sheets Count

**Status**: Completed
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p05-typelint.log 2>&1; tool_code=$?; if grep -E '^/Users/taco/gits/tacogips/telescreen/(src/telescreen/(issues|api/routers/(ingest|collectors)|police/results|workflow/mapreduce/reduce|collectors)|tests/(issues/v14_hooks|collectors|police/v14|workflow/v14|api/v14_ingest))' /tmp/telescreen-v14-p05-typelint.log; then echo 'p05 scoped typelint violations found'; exit 1; else echo "No p05 scoped typelint violations; repository typelint exit=$tool_code"; fi
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

- [x] The split is done with behavior unchanged, and the hook is in `record_scan` only.
- [x] The ingest, police, reduce, listing, mode and sheets changes are done, with the
  tests above.
- [x] The existing suites under `tests/issues`, `tests/collectors`, `tests/police` and
  `tests/workflow` pass.
- [x] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

### Session: 2026-10-13 14:30
**Tasks Completed**: Split external scan recording into `issues/external_scan.py`; added the transactional observer hook and scan item HMACs; implemented additive push inventory fields, Police 1.2 inventory parsing/audit, SUCCEEDED sweep and complete-listing reconciliation, collector inventory mode persistence/stop behavior, and complete Sheets row counts. Added focused tests under the declared v14 test paths and collector list API coverage.
**Tasks In Progress**: None for this plan.
**Blockers**: None.
**Reconciliation requests**: Repository typelint reports three TL003 bindings owned by inventory-core work at `tests/inventory/core/test_observer.py:101,132,165` (`harness` annotations). This path is outside this plan's writePaths; p05's scoped typelint filter is empty. The inventory-core owner should add those annotations.
**Hashes**:
  - `impl-plans/active/v14-p05-scan-hooks.md` before=1afc181a08ecc9dba94c09532233fb3daa2d6a318159da8b210d857385e10622 after=see `tmp/telescreen-v14-20261013-f1/p05/final-hashes.txt`.
  - `src/telescreen/issues/scan.py` before=c8485c24c4a8bb740ae032f683eff606de1caf618aa9523a10d972c921d99400 after=5c6223914e50bfb5d0cd77fd1c097e7396ccdb94b09dc17053c80a0d55921934
  - `src/telescreen/issues/service.py` before=b37c9aa79922bf50710201c13ae0cb9c1eca9b54c2005033b665f6ecaddfdf43 after=695f3a90b5cd938476800fea7a8f6d3831ba1a1ec2b5e2ec24323aa3eccc95f4
  - `src/telescreen/issues/external_scan.py` before=absent after=10f553a2c6571e99d1d9945e2dc413f39e841449f03ec338a6595965da235f99
  - `src/telescreen/issues/ingest.py` before=a521544428ecb1fca8733c5c3b3ad353f19d3ed073ee8ef2f1817a7fb5bbfbbc after=5cc5d04d51617f296e2e4fd915840a50180da416110e51e2371dd97c4abed553
  - `src/telescreen/api/routers/ingest.py` before=58a5a5f30abbb78ac43ac3d819e4ba1e5cc5994470594716e5092d036c8539c7 after=3156ea08171958dfcca9ec2202e2a86476a30233463e5bfa682a1cec92565180
  - `src/telescreen/api/routers/collectors.py` before=27202baefba14cc4e11230b721f328b8e63eb82b9aec8673473c5ed124b7a629 after=1339a28758161989c2a1aa94bf53ccb81b0a73cc26e8c93ac10e0d910c6e96c3
  - `src/telescreen/police/results.py` before=800afa13b4e815007249acfd5538247ba418d40dd7c3571dc15a8ac7afab3b26 after=09e8f16d7c31fc8f0e07847b76d7fc085c25cfa9b637a488ad11c9cea321acbd
  - `src/telescreen/workflow/mapreduce/reduce.py` before=144e9f84913eebf7279a70a552e6edc3d297d2d6a23d03e3df58296694ad6407 after=9f7aaf3d72ef00b82ed6e29484de4217ab97f89898fd65e518cdc16eb5b9cfd4
  - `src/telescreen/collectors/tasks.py` before=e4691f156159a6d635cdbddc5f669fe4d7126dd72cb438e63418df2ef52fae6c after=4bf6c00e9cd2914e035514e785b84291df276802ed3c9a845337a408c9d38096
  - `src/telescreen/collectors/service.py` before=e7f0a6de8f42dd3612468c949daf9596da11f6e6b9bdf410f50a49021425baf4 after=79082583b47ce8594e7654268b98002e09c80510614e088fa618710992311a2c
  - `src/telescreen/collectors/repository.py` before=268c9d8ce6ac5496e8d2c17c50df6bf9aa50ca49506dbac038aea1cc323e18a8 after=03d2f4343a28edd3bb335e7fe76ff81fa186bf6f50dc74635f1911396f9a48a9
  - `src/telescreen/collectors/google/adapter.py` before=523c88d960034ab96693de87291b549e68f255aded79bb32c12e731207d42fa9 after=53e975ef75e16ae69d4c65d987bda83d8ee41fc5a9230f0d230baf8ef38969d5
  - `tests/collectors/core/test_core.py` before=53cb4da53648fd1331244214f965d045bc9acb5dac6d81d724247d9fa287295e after=e8a5a9f937e72a539d72c1dfa11bdf38cbc46caa92641fd28a7fa2d0d1a61e5a
  - Added test files and all final hashes: `tmp/telescreen-v14-20261013-f1/p05/final-hashes.txt` (new test files were absent before this session).
**Verification**:
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-format-final3.log 2>&1; result=$?; cat /tmp/telescreen-v14-p05-format-final3.log; echo exit=$result` -> exit 0, 75 files formatted.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-ruff-final3.log 2>&1; result=$?; cat /tmp/telescreen-v14-p05-ruff-final3.log; echo exit=$result` -> exit 0.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-ty-final3.log 2>&1; result=$?; cat /tmp/telescreen-v14-p05-ty-final3.log; echo exit=$result` -> exit 0.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p05-typelint-final3.log 2>&1; tool_result=$?; if grep -E '^(src/telescreen/(issues|api/routers/(ingest|collectors)|police/results|workflow/mapreduce/reduce|collectors)|tests/(issues/v14_hooks|collectors|police/v14|workflow/v14|api/v14_ingest))' /tmp/telescreen-v14-p05-typelint-final3.log; then echo 'scoped typelint violations found'; exit 1; else echo "No scoped typelint violations; repository tool exit=$tool_result"; exit 0; fi` -> scoped grep empty, command exit 0; typelint process exit 1 only for the three inventory-core findings listed above.
  - `uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest` -> exit 0, 473 passed, 1 owner-only integration test skipped (log: `/tmp/telescreen-v14-p05-pytest-final4.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; wc -l src/telescreen/issues/service.py src/telescreen/issues/external_scan.py > /tmp/telescreen-v14-p05-lines-final3.log; cat /tmp/telescreen-v14-p05-lines-final3.log; test "$(wc -l < src/telescreen/issues/service.py)" -lt 1000 && test "$(wc -l < src/telescreen/issues/external_scan.py)" -lt 1000; echo exit=$?` -> exit 0, 942 and 123 lines.
**Notes**: Earlier verification attempts were superseded by current-source passes: initial scoped pytest exposed the extracted module's runtime annotation import (four failures); a later collection attempt found a test import typo; an intermediate sweep test compared SQLite's timezone-naive timestamp before normalization. Corrections were made and the final scoped suites pass. Final current-source run is the evidence above.

### Session: 2026-10-13 Step 6 rerun
**Tasks Completed**: Re-ran p05 verification on the current shared tree. All assigned source hashes still match the prior p05 final hash record. The whole-tree typelint issue noted in the earlier progress entry is resolved in the current tree; no reconciliation request remains. The prior failed pytest attempts remain historical and are superseded by this passing current-source run.
**Tasks In Progress**: None for this plan. Downstream independent review and serial finalization remain assigned to later workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**:
  - `impl-plans/active/v14-p05-scan-hooks.md` pre-edit=9d39d83a3be23e7c287df5cbe524bdc14dd55584072de2a2547af1a861be82f8; post-edit recorded in `tmp/telescreen-v14-20261013-f1/p05/step6-rerun-post-hashes.txt`.
  - The 14 p05 implementation and existing test files match the hashes recorded above and in `tmp/telescreen-v14-20261013-f1/p05/final-hashes.txt`.
  - Per-edit intent: `tmp/telescreen-v14-20261013-f1/p05/step6-rerun-edit-intent.md` (SHA-256 `d584f6078305ab90f7fe2a3e4019120721ed755feead88c27cf7540ad3133413`).
**Verification**:
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-rerun-format.log 2>&1; code=$?; cat /tmp/telescreen-v14-p05-rerun-format.log; echo exit=$code; exit $code` -> exit 0, 75 files already formatted.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-rerun-ruff.log 2>&1; code=$?; cat /tmp/telescreen-v14-p05-rerun-ruff.log; echo exit=$code; exit $code` -> exit 0, all checks passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/issues/scan.py src/telescreen/issues/service.py src/telescreen/issues/external_scan.py src/telescreen/issues/ingest.py src/telescreen/api/routers/ingest.py src/telescreen/api/routers/collectors.py src/telescreen/police/results.py src/telescreen/workflow/mapreduce/reduce.py src/telescreen/collectors/tasks.py src/telescreen/collectors/repository.py src/telescreen/collectors/google/adapter.py tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-rerun-ty.log 2>&1; code=$?; cat /tmp/telescreen-v14-p05-rerun-ty.log; echo exit=$code; exit $code` -> exit 0, all checks passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p05-rerun-typelint.log 2>&1; tool_code=$?; if grep -E '^/Users/taco/gits/tacogips/telescreen/(src/telescreen/(issues|api/routers/(ingest|collectors)|police/results|workflow/mapreduce/reduce|collectors)|tests/(issues/v14_hooks|collectors|police/v14|workflow/v14|api/v14_ingest))' /tmp/telescreen-v14-p05-rerun-typelint.log; then echo 'p05-scoped violations found'; exit 1; else cat /tmp/telescreen-v14-p05-rerun-typelint.log; echo "No p05-scoped violations; repository typelint exit=$tool_code"; fi` -> exit 0, repository typelint reports 578 files clean.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest > /tmp/telescreen-v14-p05-rerun-pytest.log 2>&1; code=$?; cat /tmp/telescreen-v14-p05-rerun-pytest.log; echo exit=$code; exit $code` -> exit 0, 473 passed, 1 owner-service integration test skipped because `TELESCREEN_TEST_DATABASE_URL` and `TELESCREEN_TEST_MEILI_URL` are absent.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; wc -l src/telescreen/issues/service.py src/telescreen/issues/external_scan.py > /tmp/telescreen-v14-p05-rerun-lines.log; cat /tmp/telescreen-v14-p05-rerun-lines.log; test "$(wc -l < src/telescreen/issues/service.py)" -lt 1000 && test "$(wc -l < src/telescreen/issues/external_scan.py)" -lt 1000` -> exit 0, 942 and 123 lines.

### Session: 2026-10-13 P05 test-integrity repairs
**Tasks Completed**: Addressed P05-TI-1 with FAILED and PARTIAL map-reduce tests plus incremental-listing and fetch-exception tests; each asserts zero inventory reconciliation. Addressed P05-TI-2 with HTTP-level old response-shape checks, removed push resolution and observer propagation, raw item-key re-keying/no-leak assertions across response, caplog, issue/finding/history/audit text columns, and an echo-free invalid-item 422 check. Existing tests remain present and unchanged.
**Tasks In Progress**: None for p05. Independent review and serial finalization remain assigned to later steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/workflow/v14/test_reduce_inventory.py` before=`f344e43a7767388d45101476dff6540be980fb7d037d1fa0fd3a191a61c6fda2` after=`9f6730449dd03bba9b70cc5d13c4890fefdbe83ebb5e5e21fa2ff829f986f1bd`; `tests/collectors/v14/test_listing_inventory.py` before=`9c3d3b32de6a665bd9dfb63040eae85ea242b9dc8c8a0833e41a5d8d5556902b` after=`96eaab64541449865cf9f6e099e6b06e555bc7b1e500c795aa9851fbdadfd0c0`; `tests/api/v14_ingest/test_ingest_v14.py` before=`848dde992a1091c82c2c2a6efd0991a5d177cd72469e89ebe5a1546462e90102` after=`f37b1f7980288dfd27629d50295d34fcbb68e474fdd3f086d9dd238b351d5577`; plan before=`6193b91308a0db142256a157bedf586a77825af5c843856d0114d39df0380a26`, after recorded in `tmp/telescreen-v14-20261013-f1/p05/step6-ti-post-hashes.txt`. Per-edit intentions are under `tmp/telescreen-v14-20261013-f1/p05/step6-ti*-intent.txt`.
**Verification**:
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider -q tests/workflow/v14/test_reduce_inventory.py tests/collectors/v14/test_listing_inventory.py tests/api/v14_ingest/test_ingest_v14.py` -> exit 0, 11 passed (log: `/tmp/telescreen-v14-p05-ti-focused-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache <p05 source and test paths>` -> exit 0, 76 files already formatted (log: `/tmp/telescreen-v14-p05-ti-format-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache <p05 source and test paths>` -> exit 0, all checks passed (log: `/tmp/telescreen-v14-p05-ti-ruff-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check <p05 source and test paths>` -> exit 0, all checks passed (log: `/tmp/telescreen-v14-p05-ti-ty-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` plus absolute-path p05 filter -> exit 0, repository typelint reports 591 files clean and no p05 violations (log: `/tmp/telescreen-v14-p05-ti-typelint-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/police tests/workflow tests/api/v14_ingest` -> exit 0, 480 passed, 1 owner-only PostgreSQL integration test skipped (log: `/tmp/telescreen-v14-p05-ti-pytest-final.log`).
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; wc -l src/telescreen/issues/service.py src/telescreen/issues/external_scan.py` and `<1000` guards -> exit 0, 942 and 123 lines (log: `/tmp/telescreen-v14-p05-ti-lines-final.log`).
**Notes**: The first focused run passed 10 and failed the HTTP removal assertion because its preceding complete empty item scan had already resolved the issue as `verified_removed`. The test now reports the item key in the content-bearing opening scan and then verifies the explicit removal transition. That first failure is superseded by the focused and full scoped passes above. An initial formatting/lint/typelint run also identified only test-file style and annotation fixes, all corrected before the final gates.
