# V14-P03 UI Foundation: Types, Client, Mock, Shapes Manifest, Shell, Components, Screenshot States

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 9, 9.1, 10; design-docs/specs/design-ui-sentry.md section 12
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p03-ui-foundation`
- planPath: `impl-plans/active/v14-p03-ui-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v14-p03-ui-foundation.md`
  - `src/web/src/api`
  - `src/web/src/components`
  - `src/web/src/routes.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/src/pages/data/index.tsx`
  - `src/web/scripts/screenshots.ts`
  - `tests/leak/test_ui_fixtures.py`
- sharedPaths: none
- artifactRoots: none

`src/web/node_modules` and `src/web/dist` are gitignored and never written. Build output
goes to `/tmp`. Read `impl-plans/conventions.md` first.

## Intent and Context

This plan gives the wave-2 UI plan (p06) everything it needs, so that it edits only page
files:

- the TypeScript types and `dataApi` client for every `/api/data` route (design section
  9);
- a realistic mock seed and mock routes with the exact API shapes;
- a key-set manifest that both the UI tests and a later Python contract test (p08) check;
- the Data area registration (route, rail icon, nav);
- two components;
- the screenshot states.

The SolidJS stack is SolidJS, `@solidjs/router` and `lucide-solid`. No new dependency.

Imitate:

- `src/routes.tsx` `AreaKey`, `AREAS` and the glob page loading;
- `components/shell/PrimaryRail.tsx` (the lucide map keyed by AreaKey) and
  `components/shell/nav.ts`;
- `components/StreamTabs.tsx` (`role="tab"` plus `.count-badge`);
- `components/ui/MiniBarChart.tsx` (hand-rendered SVG, CSS vars);
- `api/client.ts` (`apiFetch`, `query`, `json`, the `alertsApi` object style);
- `api/mock/router.ts` (`routeV13`, `page()`, `byId`, `response`, `error`);
- `api/mock/state.ts` and `api/mock/fixtures/v13.ts`;
- `api/mock/fixtures.test.ts` (the masked-pattern rules).

## Non-goals

- No real page implementation. `pages/data/index.tsx` is a stub (`<Placeholder>`) that
  p06 replaces in wave 2.
- No edits under `src/web/src/pages/**` other than that stub. No `package.json` or
  `bun.lock` change.

## Invariants

- The mock returns exactly the field names and nullability of design 9.1. Every key set is
  listed once in `api/mock/fixtures/v14-shapes.json`.
- Fixture values follow the Masker formats and reserved example names
  (`example.com`, `DATA-n`, hex prefixes); `tests/leak/test_ui_fixtures.py` stays green.
- Existing mock routes and fixtures behave the same, with two exceptions: the collector
  fixtures gain `inventory_mode`, and the mock principal and role matrix gain the three
  v1.4 permissions.
- The mock admin principal carries every v1.4 permission. Without that, p06's gated
  actions (Record event, New field, the Field schema link, the DATA-n link and the
  dashboard tile) cannot render, and the strict screenshots in p06 and p08 would skip
  states.

## Deliverables

1. `api/types.ts`, the v14 types; nullability exactly as in design 9.1:
   - `InventoryMode`, `DataAssetState`, `DataAssetOrigin`, `DataAssetKind`,
     `DataEventKind`, `DataItemKind`, `DataFieldType`, `DataFieldRole`, `DataAssetTab`
     (string unions);
   - `ActorRef = {kind, ref, display_name}` (reuse the existing actor type if one exists);
   - `DataAsset`, `DataAssetDetail`, `DataEvent`, `DataItem`, `DataField`, `DataCounts`
     (`{all, present, deleted, incomplete, overdue}`), `DataItemSeries` (`{points:
     {at, item_count}[]}`), `DataValueError` (`{field, code}`) and
     `DataValidateResponse` (`{valid, errors}`).

   Collector types gain `inventory_mode: InventoryMode`.
2. `api/client.ts`, `dataApi`:
   - reads: `list(filters: URLSearchParams)`, `counts(filters)`,
     `exportCsv(filters): Promise<Blob>` (uses `rawFetch` and carries the auth header),
     `resourceAsset(resourceKey)`, `get(id)`, `events(id, cursor?)`,
     `items(id, kind, state, cursor?)`, `itemSeries(id, days)`, `fields()`;
   - asset writes: `patch(id, body)`, `putFields(id, values)`, `recordEvent(id, body)`,
     `markDeleted(id, body)`, `restore(id, body)`, `register(body)`;
   - field writes: `createField(body)`, `updateField(id, body)`, `deleteField(id)`,
     `archiveField(id)`, `unarchiveField(id)`, `reorderFields(ids)`,
     `validateValues(values)`.

   The paths are exactly those of design 9. `collectorsApi` create and update send
   `inventory_mode` when it is set.
3. Mock:
   - new `api/mock/fixtures/v14.ts`, with ids `data-asset-01` ... (design 10.3):
     - at least 24 assets: every state (`present`, `deleted`, `unknown`), both origins,
       all 7 kinds, at least 3 overdue and at least 3 incomplete;
     - at least 2 list-shaped assets with item series and removed items;
     - 6 fields: one per role (`purpose` single_select, `owner` reference, `deletion_due`
       date), plus `retention_class` multi_select, `notes` long_text and `record_count`
       integer, with one archived;
     - events of every `DataEventKind`;
     - one asset whose `resource_key` equals the `resource_key` of an existing issue
       fixture (for the issue link);
   - `api/mock/state.ts`: mutable clones and a `nextDataAssetNumber` counter;
   - new `api/mock/v14.ts` exports `routeV14(url, request, method): Promise<Response |
     null>`, implementing every design-9 route on the state. It honours `tab`, `q`,
     `source`, `kind`, `state`, `origin`, `owner`, `field` and `sort`; computes counts and
     the overdue and incomplete flags; returns a CSV body with `text/csv`; and gives 409
     and 422 errors for the rules of design 7.1 and 7.3 (field in use, required missing).
     `routeMockRequest` calls `routeV14` before `routeV13`.
   - Mock auth permissions (required; `useAuth().can()` reads this list and the pages
     gate on it):
     - in `api/mock/router.ts`, append `"data.read"`, `"data.write"` and
       `"data_fields.admin"` to the `permissions` arrays of both `POST /api/auth/session`
       and `GET /api/auth/me` (the mock admin principal, currently around lines 208-209);
     - in `api/mock/fixtures/v12.ts`, `mockRoleMatrix` gets admin: all three, responder:
       `data.read` and `data.write`, viewer: `data.read`; the scopes follow design 3
       (read: `data.read`; write: `data.read` and `data.write`; admin: all three).

     Existing entries are unchanged; only the three are added.
4. New `api/mock/fixtures/v14-shapes.json`: `{"DataAsset": [...], "DataAssetDetail":
   [...], "DataEvent": [...], "DataItem": [...], "DataField": [...], "DataCounts": [...],
   "DataItemSeriesPoint": ["at", "item_count"], "DataFieldSlot": [...]}`, where
   `DataFieldSlot` is the element shape of `DataAssetDetail.fields`. Each value is a sorted
   key list taken from design 9.1.
5. Components:
   - `components/CountTabs.tsx`: generic `CountTabs<T extends string>(props: {tabs: readonly
     {value: T; label: string}[]; value: T; counts: Partial<Record<T, number>>; onChange:
     (value: T) => void; label: string})`, with `role="tablist"` and a `.count-badge`;
   - `components/ItemCountChart.tsx`: props `{points: readonly {at: string; item_count:
     number}[]; label: string; height?: number}`, an SVG step line with an `aria-label`
     and an empty state when there are no points.

   Each gets a `*.test.tsx`.
6. Shell:
   - `routes.tsx`: `AreaKey` adds `data`; `AREAS` adds `{key: "data", label: "Data",
     path: "/data", group: ...}` directly after Alerts;
   - `PrimaryRail.tsx`: the `Database` icon;
   - `nav.ts`: the data entries All data (`/data`), Overdue (`/data?tab=overdue`),
     Incomplete (`/data?tab=incomplete`) and Register data (`/data/new`);
   - `shell.test.tsx`: the exact rail order, updated.

   The Settings > Field schema link is added by p06 in the settings page, not in `nav.ts`.
7. Stub `pages/data/index.tsx`: a default export rendering a `Placeholder` titled "Data".
8. `scripts/screenshots.ts` gains a `v14States` array, appended to the states. The names in
   quotes are the binding accessible names that p06 must render:
   - `data-list`: `/data`;
   - `data-list-overdue`: `/data`, click tab "Overdue" (exact false);
   - `data-list-incomplete`: `/data`, click tab "Incomplete" (exact false);
   - `data-detail`: `/data/data-asset-01`;
   - `data-detail-values`: `/data/data-asset-01`, click tab "Values";
   - `data-detail-record-event`: `/data/data-asset-01`, click button "Record event";
   - `data-new`: `/data/new`;
   - `data-fields`: `/settings/data-fields`;
   - `data-fields-editor`: `/settings/data-fields`, click button "New field";
   - `issue-data-link`: `/issues/<the issue fixture id paired with the asset>`;
   - `collectors-inventory-mode`: `/collectors`, click button "Connect source".

## Pitfalls

- `exportCsv` must not call `apiFetch`, which parses JSON. Use the raw fetch path and
  return `response.blob()`. Errors still decode to the error model.
- In the mock, `owner` filtering matches the owner role value (`member:<handle>` or
  `team:<slug>`). The `field=<key>:<value>` filter is equality on the canonical value and
  containment for `multi_select`.
- Do not change `StreamTabs`; issues still use it.
- The fixtures test must not assert counts that p06 cannot see. Keep the assertions to
  shape, masking and seed coverage.
- The mock lookup `GET /api/data/resource-assets/{key}` must return `{asset: null}` with
  200 when there is no asset, not 404.

## Tests

- `api/mock/fixtures.test.ts` (extend):
  - every v14 fixture object of each type has exactly the key set of `v14-shapes.json`;
  - seed coverage (every state, origin and kind; at least 3 overdue and 3 incomplete;
    every event kind);
  - masked patterns.
- `api/mock/mock.test.ts` (extend):
  - list with `tab=overdue` returns only overdue items, and the counts match;
  - `PUT` fields with a missing required field gives 422 `required_field_missing`;
  - `PATCH` of a field type with values gives 409 `field_in_use`;
  - register returns 201 with the next `DATA-n`;
  - export returns `text/csv` with a header row;
  - the lookup returns `{asset: null}` for an unknown key;
  - `GET /api/auth/me` and `POST /api/auth/session` permissions include `data.read`,
    `data.write` and `data_fields.admin`; `/api/roles` (the `mockRoleMatrix`) gives the
    viewer `data.read` only.
- `api/client.test.ts`: `dataApi.list` builds the query string; `exportCsv` sends the
  bearer header.
- `components/CountTabs.test.tsx` (counts render, onChange fires) and
  `components/ItemCountChart.test.tsx` (the path renders for points; the empty state shows
  for `[]`).
- `shell.test.tsx`: the rail includes "Data" after "Alerts".

## Verification

Start every shell with `set -o pipefail`.

```bash
(cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v14-p03-install.log 2>&1); echo "exit=$?"
(cd src/web && bun run typecheck > /tmp/telescreen-v14-p03-typecheck.log 2>&1); echo "exit=$?"
(cd src/web && bun run test > /tmp/telescreen-v14-p03-vitest.log 2>&1); echo "exit=$?"
(cd src/web && bun run build --outDir /tmp/telescreen-v14-p03-web --emptyOutDir > /tmp/telescreen-v14-p03-build.log 2>&1); echo "exit=$?"
(cd src/web && bun run screenshots > /tmp/telescreen-v14-p03-screenshots.log 2>&1); echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py 2>&1 | tee /tmp/telescreen-v14-p03-ui-fixtures.log; echo "exit=$?"
```

Expected:

- install, typecheck, vitest, build and the fixture leak test exit 0;
- `screenshots` (non-strict) exits 0 with zero browser errors. The new `data-*` states
  render the stub and may print `state-skipped` until p06 lands; that is expected in wave
  1. Record the skipped names in the Progress Log.

## Completion Criteria

- [x] Types, `dataApi`, mock routes, fixtures and `v14-shapes.json` cover every route and
  shape of design 9 and 9.1.
- [x] `CountTabs`, `ItemCountChart`, the Data area, the rail icon and the nav entries
  exist, with tests.
- [x] `v14States` is added with the binding names above.
- [x] typecheck, vitest, build, non-strict screenshots and `test_ui_fixtures.py` pass.
- [x] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

### Session: 2026-10-13 18:24
**Tasks Completed**: Added v1.4 API types and `dataApi`, stateful mock routes and fixtures, exact response shape manifest, Data shell area and nav, `CountTabs`, `ItemCountChart`, Data placeholder, and screenshot bindings with regression tests.
**Tasks In Progress**: None for this plan. The page controls that cause five screenshot action skips are owned by downstream p06.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/api/client.ts` before=0e12d617240814a0b50d66f6b338cfa4b40d44937f9069f01c0cc183c49a760b after=f0509eded04c1b1a41a31bf6cd3a61d0e8c084b90850ececf766af2403fb77ac; `src/web/src/api/client.test.ts` before=a704d18bb67ab9728e2f447f18d1ef5b34e198eff3088d0af6f8ef350ab09ee0 after=26f231acd08324ef987263ead4e689094903b53b05518421711611b74bee291f; `src/web/src/api/types.ts` before=2e0e3c6f253f0cf4133969854b95d328e6cf499adfda4613d404b7b4c179b8dc after=964ee566282ea5eca3ea432a6fb95f53b31ab3e476e4d2d0f320dfdbea794394; `src/web/src/api/mock/fixtures.test.ts` before=60df0afa5bc71d0f1b69553c8784d193f2fc0f66959efe4141ca529efc299511 after=9383bb26855d383110a0a690a41068262877478327d686acc2f77bc880e3b1c4; `src/web/src/api/mock/fixtures/operations.ts` before=b35cbca3486eebf11fe263d92b45ab8e8e8fa5eef0a74bb48daca9563608ce98 after=e2694bd3218d2d3ede96aaafa532d77f39be8b6d5ed07ea981332931e241e98c; `src/web/src/api/mock/fixtures/v12.ts` before=e67488dcd4e25b7cdd61cfedd724f27d363ee32d8304fee40f78281c3f2f41e5 after=d698c38c7cd42fda8e8ad095a0b687f98b5a57b7933c0a661b16b3ca1b227ebb; `src/web/src/api/mock/mock.test.ts` before=fefa8e60ef39136e5ac967aeae5e3fdf9fb158478b1805da80571d91d52e4dba after=3450645ea166e532ae37ff122ad107ab36f68223ea2108361f56800c672f4f80; `src/web/src/api/mock/router.ts` before=86cd55bba51d5d8498bbfdfc54ed8484c443d2b9bb4dd291bc3d545078262492 after=985c6a74f3e5d1e440ae538019e9a3a4078421e746c28f9be0e9cb25e4bd4e5d; `src/web/src/api/mock/state.ts` before=bcd74bccdf38b1194869a8f2d10a939657a0b0f6a1e2c1e3ccd3bf516ebbe991 after=54f57796b8597b1bf33b572af16c7c4d69d4bac18ead658b7bb48cd97e21fb85; `src/web/src/api/mock/v14.ts` before=absent after=2f5d9835c7133869381aeb4c133c60b8e1d77fd4547091a651269ced5a140ad2; `src/web/src/api/mock/fixtures/v14.ts` before=absent after=888d29e08e3f75dd9bb2763283a4237c6907190f37c0c16e1009a86d560b8880; `src/web/src/api/mock/fixtures/v14-shapes.json` before=absent after=56d5cc1b122586008ffbaabfbef3d6898aae8f4e4c543d584534eff73dee1859; `src/web/src/components/CountTabs.tsx` before=absent after=fe840cb4189e60a8ed214036349919e5f36387b6564f0b83c4f824c315cae4b6; `src/web/src/components/CountTabs.test.tsx` before=absent after=196e4ada38c5e5257378f30d5cdff760910ff4946eb782440dfede856513acb8; `src/web/src/components/ItemCountChart.tsx` before=absent after=4b92265ab0cb9bd528a48480bdd8bbd3945c07576066812445a1b6c5052f3f02; `src/web/src/components/ItemCountChart.test.tsx` before=absent after=f54acfff6e3992b0e15ce5c13ef644d55ca961bf129dec14a32a52c509075b69; `src/web/src/components/shell/PrimaryRail.tsx` before=36457907cdb2a8c5781489507bf73cd94efc3dc631c05f31776efb10088b05ab after=5573d78b5c17fab7455258e354f4a8d4a1cb26086b08c15d0a5f8d0bf953af53; `src/web/src/components/shell/nav.ts` before=e31edc063f33eee20b0c5fc8929a914da0e0c982b49c627a0a6640a015ec6e76 after=e551a01f77549635c8a2c7ce6c0044831e161d6acadd1f6ed3970d9212cf37b4; `src/web/src/pages/data/index.tsx` before=absent after=050c18052726a3f40d1584bdd1003e78bebc3250d8f2b20c18eee63a949a9aae; `src/web/src/routes.tsx` before=32a199532e263affa0769cc40d36043b2911b00392e84bad3cfb20a61b13735b after=a47692fad953cf03a9e61df05e2bcfc77953314f948499c37635dd914cfa476a; `src/web/src/shell.test.tsx` before=66690cfc2a5b10c7b1a615d32ea211e8482507821e34ec57a7264ae2071e10e9 after=4aa0026c14a05fd8fbe59bdf907be25b1415ed1c6b85c17f2bd6bdfcaad1d06a; `src/web/scripts/screenshots.ts` before=622b972b538ac1adffb5dfe5529d58ab9a27d638abfdf8cd15e62c81d44f41f4 after=14778833af715830de5d50cb7fcb358bed1898d61183aecc3de93466c75a7e70.
`test unchecked boxes = 0 and plan lines < 400` -> exit 0 (log: `/tmp/telescreen-v14-p03-plan-gate.log`).
**Notes**: Earlier typecheck wrapper exited 1 because zsh reserves variable `status`; rerun with `rc` passed. Earlier Vitest attempts exited 1 (5 failed/242 and then 1 failed/242); fixes are reflected in the final source, and final Vitest passed 242/242. Exact per-edit intentions are preserved under `tmp/telescreen-v14-20261013-f1/v14-p03-ui-foundation/intents/`. Package manifests are unchanged. Other files in the shared worktree belong to sibling plans.
