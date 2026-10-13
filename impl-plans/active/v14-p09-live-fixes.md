# V14-P09 Live Fixes L1-L7 (Timeline Time, Collector Actors, Reference Select, Chart, Text, Nav, Overdue)

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md section 17 (17.2-17.6); design-docs/specs/design-v1-4-data-inventory-requirements.md section 8
**Created**: 2026-10-14
**Last Updated**: 2026-10-14

## Plan Metadata

- planId: `v14-p09-live-fixes`
- planPath: `impl-plans/active/v14-p09-live-fixes.md`
- run: 2 (manifest `telescreen-v14-20261014-f2`), wave A, mode `implement`
- dependsOn: `v14-p01-foundation`, `v14-p03-ui-foundation`, `v14-p04-inventory-core`,
  `v14-p06-ui-pages`, `v14-p07-fields-retention` (all accepted dependencies; present at
  the run base)
- writePaths:
  - `impl-plans/active/v14-p09-live-fixes.md`
  - `src/telescreen/inventory/actors.py` (new)
  - `src/telescreen/wiring_v14.py`
  - `tests/inventory/actors` (new directory)
  - `tests/e2e/test_flow_v14_live_fixes.py` (new)
  - `src/web/src/pages/data` (directory; includes the new `data.css`)
  - `src/web/src/components/CountTabs.tsx`
  - `src/web/src/components/CountTabs.test.tsx`
  - `src/web/src/components/ItemCountChart.tsx`
  - `src/web/src/components/ItemCountChart.test.tsx`
  - `src/web/src/components/ui/ui.css`
  - `src/web/src/components/shell/nav.ts`
  - `src/web/src/components/shell/SecondaryPanel.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/src/api/types.ts`
  - `src/web/src/api/mock/fixtures/v14.ts`
  - `src/web/src/api/mock/v14.ts`
  - `src/web/src/api/mock/fixtures.test.ts`
  - `src/web/src/api/mock/mock.test.ts`
  - `src/web/scripts/screenshots.ts`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 3, 6, 7, 7a, 8).

## Intent and Context

The owner ran the live stack after run 1 and found seven defects that the mock hid
(requirements section 8, L1-L7). This plan fixes them with the smallest change per
defect, adds a regression test for each one that fails on the run base `3c5c1dd`, and
adds the live shapes to the mock fixtures. The causes are pinned in design 17.2; do not
re-diagnose them.

| Id | Where | Fix (design 17.2 / 17.3) |
|----|-------|--------------------------|
| L1 | `pages/data/DataDetailSections.tsx`, `pages/data/DataList.tsx`, `pages/data/DataDetail.tsx`, `api/types.ts` | `DataEvent.occurred_at` and `DataAsset.last_seen_at` become `string \| null`; the timeline time is `occurred_at ?? recorded_at`; a null `last_seen_at` renders `—` |
| L2 | new `inventory/actors.py`, `wiring_v14.py` | `InventoryActorDirectory` resolves `collector:<id>` to the collector name |
| L3 | `pages/data/MetadataForm.tsx` | reference options carry `selected`; a stored value missing from the directory keeps its own option |
| L4 | `components/ItemCountChart.tsx` | reactive render instead of an early return |
| L5 | `components/CountTabs.tsx`, `pages/data/*.tsx`, `components/ui/ui.css`, new `pages/data/data.css` | explicit separators plus CSS |
| L6 | `components/shell/nav.ts`, `components/shell/SecondaryPanel.tsx` | "Field schema" entry for `data_fields.admin` in the Data and Settings navs |
| L7 | `pages/data/DataList.tsx` | `danger` "Overdue" tag in the State column |

Patterns to imitate:

- Python: `src/telescreen/directory/actors.py:DirectoryActorDirectory.display` (batching,
  order preservation, `ActorDisplay(ref, kind, display_name, handle, active)`);
  `src/telescreen/inventory/observer.py:InventoryObserverImpl` (a frozen dataclass that
  holds `CollectorConfigReader`); `tests/inventory/core/test_reconcile.py` (a
  `CollectorConfig(...)` builder and fakes); `tests/e2e/test_flow_v14_inventory.py`
  (`make_world`, creating a `custom` collector through `POST /api/collectors`, an ingest
  token from `TokenService`, and push through `/api/ingest/v1/scan`).
