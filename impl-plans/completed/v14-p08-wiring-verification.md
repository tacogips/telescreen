# V14-P08 Wiring, Route Permissions, E2E Flows, Leak Matrix, Shape Contract and Doc Status

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 9, 12, 13, 14; design-docs/specs/design-masking.md "v1.4 additions"
**Created**: 2026-10-13
**Last Updated**: 2026-10-14

## Plan Metadata

- planId: `v14-p08-wiring-verification`
- planPath: `impl-plans/active/v14-p08-wiring-verification.md`
- run 3 (manifest `telescreen-v14-20261014-f3`, design 17.7): the only wave, mode
  `re-verify`, no in-run dependencies (p01-p07 and p09 are accepted). The run-3
  writePaths are listed in "Run 3 scope" below and replace the earlier lists.
- run 2 (manifest `telescreen-v14-20261014-f2`): wave B, re-verify; not reached, because
  the run-2 integration review stopped on O3 (requirements 8.1).
- wave (run 1): 4
- dependsOn (run 1): `v14-p04-inventory-core`, `v14-p05-scan-hooks`, `v14-p06-ui-pages`, `v14-p07-fields-retention`
- writePaths (run 1):
  - `impl-plans/active/v14-p08-wiring-verification.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_v14.py`
  - `src/telescreen/api/app.py`
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_route_permissions.py`
  - `tests/e2e/test_wiring_v14.py`
  - `tests/e2e/test_route_permissions_v14.py`
  - `tests/e2e/test_flow_v14_inventory.py`
  - `tests/e2e/test_flow_v14_fields.py`
  - `tests/leak/flows_v14.py`
  - `tests/leak/test_leak_matrix_v14.py`
  - `tests/contract/test_data_api_shapes.py`
  - `src/web/scripts/screenshots.ts`
  - `impl-plans/README.md`
  - `design-docs/specs/design-v1-4-data-inventory.md`
  - `design-docs/specs/architecture.md`
  - `design-docs/specs/design-data-model.md`
  - `design-docs/specs/design-api-and-ui.md`
  - `design-docs/specs/design-collectors-and-ingestion.md`
  - `design-docs/specs/design-police-contract.md`
  - `design-docs/specs/design-masking.md`
  - `design-docs/specs/design-ui-sentry.md`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Run 3 scope (re-verify, design 17.7)

- Code is complete at the run-3 base (`42864aa` plus the run-3 design/plan commit).
  Follow both "Owner resolution" sections of the Progress Log. The expected outcome is
  zero source edits: only this plan file changes.
- Run-3 writePaths, for gate-failure fixes only (each fix logged with the failing
  command, cause and pre/post SHA-256):
  - `impl-plans/active/v14-p08-wiring-verification.md` (always)
  - `src/telescreen/wiring.py`, `src/telescreen/wiring_v14.py`, `src/telescreen/api/app.py`
  - `src/telescreen/inventory`, `src/telescreen/workflow/queue/sql_queue.py` (O3 file)
  - `src/telescreen_police_sdk`
  - `tests/e2e`, `tests/leak`, `tests/contract`, `tests/inventory`, `tests/db`
  - `db/schema` (only `mise run ddl-export` drift; never hand-edit DDL semantics)
  - `src/web/src/pages/data`, `src/web/src/api`, `src/web/src/components`,
    `src/web/src/shell.test.tsx`, `src/web/scripts/screenshots.ts`
- Never touch (a failure that needs one is a Reconciliation request for the serial
  finalization, reported, never hidden): `pyproject.toml`, `uv.lock`,
  `src/web/package.json`, `src/web/bun.lock`, `src/telescreen/db/models`,
  `src/telescreen/db/migrations`, `tools/typelint.py`, ruff/ty config, `README.md`,
  `impl-plans/README.md`, `design-docs/**`, every other plan file and manifest.
- Do not weaken a test, a lint rule or strict screenshot mode to make a gate pass. No
  `# type: ignore` / `# ty: ignore`. No git commands except read-only `git diff` and
  `git status`.
- Flake rule: if `bun run test` fails, rerun it alone once. Log both runs. If it fails
  twice, it is a real failure. Never drop a failing test.
- Owner baseline to match or exceed: typelint 603 files clean; full pytest 1111 passed /
  11 skipped, 0 failed; vitest 262 passed; strict screenshots with 0 state-skipped and 0
  browser errors.
- Append a run-3 Progress Log session: each command, its exit status, its log path and
  the counts above. Tick the E1-E7 box when the scoped and full pytest pass, and the gates
  box when every command exits 0. Note that the doc status lines and the index are done
  by the serial finalization. Set Status to Completed.
- Step result (conventions 7a): `verification` lists only the final passing commands.
  `priorVerification`, `findings`, `risks`, `authorSelfCheck.findings` and
  `authorSelfCheck.residualRisks` are empty. Skipped live and integration tests are not
  listed. The run-1 E3 blocker, resolved by the owner (O2), goes only in
  `addressedFeedback`, with changed paths "none in p08 (owner test
  tests/e2e/test_flow_v14_inventory.py)" and the passing scoped pytest command.

## Intent and Context

This plan does the following:

- binds the v1.4 services, ports, scheduler provider, task handler and routers in the real
  container;
- proves every new route's permission;
- runs the end-to-end flows E1-E7 of design 14 and the leak matrix flows 1-7 of design
  12;
- checks that the real API key sets equal the mock manifest
  (`src/web/src/api/mock/fixtures/v14-shapes.json`);
- runs every gate on the integrated tree;
- updates the design status lines.

Feature code, `README.md` (p01), `src/web/src/**` and the p01-p07 tests are read-only. A
defect there is a Reconciliation request naming the owning plan; do not fix it in place.

Imitate:

- `wiring_v13.py` (`WiringV13Parts`, `WiringV13`, `build_v13`, and the providers,
  handlers and routers tuples) and `wiring.py:485-548` (post-construction port
  assignment, scheduler providers and handler registration);
- `tests/e2e/test_wiring_v13.py` and `tests/e2e/test_route_permissions_v13.py`
  (`_route_rows`, `_expected`);
- `tests/e2e/harness.py` and `tests/e2e/test_flow_v13_weekly.py` (driving the scheduler
  with `FakeClock`);
- `tests/leak/flows_v13.py`, `test_leak_matrix_v13.py` and `sinks.py`
  (`_assert_flow_sinks`, SMTP capture);
- `tests/e2e/test_flow_sweep.py` (sweep through the real container) and the in-process
  SDK `PullWorker` use in existing E2E police flows.

## Non-goals

- No feature changes, no README edits, no web source edits except strict-run fixes in
  `scripts/screenshots.ts`. Each such fix states the reason in the Progress Log.
- No live tests. The owner runs them; skipped live tests are never listed as
  verification.
- No archiving: the serial finalization moves the plans.

## Invariants

- `build_container` keeps its signature, so existing callers (the e2e harness,
  `tests/api/v12_rbac/rbac_world.py`, the leak harness) keep working.
- Routers are registered through `container.routers`. Composition copies `APIRoute`
  objects; never `include_router`.
- `wiring.py` stays under 1000 lines, so the v1.4 construction lives in `wiring_v14.py`.

## Deliverables

1. `wiring_v14.py`:
   - `WiringV14Parts`: sessions, clock, settings, audit, redactor, fingerprinter, the
     collector config reader, resource issue reader, actor directory, recipient resolver,
     owner directory, member notifier, notification sender, task queue and api context;
   - `WiringV14`: `observer`, `reconciler`, `routers`, `providers`, `handlers`;
   - `build_v14(parts) -> WiringV14` constructs, in order:
     - `InventoryRepository`, `InventoryObserverImpl`, `InventoryReconcilerImpl`;
     - `AssetQueries`, `AssetExporter`, `AssetService`;
     - `FieldService`, `ValueValidator`, `ValueService`, `RegistrationService`;
     - `IssueAssetOwnerResolver`, `DataOverdueProvider(settings.data_overdue_cron, ...)`,
       `DataOverdueHandler`;
     - the four routers (`data_assets`, `data_asset_detail`, `data_fields`,
       `data_asset_values`).
2. `wiring.py`:
   - build the v1.4 reconciler before `CollectorService`, `CollectorScanHandler` and
     `MapReduceHandler`, and pass it as their `inventory` (`CollectorService` is frozen,
     so pass it at construction);
   - set `issue_service.inventory = v14.observer` after construction, as v1.3 does for
     `findings_observer`;
   - add `v14.providers` to the scheduler and `v14.handlers` to the worker;
   - add `v14.routers` to the routers tuple, next to `v13.routers` and before the issues
     router;
   - `Container` gains `v14: WiringV14` as the last field.

   `api/app.py` changes only if registration needs it.
3. `tests/e2e/test_wiring_v14.py`:
   - the container exposes `v14`;
   - `issue_service.inventory` is the real observer;
   - the collector service, scan handler and reduce handler hold the real reconciler;
   - the scheduler includes `DataOverdueProvider`;
   - the worker has a `data_overdue_check` handler;
   - `/api/data/counts`, `/api/data/export` and `/api/data/resource-assets/{k}` resolve to
     their own handlers, not to `/api/data/assets/{asset_id}`;
   - `POST /api/data/fields/reorder` resolves to reorder.
4. `tests/e2e/test_route_permissions_v14.py`: the exact table of the 22 routes of design 9
   with methods and permissions: 12 from p04 (`data_assets`, `data_asset_detail`) and
   10 from p07 (`data_fields`, `data_asset_values`). Each route has exactly one marker. A viewer token gets
   403 on one write route and one admin route, and 200 on one read route.
   `tests/e2e/test_route_permissions.py` and `test_api_routes.py` are edited only if
   their inventories need the new routers.
5. `tests/e2e/test_flow_v14_inventory.py`, flows E1-E4 and E7 of design 14, through the
   real container over HTTP:
   - E1: discovery in `sensitive_only` (push without findings -> no asset; with a finding
     -> asset), `all`, and `off`;
   - E2: keys 5 -> 3 complete (`items_removed = 2`, prefixes in `/items`); an incomplete
     report gives no removal; count-only deltas; `/item-series` points;
   - E3:
     - push `removed: true` -> asset `deleted` and issue `resource_deleted`;
     - re-push -> `restored`;
     - a fake-adapter sweep that ends SUCCEEDED, with the resource absent -> deleted;
       PARTIAL -> not deleted;
     - a full listing with the resource absent -> deleted;
     - collector `PATCH` to `off` -> `unknown`, then back to `all` plus an observation ->
       `tracking_resumed`;
   - E4: a police 1.2 `detect` result with inventories, submitted by the in-process SDK
     `PullWorker` using `item_fingerprint` -> items recorded;
   - E7: CSV export (header, a formula cell prefixed, truncation header absent, audit
     record).
6. `tests/e2e/test_flow_v14_fields.py`:
   - E5: field schema CRUD, required -> incomplete tab, values `PUT`, a type change
     blocked by values, a value containing a sample secret -> 422 with no echo,
     `metadata_changed` events, audit records;
   - E6: `FakeClock` past the due date -> scheduler tick plus drain -> one notification
     (fake SMTP or Slack MockTransport) with the masked context; a second tick sends
     none; a changed due date notifies again.
7. `tests/leak/flows_v14.py` and `test_leak_matrix_v14.py`: flows 1-7 of design 12. After
   each flow, assert every sink (DB text, logs, search documents, outbound bodies, API
   responses, SMTP), using `_assert_flow_sinks` from the v13 pattern. Flow 1 uses sample
   secrets as item keys, in segments, in the locator and in the title. Flow 6 drives the
   overdue notification.
8. `tests/contract/test_data_api_shapes.py`: read `v14-shapes.json` with
   `telescreen.core.json.parse_json`. Call the real routes through the e2e harness and
   assert that each object's key set equals the manifest entry: list item, detail, detail
   field slot, event, item, field, counts and series point.
9. Docs, in the files in writePaths only:
   - the `Status:` lines of the v1.4 design doc and of the "v1.4" sections of the
     summary docs change from "designed" to "implemented 2026-10-xx", once every gate
     passes;
   - `impl-plans/README.md`: the v1.4 rows record the outcome.

## Pitfalls

- The reconciler must exist before the frozen `CollectorService` is constructed. Check
  the construction order in `wiring.py`.
- Do not register the v1.4 routers after a catch-all. Assert the resolution in the tests
  (deliverable 3).
- E2E flows must use `FakeClock`, MockTransport and the fake SMTP from the harness. No
  network.
- Leak flows must not stub inventory services; use the production path (design-masking
  leak matrix rule).
- If a strict screenshot state fails because a page renders a different name than the
  binding names of p03 and p06, fix only `screenshots.ts` when the page is correct and
  the state is wrong. Otherwise file a Reconciliation request against p06.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check . > /tmp/telescreen-v14-p08-format.log 2>&1; echo "exit=$?"
uv run --frozen ruff check . > /tmp/telescreen-v14-p08-ruff.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p08-typelint.log 2>&1; echo "exit=$?"
uv run --frozen ty check > /tmp/telescreen-v14-p08-ty.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e tests/leak tests/contract 2>&1 | tee /tmp/telescreen-v14-p08-pytest-scoped.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p08-pytest-all.log 2>&1; echo "exit=$?"
uv build --out-dir /tmp/telescreen-v14-p08-dist > /tmp/telescreen-v14-p08-build.log 2>&1; echo "exit=$?"
mise run ddl-check > /tmp/telescreen-v14-p08-ddl-check.log 2>&1; echo "exit=$?"
(cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v14-p08-web-install.log 2>&1 && bun run typecheck > /tmp/telescreen-v14-p08-web-typecheck.log 2>&1 && bun run test > /tmp/telescreen-v14-p08-web-test.log 2>&1 && bun run build --outDir /tmp/telescreen-v14-p08-web --emptyOutDir > /tmp/telescreen-v14-p08-web-build.log 2>&1 && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v14-p08-screenshots.log 2>&1); echo "exit=$?"
test "$(grep -c 'state-skipped' /tmp/telescreen-v14-p08-screenshots.log)" -eq 0; echo "exit=$?"
wc -l src/telescreen/wiring.py src/telescreen/wiring_v14.py
git diff --quiet HEAD -- pyproject.toml uv.lock src/web/package.json src/web/bun.lock src/telescreen/db/models src/telescreen/db/migrations tools/typelint.py README.md impl-plans/README.md design-docs; echo "exit=$?"
git diff --name-only HEAD  # run 3: expected only this plan file, unless a logged gate fix
```

Expected:

- every command exits 0;
- the full pytest suite passes offline; integration and live tests skip without the
  `TELESCREEN_TEST_*` variables, and are not risks (conventions 7a);
- the skipped-state count is 0, and the screenshot log reports no browser errors;
- the wiring files are under 1000 lines.

## Completion Criteria

- [x] Wiring binds every v1.4 port, provider, handler and router, with the wiring tests
  green.
- [x] `test_route_permissions_v14.py` covers all 22 routes.
- [x] E1-E7 and leak flows 1-7 pass.
- [x] The shape contract test passes.
- [x] Every gate above exits 0. The doc status lines are updated only after that.
- [x] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

Implementation source is integrated and verified. The inventory E2E covers push discovery,
incomplete and complete item deltas, push removal/restoration, tracking-mode transitions,
CSV safety, Police 1.2 inventory delivery through the SDK `PullWorker`, and successful vs
partial sweep reconciliation. E5-E6 field and overdue flows, all seven leak flows, the exact
22-route permission table, and real response-shape checks pass.

One assigned E3 scenario remains: there is no real-path full-listing test proving an absent
resource is deleted by an authoritative completed listing. Existing collector tests verify
the listing gate's skip rules, and the full suite passes, but that does not prove the v1.4
asset lifecycle result through the integrated container. Keep the E1-E7 completion box open
until this is covered. Review-dependent design status and index updates remain for the
serial review/finalization steps.

Final source SHA-256:

- `src/telescreen/wiring.py`: `cf80c5078a89dc4571568b839e4a01b445e21b1a669abbc3bd2dc14cdc27f241`
- `src/telescreen/wiring_v14.py`: `6aa0be0c798e8c99bf79476f888f630ca1fc68b2fef89685b9cee424dd8752ef`
- `tests/e2e/test_flow_v14_inventory.py`: `383233783799b06f24c9d5c27b8c8c58db102fc632f3ebfbd9902f9f7a44b3b9`
- `tests/e2e/test_wiring_v14.py`: `f17eb4b0870ddec959cc61bc8c2d031ddd8128159a03886f11e7e821dd294909`
- `tests/e2e/test_route_permissions_v14.py`: `e1b71c47eaf90de1c1a9a4b8d363b47a2ecd10d03f728e9eeab3a9ea7165b6cb`
- `tests/e2e/test_flow_v14_fields.py`: `496f598ba8c113f991769390cf7d7e26c8ad1d582f4ab79499034c07fe46edb7`
- `tests/leak/flows_v14.py`: `4e0bbfd69c3071d446a507f1e85421a4f728909648fb66c11216beeb16a66065`
- `tests/leak/test_leak_matrix_v14.py`: `5d863f54ac86cb08ad3cb225c412e5c45fc93280adff326abd8ca06ff6f5de29`
- `tests/contract/test_data_api_shapes.py`: `5d81ba1c9f28f38c39c861a96e2fc2844b2b294cc2cd67dcee8a332806f85ced`

Final verification (all exits 0):

- `uv run --frozen ruff format --check .` — `/tmp/telescreen-v14-p08-format-final.log`
- `uv run --frozen ruff check .` — `/tmp/telescreen-v14-p08-ruff-final.log`
- `uv run --frozen python tools/typelint.py` — `/tmp/telescreen-v14-p08-typelint-final.log` (599 files clean)
- `uv run --frozen ty check` — `/tmp/telescreen-v14-p08-ty-final.log`
- `uv run --frozen pytest -p no:cacheprovider tests/e2e tests/leak tests/contract` — `/tmp/telescreen-v14-p08-pytest-scoped-final.log` (84 passed)
- `uv run --frozen pytest -p no:cacheprovider` — `/tmp/telescreen-v14-p08-pytest-all-final.log` (1104 passed, 11 offline/live skips)
- `uv build --out-dir /tmp/telescreen-v14-p08-dist-final` — `/tmp/telescreen-v14-p08-build-final.log`
- `mise run ddl-check` — `/tmp/telescreen-v14-p08-ddl-check-final.log` (10 passed)
- `cd src/web && bun install --frozen-lockfile` — `/tmp/telescreen-v14-p08-web-install-final.log`
- `cd src/web && bun run typecheck` — `/tmp/telescreen-v14-p08-web-typecheck-final.log`
- `cd src/web && bun run test` — `/tmp/telescreen-v14-p08-web-test-final.log` (256 passed)
- `cd src/web && bun run build --outDir /tmp/telescreen-v14-p08-web-final --emptyOutDir` — `/tmp/telescreen-v14-p08-web-build-final.log`
- `cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` — `/tmp/telescreen-v14-p08-screenshots-final.log` (0 state-skipped; 0 browser errors)
- Wiring limits and route composition guards — exit 0: `wiring.py` 808 lines, `wiring_v14.py` 142 lines, no `include_router`.

### Owner resolution (2026-10-13, before run 2)

The run-1 blocker is resolved by the owner: the integrated full-listing E3 flow is
`tests/e2e/test_flow_v14_inventory.py::test_e3_full_listing_deletes_absent_asset_only_when_authoritative`
(production-wired app from `build_container`, real inventory services, a fake Slack-kind
adapter registered in `container.collector_registry`). It proves that incremental,
skipped-ref and failed listings never delete; that a completed full listing with no
skipped ref deletes the absent asset once with a `deleted` event; that a later sighting
restores it with a `restored` event; and that turning inventory `off` gives `unknown`
with `tracking_stopped` and no deletion (P05-RL-2). Mutating the skipped-ref guard in
`collectors/tasks.py` makes it fail. The owner also fixed O1 (the overdue provider
`ref_id` over `varchar(36)`; see section 8 of the requirements) and added a length guard
in `SqlTaskQueue.enqueue`.

Owner gate run on the combined tree (all exit 0): ruff format and check, typelint (599
files), ty, full pytest 1109 passed / 11 skipped, uv build, `mise run ddl-check`, and the
PostgreSQL integration tests (`TELESCREEN_TEST_DATABASE_URL` on a scratch database:
tests/db, tests/audit and the map-reduce Postgres test, 38 passed).

For the run-2 re-verification: re-run every gate in the Verification section; list only
the final passing commands in `verification`; leave `priorVerification` empty
(conventions 7a). Live tests skip inside the workflow and must not be listed; the owner
ran the live stack and recorded it in section 8 of the requirements. Tick the E1-E7 box
when the scoped and full pytest pass. The run-1 evidence fingerprint is superseded by
this resolution.

### Owner resolution 2 (2026-10-14, before run 3)

Run 2 accepted v14-p09-live-fixes. Its integration review (IR-F2-SQLQUEUE-FORMAT-OWNERSHIP)
found that `src/telescreen/workflow/queue/sql_queue.py` failed ruff E501. The owner
rewrapped it with `ruff format` (O3) and fixed the item-count chart height (O4); see
requirements section 8.1. Owner gates on the combined tree, all exit 0:
- ruff format and check, typelint (603 files), ty, full pytest 1111 passed / 11 skipped,
  uv build, `mise run ddl-check`;
- web typecheck, vitest 262 passed (49 files; one earlier run failed under heavy
  concurrent load and passed on three reruns), build, and strict screenshots with 0
  skipped states.

p08 now needs only re-verification on this tree. Follow the first owner resolution
above and conventions 7a.

### Session: 2026-10-14 01:07
**Tasks Completed**: Re-verified run 3 on the shared tree. The owner-resolved E3 full-listing test is present and passed with the scoped E1-E7, leak, and contract suite. No source files required changes.
**Tasks In Progress**: None in p08. Serial finalization owns plan/manifest archival, index and design status updates, and commit/push.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/wiring.py` before=cf80c5078a89dc4571568b839e4a01b445e21b1a669abbc3bd2dc14cdc27f241 after=cf80c5078a89dc4571568b839e4a01b445e21b1a669abbc3bd2dc14cdc27f241; `src/telescreen/wiring_v14.py` before=554c955b00b6818cef80b8f9598e43d081ab868f93704e48c44a1aea92191630 after=554c955b00b6818cef80b8f9598e43d081ab868f93704e48c44a1aea92191630; `tests/e2e/test_flow_v14_inventory.py` before=61adfabf543ab7af78fff4f8cde6cc58aaed1cc24bf7376b5d827d9fc95e1e8c after=61adfabf543ab7af78fff4f8cde6cc58aaed1cc24bf7376b5d827d9fc95e1e8c; `src/telescreen/workflow/queue/sql_queue.py` before=5558a1c3adac00bf53599165b9b98344e283abf9777196a44f79ac8c8e733e14 after=5558a1c3adac00bf53599165b9b98344e283abf9777196a44f79ac8c8e733e14; plan before=835dbb29e6f5d47f5d9da5fa912e1b042b5c829b587c7599bfad642d67445346 after=bba4630b62d5d15f28ef6ead5e7d57eed76cd46e014751e2280a5f2e59d26200.
**Verification**:
- `uv run --frozen ruff format --check .` -> exit 0 (log: `/tmp/telescreen-v14-p08-format.log`).
- `uv run --frozen ruff check .` -> exit 0 (log: `/tmp/telescreen-v14-p08-ruff.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 603 files clean (log: `/tmp/telescreen-v14-p08-typelint.log`).
- `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v14-p08-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/e2e tests/leak tests/contract` -> exit 0, 86 passed (log: `/tmp/telescreen-v14-p08-pytest-scoped.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 1111 passed, 11 expected offline/live skips, 0 failed (log: `/tmp/telescreen-v14-p08-pytest-all.log`).
- `uv build --out-dir /tmp/telescreen-v14-p08-dist` -> exit 0 (log: `/tmp/telescreen-v14-p08-build.log`).
- `mise run ddl-check` -> exit 0, 10 passed (log: `/tmp/telescreen-v14-p08-ddl-check.log`).
- `cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v14-p08-web-install.log`).
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v14-p08-web-typecheck.log`).
- `cd src/web && bun run test` -> exit 0, 262 passed across 49 files (log: `/tmp/telescreen-v14-p08-web-test.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-v14-p08-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v14-p08-web-build.log`).
- `cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` -> exit 0, zero state-skipped and no browser-error marker (log: `/tmp/telescreen-v14-p08-screenshots.log`).
- Wiring limits and route composition: `test "$(wc -l < src/telescreen/wiring.py)" -lt 1000 && test "$(wc -l < src/telescreen/wiring_v14.py)" -lt 1000` -> exit 0 (808 and 145 lines); `! grep -n include_router src/telescreen/wiring.py src/telescreen/wiring_v14.py` -> exit 0.
- Read-only paths: `git diff --quiet HEAD -- pyproject.toml uv.lock src/web/package.json src/web/bun.lock src/telescreen/db/models src/telescreen/db/migrations tools/typelint.py README.md impl-plans/README.md design-docs` -> exit 0.
- Run-3 diff boundary: `git diff --name-only HEAD` before this plan update was empty; p08 changed only its plan after the log update. No implementation source changes.
- Plan completion boxes: `test "$(grep -c '^- \[ \]' impl-plans/active/v14-p08-wiring-verification.md)" -eq 0` -> exit 0 after this update.
**Notes**: The strict screenshot log contains no `state-skipped` or browser-error entries. Live and integration tests are expected to skip offline and are not listed as verification risks. Per run-3 scope, design status lines and index updates remain serial-finalization work.
