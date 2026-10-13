# V14-P06 UI Pages: Data List, Detail, Registration, Field Schema Editor, Issue Link, Dashboard Tile, Collector Mode

**Status**: Ready
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

- [ ] All five deliverables are done, rendering the binding names.
- [ ] Every test above is added and passing.
- [ ] typecheck, vitest, build and strict screenshots pass with 0 skipped states and 0
  browser errors.
- [ ] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

(none yet)