- Web: `src/web/src/pages/data/DataDetail.test.tsx` (a `fetch` spy routing by URL, with
  `setSession`); `src/web/src/components/CountTabs.test.tsx`; `src/web/src/shell.test.tsx`
  (an `entriesFor` assertion at line 58); `src/web/src/pages/settings/index.tsx:SettingsNavigation`
  (permission-gated links).

## Non-goals

- No DDL, migration, API route, permission, response key, or dependency/lockfile change.
- No edit to `src/telescreen/directory/actors.py`, `src/telescreen/ports/**`,
  `src/telescreen/inventory/queries.py`, `src/telescreen/inventory/observer.py`,
  `src/telescreen/inventory/reconcile.py` or any p01-p08 test. Stored `actor_ref`
  values are not rewritten; `data_asset_events` stays append-only.
- No change to `RelativeTime.tsx`, `Avatar`, `Tag`, `Timeline` or other shared components
  outside writePaths. No new UI dependency.
- No edit of p08-owned files: `src/telescreen/wiring.py`, `src/telescreen/api/app.py`,
  `tests/e2e/test_flow_v14_inventory.py`, `tests/e2e/test_flow_v14_fields.py`,
  `tests/e2e/test_wiring_v14.py`, `tests/e2e/test_route_permissions*.py`,
  `tests/leak/**`, `tests/contract/**`. No README, design doc or `impl-plans/README.md`
  edit (serial finalization).

## Invariants

- `ActorDirectory` contract: output length and order equal the input; one batched call to
  the wrapped directory per `display` call; `system:*` is still `Telescreen`; `member:`,
  `team:` and `token:` resolution is unchanged.
- Response key sets stay equal to `src/web/src/api/mock/fixtures/v14-shapes.json`
  (read-only here); `fixtures.test.ts` keeps its exact-key assertions.
- Accessible names stay the same: count tabs keep `aria-label="<label> <count>"`; tab,
  button and select names used by `scripts/screenshots.ts` keep their names.
- Saving an unchanged metadata form sends each field's stored `value_json` unchanged.
- Mock fixtures stay masked: reserved example names only, and
  `tests/leak/test_ui_fixtures.py` (read-only) passes.

## Deliverables

### D1. L2 collector actor names (backend)

- `src/telescreen/inventory/actors.py` (module docstring): a frozen dataclass
  `InventoryActorDirectory` with fields `base: ActorDirectory` and
  `configs: CollectorConfigReader`, and
  `async def display(self, session: AsyncSession, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]`.
  - A ref is a collector ref when `ref.partition(":")` gives kind `collector`, a separator
    and a non-empty key. Read each distinct collector id once with
    `configs.get(session, key)`.
  - Found: `ActorDisplay(ref, ActorDisplayKind.SYSTEM, config.name, None, True)`. Missing:
    `ActorDisplay(ref, ActorDisplayKind.SYSTEM, "Telescreen", None, True)`.
  - All other refs go to `base.display(session, others)` in one call, only when non-empty.
    Merge back by input position (refs may repeat).
- `src/telescreen/wiring_v14.py`: change only the `AssetQueries(...)` construction to pass
  `InventoryActorDirectory(parts.actors, parts.collector_configs)`. No other line changes.

### D2. L1 null timestamps

- `api/types.ts`: `DataEvent.occurred_at: string | null`;
  `DataAsset.last_seen_at: string | null`.
- `DataDetailSections.tsx`: `RelativeTime iso={event.occurred_at ?? event.recorded_at}`.
- `DataList.tsx` and `DataDetail.tsx`: a null `last_seen_at` renders `—`, never
  `new Date(null)`.
- Fix every type error `bun run typecheck` reports inside writePaths. At `3c5c1dd` only
  `api/types.ts`, the v14 mock files and `pages/data/**` read these fields.

### D3. L3 reference select

- In `MetadataForm.tsx`, the reference `<select>`: every member and team `<option>` gets
  `selected={(values()[slot.key] ?? "") === <option value>}`. The empty "Choose…"
  option is selected when the value is `""`.
