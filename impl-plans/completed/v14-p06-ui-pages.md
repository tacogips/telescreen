# V14-P06 UI Pages: Data List, Detail, Registration, Field Schema Editor, Issue Link, Dashboard Tile, Collector Mode

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 7.2, 9, 9.1, 10; design-docs/specs/design-ui-sentry.md sections 1-7 and 12
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p06-ui-pages`
- planPath: `impl-plans/active/v14-p06-ui-pages.md`
- wave: 2
- dependsOn: `v14-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v14-p06-ui-pages.md`
  - `src/web/src/pages/data`
  - `src/web/src/pages/settings/index.tsx`
  - `src/web/src/pages/settings/index.test.tsx`
  - `src/web/src/pages/settings/DataFields.tsx`
  - `src/web/src/pages/settings/DataFields.test.tsx`
  - `src/web/src/pages/issues/detail/IssueDetailSidebar.tsx`
  - `src/web/src/pages/issues/IssueDetail.test.tsx`
  - `src/web/src/pages/dashboard/index.tsx`
  - `src/web/src/pages/dashboard/index.test.tsx`
  - `src/web/src/pages/collectors/index.tsx`
  - `src/web/src/pages/collectors/index.test.tsx`
- sharedPaths: none
- artifactRoots: none

The `pages/data/index.tsx` stub was created by p03 in wave 1. It lies inside this plan's
`src/web/src/pages/data` write path, and this plan replaces it as an explicit wave
handoff. Read `impl-plans/conventions.md` first.

## Intent and Context

Build every v1.4 page against the p03 `dataApi`, types and mock (`VITE_TELESCREEN_MOCK=1`).
The pages must render the accessible names that the p03 screenshot states target.

Imitate:

- `pages/alerts/index.tsx` (list, `/new` editor in a `Panel`, detail with breadcrumb and
  tablist, `useAuth().can(...)` gating);
- `pages/issues/IssueListControls.tsx` (filter bar and `TopBar.Slot`);
- `pages/issues/IssueListRows.tsx` (`Sparkline` in a table cell);
- `pages/issues/detail/IssueDetailSidebar.tsx:147-195` (timeline with `Avatar` and
  `RelativeTime`);
- `pages/settings/index.tsx` (`SettingsNavigation` links and `<Match when={params.id ===
  ...}>`);
- `pages/settings/OwnershipRules.tsx` (ordered table with move up and down, plus a
  drawer);
- `pages/collectors/index.tsx` (drawer form);
- `components/ui` (`Table`, `PageFilterBar`, `Select`, `Panel`, `Drawer`, `Tag`,
  `EmptyState`, `Pagination`, `Timeline`, `FoldSection`);
- the p03 components `CountTabs`, `ItemCountChart` and `Sparkline`.

## Non-goals

- No edits to `src/web/src/api/**`, `components/**`, `routes.tsx` or `scripts/**` (p03 and
  p08 own them). If a client method or type is missing, write a Reconciliation request.
- No new dependency. Charts use `ItemCountChart` and `Sparkline` only.

## Invariants

- Pages display only API data; nothing is invented (design-ui-sentry section 8 rule).
- Write actions are hidden without `data.write`; the field schema link and page require
  `data_fields.admin`; data pages need `data.read`.
- Errors render through the existing `ErrorBanner`. 422 messages are static per code and
  never echo input.

## Deliverables

1. `pages/data/index.tsx` routes by `useParams`: no id -> list; `new` -> registration;
   otherwise -> detail. Split into files under `pages/data/`:
   - `DataList.tsx`: `CountTabs` "All", "Present", "Deleted", "Incomplete", "Overdue"
     with counts from `dataApi.counts`, synced to `?tab=`.
     - Filter bar: search box, `Select`s for source, kind, state and origin, an owner
       select from the members list, and field chips (`key:value`).
     - Table columns: name with locator, source, state `Tag`, item count with
       `Sparkline(item_trend)`, open issues, owner `Avatar`, deletion due (danger style
       when `overdue`), last seen. Rows link to `/data/:id`.
     - "Load more" uses `next_cursor`.
     - Buttons "Export CSV" (downloads the `dataApi.exportCsv` blob as
       `telescreen-data-<date>.csv`) and "Register data" (`data.write`).
   - `DataDetail.tsx`:
     - header: short id, name, state `Tag`, origin, kind, source, locator;
     - actions (`data.write`): "Edit" (name for registered assets only, kind), "Record
       event" (drawer: occurred at, items added, items removed, item count after, note),
       and "Mark deleted" or "Restore", depending on the state;
     - an incomplete banner listing `missing_required`;
     - `MetadataForm`;
     - "Timeline" from `dataApi.events`, with "Load more";
     - `ItemCountChart` from `itemSeries(id, 90)`;
     - an items section with tabs "Items" and "Values" and a present/removed select. The
       table shows key prefix, first seen and removed at;
     - related issues as `TELE-n` links to `/issues/:id`.
   - `MetadataForm.tsx`: one input per `field_type`:
     - text input, textarea, number, decimal text, checkbox, date input;
     - select and multi-select from `options`;
     - reference picker (members and teams from the directory API);
     - URL input.

     Required fields carry a marker. On save it calls `dataApi.validateValues`; when that
     returns `valid: false`, show the per-field codes as messages and do not submit.
     Otherwise it calls `putFields`. `value_json` is built with `JSON.stringify` of the
     typed value (decimal as a string, multi-select as an array, a cleared field as
     `null`).
   - `DataRegister.tsx`: name, source, locator, kind, item count, and `MetadataForm` in
     create mode. On success it navigates to the new detail; on 409 it shows "Already
     registered".
2. `pages/settings/DataFields.tsx`, at `/settings/data-fields`:
   - a heading "Field schema" and a button "New field";
   - an ordered table (label, key, type, required, role, values, archived) with move up
     and down, which calls `reorderFields` with the full non-archived id list;
   - a drawer editor:
     - key, editable only on create, pattern hint `^[a-z][a-z0-9_]{0,47}$`;
     - label, description;
     - type select, disabled when `value_count > 0`;
     - an options editor (value and label rows) for select types;
     - a required toggle and a role select (with compatible types only);
   - "Archive", "Unarchive", and "Delete" (disabled when `value_count > 0`).

   409 `field_in_use` and `conflict` show static messages.

   `pages/settings/index.tsx` adds the "Field schema" link, gated by
   `can("data_fields.admin")`, and the `Match` for `data-fields`.
3. `IssueDetailSidebar.tsx`: a section "Data asset". When the user `can("data.read")`, it
   calls `dataApi.resourceAsset(issue.resource_key)` and renders a link named with the
   asset's `short_id` (for example "DATA-1") to `/data/:id`. Nothing renders when the
   asset is null or the call fails with 403.
4. `pages/dashboard/index.tsx`: an "Overdue data" tile with `counts.overdue` from
   `dataApi.counts(new URLSearchParams())`, linking to `/data?tab=overdue`. The tile is
   hidden when the user lacks `data.read` or the call returns 403.
5. `pages/collectors/index.tsx`: the drawer form gains `<select aria-label="Inventory
   mode">` with the options "Sensitive only" (`sensitive_only`), "All resources" (`all`)
   and "Off" (`off`). `bodyFor` sends `inventory_mode`, and the list shows the mode in a
   column. Choosing "Off" for an existing collector asks for confirmation ("Assets of this
   collector become unknown").

## Binding accessible names (p03 screenshot states)

- tabs "Overdue" and "Incomplete" on `/data`;
- tab "Values" and button "Record event" on `/data/data-asset-01`;
- button "New field" on `/settings/data-fields`;
- the link with the asset short id on the paired issue detail;
- the select "Inventory mode" inside the drawer opened by "Connect source".

## Pitfalls

- Use `<Show keyed>` around `ErrorBanner`, as the v1.3 pages do, so errors re-render.
- The register page is `/data/new`, which the router also matches as `:id`. Check for
  `new` before treating the param as an id.
- The CSV download needs an object URL plus a temporary `<a download>`. Revoke the URL
  afterwards. Do not navigate to `/api/data/export` directly, because the auth header
  would be missing.
- Do not trust client-side validation alone. Always render server errors.
- Keep each file reasonably small. Split `DataDetail` sections into
  `DataDetailSections.tsx` if it passes about 400 lines.

## Tests (vitest with the mock client, imitating `pages/alerts` tests)

- `pages/data/DataList.test.tsx`:
  - tabs show the counts;
  - clicking "Overdue" filters to overdue rows;
  - a search narrows the rows;
  - "Export CSV" calls `exportCsv`;
  - a viewer does not see "Register data".
- `pages/data/DataDetail.test.tsx`:
  - the timeline renders the event kinds;
  - "Values" shows the value prefixes;
  - "Record event" submits the body;
  - "Mark deleted" turns into "Restore";
  - an incomplete banner lists the missing keys;
  - saving with a required field empty shows the per-field error and makes no `PUT`.
- `pages/data/DataRegister.test.tsx`: the required fields block the submit; success
  navigates to the detail.
- `pages/settings/DataFields.test.tsx`:
  - create a select field with options;
  - the type select is disabled when values exist;
  - move up calls reorder with the full id order;
  - delete is disabled when values exist.
- `pages/settings/index.test.tsx`: the "Field schema" link is shown for admin only.
- `pages/issues/IssueDetail.test.tsx`: the asset link appears for the paired issue and
  does not appear for another issue.
- `pages/dashboard/index.test.tsx`: the overdue tile shows the count, and is hidden on 403.
- `pages/collectors/index.test.tsx`: the inventory mode select sends `inventory_mode` on
  update.

## Verification

Start every shell with `set -o pipefail`.

```bash
(cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v14-p06-install.log 2>&1); echo "exit=$?"
(cd src/web && bun run typecheck > /tmp/telescreen-v14-p06-typecheck.log 2>&1); echo "exit=$?"
(cd src/web && bun run test > /tmp/telescreen-v14-p06-vitest.log 2>&1); echo "exit=$?"
(cd src/web && bun run build --outDir /tmp/telescreen-v14-p06-web --emptyOutDir > /tmp/telescreen-v14-p06-build.log 2>&1); echo "exit=$?"
(cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v14-p06-screenshots.log 2>&1); echo "exit=$?"
test "$(grep -c 'state-skipped' /tmp/telescreen-v14-p06-screenshots.log)" -eq 0; echo "exit=$?"
```

Expected:

- every command exits 0;
- strict screenshots report zero browser errors;
- the skipped-state count is 0, which proves that every binding name of p03's
  `v14States` and every v1.3 state renders.

## Completion Criteria

- [x] All five deliverables are done, rendering the binding names.
- [x] Every test above is added and passing.
- [x] typecheck, vitest, build and strict screenshots pass with 0 skipped states and 0
  browser errors.
- [x] The Progress Log has hashes, and every command with its exit status and log path.
- [x] INT-P06-01 keeps CountTabs and filters mounted during refetches; the regression proves
  search focus and input identity persist, and tabs remain mounted during search and tab reloads.

## Progress Log

Implementation completed on the shared `feat/data-inventory` working tree. Replaced the
p03 Data placeholder with list, registration, detail, metadata values, timeline, item
history, chart and related issue views. Added the admin field schema editor, issue asset
link, dashboard overdue tile and collector inventory mode. Write actions use the
assigned permissions; errors use static code messages and server error banners. No API,
component, route, screenshot script, or dependency files were changed by p06.

Final-source hashes (SHA-256; modified files are before -> after; new files are after):

- `src/web/src/pages/data/index.tsx`: `050c18052726a3f40d1584bdd1003e78bebc3250d8f2b20c18eee63a949a9aae` -> `55be4d421f38e3ed6bdad2ce495c97e5e92d47a647160f59fd63cb8124868cc8`
- `src/web/src/pages/data/DataList.tsx`: `8f74360d554b000e78582f19fd36b62af74006a1972030e27321e1df70417d75`
- `src/web/src/pages/data/DataList.test.tsx`: `78bf2a71d6ca594cb5a3ebd0b2b279d78a3f235fc7510aeaefb07eaf0bdacc2c`
- `src/web/src/pages/data/MetadataForm.tsx`: `b8102a2baea9638fc8731e8c3678db76eac291526421d3083e01f7ec070e3749`
- `src/web/src/pages/data/DataRegister.tsx`: `c43d8917da3984020f4a1151394199244df60571e9e5b6a4a00f554045fb211c`
- `src/web/src/pages/data/DataRegister.test.tsx`: `6cc9d767620b2aa676c697b92bee2388dd7894ecab73e54de12e1e1f36efcb2d`
- `src/web/src/pages/data/DataDetail.tsx`: `a759396b52f530c7d58fb64182a5b28f1c5fbb26dbabbe850c52654d929fbc6e` -> `4420a245406b5bf1afa6be499fa318b35e2034211cccaf411859b42d20c91f51`
- `src/web/src/pages/data/DataDetail.test.tsx`: `52def5d4abbe26fabdd922de2e91ad1e27cd9d8eb2c2bd0257dfd6c96b67fc57` -> `6211e0fdb4e314ae3ea25101c36b6625d3810b2918e09312a3d65ccd9955c57`
- `src/web/src/pages/data/DataDetailSections.tsx`: `80974f953d3c9d8447fc85ee1e1dd6ac1255e00233882ad26b4e54b174c6c832`
- `src/web/src/pages/settings/index.tsx`: `ea3f9f59616459949e47b233f90c7abdac7c79f3566e7262cfb6055cb4eb76d7` -> `220009ab47f75132c489fc5e70e978daa90e3e9e66b5f820e578a877f31d6543`
- `src/web/src/pages/settings/index.test.tsx`: `601d0d93da940770e380956ceda5a88c9d459f35e740a63ad0ee1e944ec540b9` -> `5d76ef04b1fb4e43ed0088b96d9ce9691f95f11940b1c28515da827f37849479`
- `src/web/src/pages/settings/DataFields.tsx`: `6820ed82e73aec2bc2fba9833a1c2e726932ee2bb24b30ae40d5b081bf9dc990`
- `src/web/src/pages/settings/DataFields.test.tsx`: `206271e39c5a1af848b706877bf32e8d3ff0d364446b41c0971ead28d41e7958`
- `src/web/src/pages/issues/detail/IssueDetailSidebar.tsx`: `18c5fc3b68079dd63b6375008fb5015562e7e31a1091403842604dfbb8b4595a` -> `dd382624debdd68d791476b2a088c9c1817a3e00ddc6e43b5d707873dc096d7c`
- `src/web/src/pages/issues/IssueDetail.test.tsx`: `f11c1acff80c63072c7ff15dadd4bb7a9ffbd13bfd5b73a3d532055d3a3be611` -> `77af90f3895c6cd9918b454cc0aebc476d9182c0e91f22eb89baed24a35e4806`
- `src/web/src/pages/dashboard/index.tsx`: `500772602d6f261b4d58da962e8347665d1b58cbbd3ee5b4249b55049094b8f8` -> `27030c7b97c7065f3a17bc89e5034bbb964a08dd42cdeb5c270cc2e06068e161`
- `src/web/src/pages/dashboard/index.test.tsx`: `d4e157c8ff4ac6d8225eb1914c16080841a93a88d53821a8f6c1188163257eb1` -> `9efd0e4c76bdc4626794e185e3a860bddd15b4e619b381cb464ec3bc7eca842c`
- `src/web/src/pages/collectors/index.tsx`: `61f8a0c7e1b339313240137498f2d6df23c19da84a975653a0a8ebcc5d8bdf09` -> `cae0a2c8ccb391a6ee49af2a05f6536064bcad16ae2c5ff79bf8f879f995d385`
- `src/web/src/pages/collectors/index.test.tsx`: `11f6104b4ad7f205db24b39cabea1164ab7588516553c4a8a7dcc2bb60db0515` -> `447527f47d50b485f29206204de759221329da8d0cf1df8d9ef92bff95253f55`

Verification (all on the final p06 source; full logs at the listed paths):

- `bun install --frozen-lockfile` in `src/web`: exit 0, `/tmp/telescreen-v14-p06-install.log`.
- `bun run typecheck` in `src/web`: exit 0, `/tmp/telescreen-v14-p06-typecheck.log`.
- Focused p06 Vitest suite: exit 0, 8 files and 47 tests passed, `/tmp/telescreen-v14-p06-focused.log`.
- `bun run test` in `src/web`: exit 0, 48 files and 254 tests passed, `/tmp/telescreen-v14-p06-vitest.log`.
- `bun run build --outDir /tmp/telescreen-v14-p06-web --emptyOutDir` in `src/web`: exit 0, `/tmp/telescreen-v14-p06-build.log`.
- `TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` in `src/web`: exit 0, `/tmp/telescreen-v14-p06-screenshots.log`; state-skipped count 0 and browser-error match count 0.
- Screenshot gate `test "$(grep -c 'state-skipped' /tmp/telescreen-v14-p06-screenshots.log)" -eq 0`: exit 0.

TI-P06-01 repair: added the missing `DataDetail.test.tsx` case using stateful detail
responses and the `POST /api/data/assets/data-asset-01/mark-deleted` and
`POST /api/data/assets/data-asset-01/restore` endpoints. It asserts both requests and
the `Mark deleted` -> `Restore` -> `Mark deleted` action transitions. Existing cases
were preserved. Test SHA-256: `52def5d4abbe26fabdd922de2e91ad1e27cd9d8eb2c2bd0257dfd6c96b67fc57`
-> `a849b806bfacc8b7ea71d94399e25a3dd3da9745bab8b9f8e8685891c572fce9`.

- `set -o pipefail; (cd src/web && bunx vitest run src/pages/data/DataDetail.test.tsx > /tmp/telescreen-v14-p06-detail.log 2>&1)`: exit 0, 3 tests passed; complete log `/tmp/telescreen-v14-p06-detail.log`.
- `set -o pipefail; (cd src/web && bun run typecheck > /tmp/telescreen-v14-p06-typecheck-repair.log 2>&1)`: exit 0; complete log `/tmp/telescreen-v14-p06-typecheck-repair.log`.
- `set -o pipefail; (cd src/web && bun run test > /tmp/telescreen-v14-p06-vitest-repair.log 2>&1)`: exit 0, 48 files and 255 tests passed; complete log `/tmp/telescreen-v14-p06-vitest-repair.log`.

ADV-P06-01 repair: `DataDetail.tsx` now formats the Record event default and reset with
`localInputValue(new Date())`, converting the instant to local wall-clock text for the
`datetime-local` input. `saveEvent` still converts the entered local value to an ISO UTC
instant. The existing manual-event test now parses the POST body and asserts the
submitted `occurred_at` differs from `Date.now()` by less than 120,000 ms. The existing
Mark deleted/Restore test and assertions were preserved. SHA-256: `DataDetail.tsx`
`a759396b52f530c7d58fb64182a5b28f1c5fbb26dbabbe850c52654d929fbc6e` ->
`4420a245406b5bf1afa6be499fa318b35e2034211cccaf411859b42d20c91f51`; `DataDetail.test.tsx`
`a849b806bfacc8b7ea71d94399e25a3dd3da9745bab8b9f8e8685891c572fce9` ->
`6211e0fdb4e314ae3ea25101c36b6625d3810b2918e09312a3d65ccd9955c57`.

Current-source verification for ADV-P06-01 (complete logs):

- `set -o pipefail; (cd src/web && TZ=Asia/Tokyo bunx vitest run src/pages/data/DataDetail.test.tsx > /tmp/telescreen-v14-p06-detail-tz-tokyo-adv-repair.log 2>&1)`: exit 0, 3/3 tests passed.
- `set -o pipefail; (cd src/web && TZ=America/New_York bunx vitest run src/pages/data/DataDetail.test.tsx > /tmp/telescreen-v14-p06-detail-tz-ny-adv-repair.log 2>&1)`: exit 0, 3/3 tests passed.
- `set -o pipefail; (cd src/web && bun run typecheck > /tmp/telescreen-v14-p06-typecheck-adv-repair.log 2>&1)`: exit 0.
- `set -o pipefail; (cd src/web && bun run test > /tmp/telescreen-v14-p06-vitest-adv-repair.log 2>&1)`: exit 0, 48 files and 255/255 tests passed.
- `set -o pipefail; (cd src/web && bun run build --outDir /tmp/telescreen-v14-p06-web-adv-repair --emptyOutDir > /tmp/telescreen-v14-p06-build-adv-repair.log 2>&1)`: exit 0.
- `set -o pipefail; (cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v14-p06-screenshots-adv-repair.log 2>&1)`: exit 0; screenshot log `/tmp/telescreen-v14-p06-screenshots-adv-repair.log`.
- `set -o pipefail; test "$(grep -c 'state-skipped' /tmp/telescreen-v14-p06-screenshots-adv-repair.log)" -eq 0`: exit 0; zero state-skipped lines.
- `set -o pipefail; ! grep -Eiq 'pageerror|console error|browser error' /tmp/telescreen-v14-p06-screenshots-adv-repair.log`: exit 0; no browser-error matches.
- `set -o pipefail; test "$(grep -c '^- \[ \]' impl-plans/active/v14-p06-ui-pages.md)" -eq 0`: exit 0; zero unchecked criteria; plan is 291 lines.

Independent review and serial integration/finalization remain downstream workflow work.

INT-P06-01 repair: moved CountTabs and the filter bar outside the loading gate in
`DataList.tsx`; only the result region now shows the loading placeholder. Counts and
Load more use the latest resolved resource value. Added a deferred-fetch regression in
`DataList.test.tsx` that asserts search input identity/focus through an in-flight query
and after resolution, and asserts the tablist remains mounted during an Overdue reload.
The existing search/export tests remain unchanged. SHA-256: `DataList.tsx`
`8f74360d554b000e78582f19fd36b62af74006a1972030e27321e1df70417d75` ->
`9d7f6b687229392ff9251e482fa7956fb54623693673b09e2ee0392dbb4eccf4`; `DataList.test.tsx`
`78bf2a71d6ca594cb5a3ebd0b2b279d78a3f235fc7510aeaefb07eaf0bdacc2c` ->
`3237c1a54b4349d483d5ba02496c32c51845e8bd7e4dc9570487ca1689176d48`.

Final-source verification (complete logs):

- `set -o pipefail; (cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v14-p06-install-int-p06-01.log 2>&1)`: exit 0.
- `set -o pipefail; (cd src/web && bunx vitest run src/pages/data/DataList.test.tsx > /tmp/telescreen-v14-p06-int-p06-01-focused-rerun.log 2>&1)`: exit 0, 1 file and 3 tests passed.
- `set -o pipefail; (cd src/web && bun run typecheck > /tmp/telescreen-v14-p06-typecheck-int-p06-01.log 2>&1)`: exit 0.
- `set -o pipefail; (cd src/web && bun run test > /tmp/telescreen-v14-p06-vitest-int-p06-01.log 2>&1)`: exit 0, 48 files and 256 tests passed.
- `set -o pipefail; (cd src/web && bun run build --outDir /tmp/telescreen-v14-p06-int-p06-01-web --emptyOutDir > /tmp/telescreen-v14-p06-build-int-p06-01.log 2>&1)`: exit 0.
- `set -o pipefail; (cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v14-p06-screenshots-int-p06-01.log 2>&1)`: exit 0; zero state-skipped lines and zero browser error lines. Overdue and Incomplete captures show CountTabs and the full filter bar mounted while the result region is loading.
- `set -o pipefail; uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py > /tmp/telescreen-v14-p06-leak-int-p06-01.log 2>&1`: exit 0, 1 test passed.

An initial focused run exited 1 because the regression harness matched its first deferred
response again after URL parameters merged. The harness now distinguishes the search
request from the Overdue request; the passing focused and full Vitest runs above are on
the corrected source. Independent review and serial integration/finalization remain
downstream workflow work.
