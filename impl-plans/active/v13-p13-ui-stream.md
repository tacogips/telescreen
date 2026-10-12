# V13-P13 UI Issue Stream: Tabs, Counts, Period, Trend, Values, Search, Server Views, Bulk, Merge, Shortcuts

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Issue stream); design-docs/specs/design-v1-3-sentry-parity.md sections 3 (B3, B4, B5), 4, 5, 6.1, 7, 13 (shortcuts), D29
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p13-ui-stream`
- planPath: `impl-plans/active/v13-p13-ui-stream.md`
- wave: 2
- dependsOn: `v13-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v13-p13-ui-stream.md`
  - `src/web/src/pages/issues/IssueList.tsx`
  - `src/web/src/pages/issues/IssueList.test.tsx`
  - `src/web/src/pages/issues/IssueListControls.tsx`
  - `src/web/src/pages/issues/IssueListRows.tsx`
  - `src/web/src/pages/issues/IssueBulkActions.tsx`
  - `src/web/src/pages/issues/query.ts`
  - `src/web/src/pages/issues/query.test.ts`
  - `src/web/src/pages/issues/savedViews.ts`
  - `src/web/src/pages/issues/issues.css`
  - `src/web/src/pages/issues/StreamViews.tsx`
  - `src/web/src/pages/issues/ArchiveMenu.tsx`
  - `src/web/src/pages/issues/StreamViews.test.tsx`
  - `src/web/scripts/screenshots.ts`
- sharedPaths: none
- artifactRoots: none
- Shared-file ownership (run-1 manifest e2; design section 15): `screenshots.ts` was
  written by v13-p03 in wave 1 and is handed to this plan for wave 2. Edit only the
  stream state steps (tabs, period, bulk selection, archive menu, views, banner) so their
  selectors match the new DOM; keep every route and state that p03 listed and keep the
  `TELESCREEN_SCREENSHOTS_STRICT` handling. No other page or component test imports the
  stream modules at `d8073b1` (`pages/issues/index.tsx` only imports `IssueList`, keep its
  export name). `src/web/src/components/ui/ui.test.tsx` covers `SearchQueryBuilder`, which
  is p03-owned and must not be edited here.

`ArchiveMenu.tsx` is shared UI that v13-p14 imports read-only in its header; only this
plan edits it.

Read `impl-plans/conventions.md` first.

## Intent and Context

Rebuild the stream around the server query language:

- status tabs with counts, the period picker and the trend column;
- the Values column, display names with avatars (B4) and the new sorts;
- negation that is actually sent (B5) and `is:unresolved` as the open set (B3);
- server saved views;
- server bulk actions, "select all N matching", archive and merge;
- the auto-refresh banner and list shortcuts.

Use the p03 client (`issuesApi.listQuery`, `counts`, `bulk`, `merge`, `viewsApi`), the
key spec (`api/issueQuery.ts`) and the components (`Avatar`, `PeriodPicker`,
`StreamTabs`, `registerShortcuts`).

Imitate the existing `IssueList.tsx` resource and refetch pattern and the
`IssueList.test.tsx` mocking with `fetch` stubs.

## Non-goals

- No edits in `src/web/src/api/**` or `components/**` (p03 owns them; file a
  Reconciliation request).
- No detail-page changes (p14).
- No new npm packages.

## Invariants

- The UI never drops a token. The raw query string goes to the server as `query`.
  Client-side validation only marks tokens; it never filters them out.
- The default query is `is:unresolved` (the Unresolved tab).
- A bulk action that mutates refetches the list and the counts. It shows `failed` ids in
  a toast; it does not silently ignore them.
- Auto refresh polls `counts` with `updated_since` every 30 s, only while
  `document.visibilityState === "visible"`. It shows a banner "N new or updated issues"
  and never reorders the list until the user clicks it.
- Shortcuts are ignored while focus is in an editable element (the registry already
  handles this; do not bypass it).

## Deliverables

1. **`query.ts`**: re-export the key spec from `api/issueQuery.ts`. Remove the
   `issueQueryToParams` translation (the server parses now). Keep `issueQueryTokens` for
   highlighting, with negated tokens valid.
2. **`IssueList.tsx`, `IssueListControls.tsx`**:
   - `StreamTabs`: Unresolved, For review, Regressed, Escalating, Archived and Resolved,
     with counts. A tab click replaces the `is:` token;
   - search with autocomplete of keys and values (members as `member:<handle>` and teams
     as `#slug`, from the directory lists);
   - `PeriodPicker`, a sort select (all 7 sorts) and a 24h/14d trend toggle, all kept in
     the URL search params;
   - the banner.
3. **`IssueListRows.tsx`** columns: select, issue (title, short id, source, rule, status
   badge, "+N merged"), trend (`Sparkline` of `trend`), events, values, priority
   (`PriorityBadge` from `priority`), risk, assignee (`Avatar` plus `display_name`, or
   "Unassigned"), last seen and age.
4. **`IssueBulkActions.tsx`**:
   - resolve (dialog: reason and note), archive (`ArchiveMenu`), assign (picker), priority,
     mark reviewed, merge (choose the primary among the selected), status;
   - "Select all N matching", which switches the request to `{query, period}`;
   - "Start workflow" stays the existing client loop.
5. **`ArchiveMenu.tsx`**: a reusable menu with until escalating, for 1h/24h/7d/30d/90d,
   until N more findings and until N more values (number input 1-100000). It emits an
   `ArchiveRequest`.
6. **`StreamViews.tsx` and `savedViews.ts`**:
   - personal and shared sections from `viewsApi`, star, set default, save current,
     delete (own only);
   - a one-time import of the `telescreen.issueViews` localStorage entries: POST each,
     clear the key on full success, and show a toast for failures;
   - the default view applies when the URL has no query.
7. **Shortcuts** via `registerShortcuts("stream", ...)`: `j`/`k` move focus, `o`/Enter
   open, `x` toggles selection, `r` opens the resolve dialog, `a` the assign picker, `e`
   the archive menu, `/` focuses search.

## Tests (vitest)

- B3: with the default view, the request has `query=is:unresolved`, and a mocked regressed
  issue is rendered.
- B5: typing `!source:slack` sends `query=!source:slack`; the token is not dropped.
- B4: an assignee cell renders "Alice Example" with initials "AE", never `mem-01`.
- Tabs: the counts render; clicking Archived sets `is:archived`.
- Bulk:
  - "Select all 57 matching" + archive sends `{query, period, action: "archive"}`;
  - a response with one failure shows the toast with that short id;
  - merge sends `primary_id` plus the other ids.
- Views: the localStorage import posts twice and clears the key; a failed import keeps the
  key.
- Banner: a polled `updated_since_count` of 3 shows "3 new or updated issues", and the row
  order is unchanged until it is clicked.
- Shortcuts: `j` moves focus to the second row; `x` selects; keys inside the search input
  do nothing.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p13-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p13-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p13-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p13-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p13-build.log; echo "exit=$?"
bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p13-shots.log; echo "exit=$?"
```

Expected: each command exits 0, the whole vitest suite passes, and the non-strict
screenshot smoke reports zero page or console errors. A missing browser is a recorded
verification gap, never a pass.

## Completion Criteria

- [ ] B3, B4 and B5 UI regression tests pass.
- [ ] Every stream feature of design-ui-sentry.md 11.2 (Issue stream) is present.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