- When the current value is non-empty and matches no loaded option (after the directory
  has loaded), render one extra first option with that exact value and the label
  "Current value (not in directory)". Do not display the raw ref text.
- Do not change `initialValue`, `typedValue`, validation or the `onSave` payload shape.

### D4. L4 reactive chart

- `ItemCountChart.tsx`: no early return. Use `<Show when={props.points.length > 0}
  fallback={...existing empty div...}>`, with path and viewBox derived inside reactive
  scope (functions or `createMemo`). One point draws a flat line across a minimum width.
  Keep `role="img"`, `aria-label`, the class names and the `H x V y` step format (the
  existing test asserts `H 24 V`).

### D5. L5 separated text

- `CountTabs.tsx`: the label, a space text node (`{" "}`), then the
  `<span class="count-badge">`. Tab `textContent` normalizes to "All 3".
- Timeline row in `DataDetailSections.tsx`: wrap the row in `class="data-timeline-row"`
  with `{" "}` between the avatar, the `<strong>` actor name, the event label and the
  time, so the normalized `textContent` reads "<initials> <name> <label> <time>".
- List name cell in `DataList.tsx`: `<strong>` name, `{" "}`, then the locator in
  `<small class="data-locator">` (CSS `display:block`).
- Breadcrumb in `DataDetail.tsx`: `<nav class="data-breadcrumb" aria-label="Breadcrumb">`
  with `Data`, a `<span aria-hidden="true"> / </span>` separator, then the short id. The
  normalized `textContent` is "Data / DATA-1".
- CSS: in `components/ui/ui.css`, add `.count-tabs`, `.count-tab`, `.count-badge` and
  `.item-count-chart` rules (flex gap, badge padding and radius, chart width 100%). In new
  `pages/data/data.css`, imported by `pages/data/index.tsx`, add `.data-timeline-row`
  (flex, gap, align-items center), `.data-locator` and `.data-breadcrumb` rules. Use only
  the existing tokens from `styles/tokens.css` (for example `--text-secondary`,
  `--bg-tertiary`); no hard-coded colors, so light and dark both work.

### D6. L6 Field schema nav

- `nav.ts`: `entriesFor(area: AreaKey, can: (permission: string) => boolean)`. For `data`,
  append `{ label: "Field schema", href: "/settings/data-fields" }` when
  `can("data_fields.admin")`. Other areas ignore `can`.
- `SecondaryPanel.tsx`: pass `auth.can` to `entriesFor`. Append the same "Field schema"
  entry at the end of the inline settings list under the same check.
- `shell.test.tsx`: update the line-58 assertion to the new signature, with a `can` that
  returns false (same four entries).

### D7. L7 Overdue badge

- `DataList.tsx` State cell: after the state tag, `<Show when={asset.overdue}><Tag
  variant="danger">Overdue</Tag></Show>`, before the Incomplete tag.

### D8. Mock fixtures (design 17.4)

- `api/mock/fixtures/v14.ts`:
  - Events: scan and system kinds (`discovered`, `items_changed`, `values_changed`,
    `deleted`, `restored`, `tracking_stopped`, `tracking_resumed`) get
    `occurred_at: null`; `manual_change`, `registered` and `metadata_changed` keep a value.
    Scan kinds use the actor `{ kind: "system", ref: "collector:col-01", display_name:
    "slack collection" }` (the `col-01` name in `fixtures/operations.ts`); `tracking_*`
    use `{ kind: "system", ref: "system:inventory", display_name: "Telescreen" }`;
    member kinds keep `member:alex`.
  - `data-asset-01` (index 0) gets a non-null `item_count`, so its `item_trend` and mock
    `item-series` have 14 points. Change the null-count rule to another residue (for
    example `index % 3 === 2`) and keep at least one asset with `item_count: null`.
    `data-asset-01` stays overdue with owner `member:alex`.
- `api/mock/v14.ts`: change only if typecheck or the fixture changes require it.
- `api/mock/fixtures.test.ts`: assert that at least one event has `occurred_at === null`;
  one actor ref starts with `collector:`; one is `system:inventory`; the owner field
  `value_json` of `data-asset-01` parses to `member:<handle>` of an active member in
  `fixtures/directory.ts`; and `data-asset-01` has at least 3 trend points.
- Fix `mock.test.ts` only where the fixture changes break an existing assertion. Record
  each change.

### D9. Screenshots

- `scripts/screenshots.ts`: change only if a strict state breaks because of D5 or D8. Never
  remove a state or a theme, and never relax strict mode. Record each change in the
  Progress Log.

## Tests (write first; run against the unfixed code; record the failing output)

Python, `tests/inventory/actors/test_inventory_actors.py` (fake base directory that
records calls, fake `CollectorConfigReader` returning a `CollectorConfig` built like
`tests/inventory/core/test_reconcile.py`):

- `("collector:c1",)` with config `c1` named `inventory-live` -> kind `system`, display
  name `inventory-live`, ref unchanged.
- `("collector:gone",)` -> `Telescreen`, kind `system`.
- `("member:alex", "collector:c1", "system:inventory", "collector:c1")` -> four results in
  input order; the base is called once with `("member:alex", "system:inventory")`; the
  reader is called once for `c1`.
- `()` -> `()` and no base call.
- `("collector:",)` -> delegated to the base (no key).

Python, `tests/e2e/test_flow_v14_live_fixes.py` (production container via `make_world`):

- Create a `custom` collector named `v14-live-actor` with `inventory_mode: "all"`, then push
  one resource to `custom:v14-live-actor` through `/api/ingest/v1/scan` with an ingest
  token -> `GET /api/data/assets?source=custom:v14-live-actor` has one asset;
  `GET /api/data/assets/{id}/events` has a `discovered` event whose actor is
  `{kind: "system", ref: "collector:<collector id>", display_name: "v14-live-actor"}`,
  with `occurred_at` null and `recorded_at` set. On `3c5c1dd` this test fails with
  "Unknown".

Web (vitest):

- L1 (`pages/data/DataDetail.test.tsx`): an event with `occurred_at: null` and
  `recorded_at` set to two hours before now -> the timeline shows "2 hours ago" and no
  text matches `/days? ago/`. `DataList.test.tsx`: a row with `last_seen_at: null` shows
  `—`.
- L3 (new `pages/data/MetadataForm.test.tsx`): `/api/members` resolves only after the
  first render, through a deferred promise. Then the Owner select's selected option has
  value `member:alex`. Submitting unchanged sends
  `{"key":"owner","value_json":"\"member:alex\""}` in the PUT body. A stored
  `member:retired` not in the directory -> the selected option is "Current value (not in
  directory)", and an unchanged submit sends it unchanged.
- L4 (`ItemCountChart.test.tsx`): a signal starts at `[]`, then is set to 3 points -> a
  `path` exists and its `d` has 2 `H` steps; the empty text is gone. One point -> a path,
  not the empty text. `DataDetail.test.tsx`: a 3-point `item-series` -> no "No item count
  history".
- L5: `CountTabs.test.tsx` -> tab `textContent` normalized equals "All 8".
  `DataDetail.test.tsx` -> the breadcrumb normalized text is "Data / DATA-1", and the
  timeline row contains "<display_name> discovered". `DataList.test.tsx` -> the name cell
  contains "<name> <locator>". Normalize with `.replace(/\s+/g, " ").trim()`.
- L6 (`shell.test.tsx`): `entriesFor("data", () => true)` ends with Field schema;
  `entriesFor("data", () => false)` does not. Rendering the shell with permissions that
  include `data_fields.admin` shows the "Field schema" link in the Data panel and on a
  `/settings` route; without that permission, neither.
- L7 (`DataList.test.tsx`): a row with `overdue: true` has "Overdue" in its State cell;
  a row with `overdue: false` does not.

Red evidence: before each fix, run the new or changed test and record the failure (file,
test name, short assertion message) in the Progress Log. These are expected failures:
never list them in the step `verification` (conventions 7a).

## Pitfalls

- Solid component bodies run once. Never read `props.*` outside JSX or a function for
  values that change (this is the L4 bug). The same applies to the select `selected`
  expressions.
- Do not "fix" L2 in the UI or by changing what the observer writes. Existing rows already
  hold `collector:<id>`.
- Do not call `configs.get` once per occurrence; deduplicate ids. Do not raise for an
  unknown id.
- `typelint`: annotate every local (for example `parsed: list[tuple[str, str, str]]`); no
  `dict` locals. Use tuples or lists of pairs for position merging.
- Changing `entriesFor`'s arity breaks every caller: `SecondaryPanel.tsx` and
  `shell.test.tsx` are the only ones (checked with grep at the base).
- Fixture `item_count` changes alter list counts and sparkline data. Run the whole vitest
  suite, not only the data tests.
- Do not change the `count-badge` class or the tab `aria-label`; screenshots use
  `role=tab` names with `exact: false`.
- `data.css` must be imported once (by `pages/data/index.tsx`), not by each page.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/inventory/actors.py src/telescreen/wiring_v14.py tests/inventory/actors tests/e2e/test_flow_v14_live_fixes.py > /tmp/telescreen-v14-p09-format.log 2>&1; echo "exit=$?"
uv run --frozen ruff check --no-cache src/telescreen/inventory/actors.py src/telescreen/wiring_v14.py tests/inventory/actors tests/e2e/test_flow_v14_live_fixes.py > /tmp/telescreen-v14-p09-ruff.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p09-typelint.log 2>&1; echo "exit=$?"
grep -E '^(src/telescreen/inventory/actors.py|src/telescreen/wiring_v14.py|tests/inventory/actors|tests/e2e/test_flow_v14_live_fixes.py)' /tmp/telescreen-v14-p09-typelint.log; test $? -eq 1
uv run --frozen ty check > /tmp/telescreen-v14-p09-ty.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/inventory tests/e2e/test_flow_v14_live_fixes.py tests/e2e/test_wiring_v14.py tests/leak 2>&1 | tee /tmp/telescreen-v14-p09-pytest-scoped.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p09-pytest-all.log 2>&1; echo "exit=$?"
(cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v14-p09-web-install.log 2>&1 && bun run typecheck > /tmp/telescreen-v14-p09-web-typecheck.log 2>&1 && bun run test > /tmp/telescreen-v14-p09-web-test.log 2>&1 && bun run build --outDir /tmp/telescreen-v14-p09-web --emptyOutDir > /tmp/telescreen-v14-p09-web-build.log 2>&1 && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v14-p09-screenshots.log 2>&1); echo "exit=$?"
test "$(grep -c 'state-skipped' /tmp/telescreen-v14-p09-screenshots.log)" -eq 0; echo "exit=$?"
git diff --quiet 3c5c1dd -- src/telescreen/directory src/telescreen/ports src/telescreen/inventory/queries.py src/telescreen/inventory/observer.py src/telescreen/inventory/reconcile.py src/telescreen/wiring.py db pyproject.toml uv.lock src/web/package.json src/web/bun.lock; echo "exit=$?"
test "$(grep -c '^- \[ \]' impl-plans/active/v14-p09-live-fixes.md)" -eq 0; echo "exit=$?"
```

Expected:

- Every command prints `exit=0`, and the typelint grep prints nothing.
- The full pytest passes offline (baseline 1109 passed / 11 skipped, plus the new tests);
  integration and live tests skip and are not listed.
- vitest passes (baseline 256, plus the new tests). The screenshot log has 0 state-skipped
  and 0 browser errors in light and dark.
- The `git diff --quiet` guard shows that no forbidden path changed.

## Completion Criteria

- [ ] D1: `InventoryActorDirectory` and the one-line `wiring_v14.py` change; unit and e2e
  tests pass and were red on the base.
- [ ] D2-D7: each L1 and L3-L7 vitest passes and was red on the base (Progress Log).
- [ ] D8: fixture states asserted in `fixtures.test.ts`; `tests/leak/test_ui_fixtures.py`
  passes.
- [ ] Every Verification command exits 0; Progress Log has pre/post SHA-256 per file,
  each command with its exit status and log path, and the red evidence per defect.

## Progress Log

(append one entry per session, conventions section 8)
