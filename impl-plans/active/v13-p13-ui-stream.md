# V13-P13 UI Issue Stream: Tabs, Counts, Period, Trend, Values, Search, Server Views, Bulk, Merge, Shortcuts

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Issue stream); design-docs/specs/design-v1-3-sentry-parity.md sections 3 (B3, B4, B5), 4, 5, 6.1, 7, 13 (shortcuts), D29
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

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

- [x] B3, B4 and B5 UI regression tests pass.
- [x] Every stream feature of design-ui-sentry.md 11.2 (Issue stream) is present.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

### Session 1 — 2026-10-12 — Stream implementation

- Implemented the stream query parser adapter, tabs and counts, URL-backed period,
  custom dates, sort and trend controls, autocomplete values, issue rows, server saved
  views with one-time localStorage import, bulk toolbar, archive menu, merge action,
  failure toasts, refresh banner and stream shortcuts. Added regression tests for B3,
  B4, B5, tabs, custom dates, bulk selection, views, archive, refresh behavior and
  shortcuts. The screenshot runner now targets the stream views, tabs, selection and
  archive menu. All p13 stream screenshot states ran; the six non-strict skips are
  downstream alert/member/onboarding states. No browser, page, HTTP or console errors.
- Source SHA-256 before/after values are recorded in
  `tmp/telescreen-v13-20261012-e2/p13/source-hashes.tsv`. The pre-edit plan hash was
  `0c18dc5977a5cc8dfaab3152ee1c6fd04763e54393daf55e3439bc4adde00147`; final plan
  hash is recorded in `tmp/telescreen-v13-20261012-e2/p13/plan-final.sha256`.
- Reconciliation request: p13 cannot implement correct query-wide Assign, Priority,
  Resolve-with-reason/note, conditional Archive, Remediate or Start workflow using
  the current `issuesApi.bulk` contract in `src/web/src/api/client.ts`, which accepts
  only `ids`, `query`, `period`, `action` and `status`. The p13 writePaths exclude that
  client, and the matching bulk backend belongs to downstream v13-p06. Extend the
  API/client and p06 contract to carry the required action payloads and add matching
  tests, then let p13 wire each control to those fields. Until then, those actions
  cannot be applied reliably to “all matching”; selected-row actions remain wired to
  their per-issue APIs. No out-of-scope client or backend file was edited.
- Verification (all exit 0):
  - `cd src/web && bun install --frozen-lockfile` — `/tmp/telescreen-v13-p13-install-final.log`.
  - `cd src/web && bun run typecheck` — `/tmp/telescreen-v13-p13-typecheck-final.log`.
  - `cd src/web && bun run test` — `/tmp/telescreen-v13-p13-test-final.log`, 37 files / 193 tests passed.
  - `cd src/web && bun run build --outDir /tmp/telescreen-v13-p13-web --emptyOutDir` — `/tmp/telescreen-v13-p13-build-final.log`.
  - `cd src/web && bun run screenshots` — `/tmp/telescreen-v13-p13-shots-final.log`; exit 0, all p13 stream states captured, zero browser errors.
- Implementation remains incomplete only for the query-wide action payload seam above;
  formal review and integration are downstream workflow steps.

### Session 2 — 2026-10-13 — INT-W2-02 query-wide bulk payload repair

- Updated `IssueList.tsx` to pass typed `BulkParams` for selected and query-wide bulk
  requests. Status now uses `params.status`; priority, member assignment (including
  `assignee_ref: null` for Unassigned), archive conditions, and resolve reason/note are
  sent in `params`. Remediation and Start workflow remain finite-selection actions and
  show a warning when the selection is all matching. Added the Unassigned option to
  `IssueBulkActions.tsx`.
- Added exact request-body assertions for all-matching status, priority, member
  assignment (including null for Unassigned), archive-until-findings, and resolve
  actions. The merge assertion continues to expect `{primary_id, ids}`. Added a test
  that all-matching remediation is selection only and warns. B3, B4, B5 and other stream
  assertions remain intact.
- Hashes: `IssueList.tsx` before=be6a2697e1c457786ab6b1fb81fa47b146335674d9141bc8be5556b7dac96d35 after=33697b399b316536944e2fa050e663a7bebdc4c1b2455fef3a244aee27280937; `IssueBulkActions.tsx` before=41662c6c340012d627dbd5c1aa6bcf114f40e25691392d4552937a9cdb64a91b after=eeb0685d92650dc3eac6b848fa21645d00882d643539982d07278e0cc79696fc; `IssueList.test.tsx` before=f4c13c7b3a6de7fc55ac7d25c7fe2d145eaa70f14f2a89000ca5138cbe631b56 after=b2c01458af032e405a547cd4fb6602ae30901e66660662300d858f7ca284d488. The plan pre-edit hash was b817a031c0419b468a3ea60fbc1914795c14953064bd9814a702d35b8814b622; post-edit hash is recorded in `tmp/telescreen-v13-20261012-e2/p13/reconcile-int-w2-02/plan-final.sha256`.
- p03 API files remain untouched by this repair and match the reviewed contract hashes: `client.ts` 9c986c54d4ff733a5e8589aa73ba624d71f290689bb30bf8a3a12a667b741e3d, `types.ts` e090323511c15d680b613b8b68814276c93f48ff50c2f18f9c84d2321df6b5ab, `router.ts` 564c6a4bb1a7e1a8bb0c7e8ba4d6f66a2e8f8d78d20d8095105d629d4245b0a6, `mock.test.ts` 0a238952637d4c1fdc77cd099fdf4dcbd64ff8233988576ac9b0a4d5a01e14e3. `src/web/package.json` and `src/web/bun.lock` are unchanged.
- Verification (final source, all exit 0):
  - `cd src/web && bun install --frozen-lockfile` — `/tmp/telescreen-v13-p13-install.log`.
  - `cd src/web && bun run typecheck` — `/tmp/telescreen-v13-p13-typecheck-final2.log`.
  - `cd src/web && bun x vitest run src/pages/issues/IssueList.test.tsx` — `/tmp/telescreen-v13-p13-focused-test-final2.log`, 19 passed / 0 failed.
  - `cd src/web && bun run test` — `/tmp/telescreen-v13-p13-test-final2.log`, 37 files / 199 tests passed / 0 failed.
  - `cd src/web && bun run build --outDir /tmp/telescreen-v13-p13-web-final2 --emptyOutDir` — `/tmp/telescreen-v13-p13-build-final2.log`.
  - `cd src/web && bun run screenshots` — `/tmp/telescreen-v13-p13-shots-final2.log`, exit 0; no browser errors, with the three downstream states explicitly skipped.
  - `git diff --check -- src/web/src/pages/issues/IssueList.tsx src/web/src/pages/issues/IssueBulkActions.tsx src/web/src/pages/issues/IssueList.test.tsx impl-plans/active/v13-p13-ui-stream.md` — exit 0.
  - Dependency file check (`git diff --name-only HEAD -- src/web/package.json src/web/bun.lock`) — no paths reported.
- An initial full Vitest attempt failed only because the archive threshold test dispatched `change` while the input uses `onInput`; corrected the test event and reran the complete UI suite and full suite successfully. The original complete log is `/tmp/telescreen-v13-p13-test.log`; passing source-matched reruns are `/tmp/telescreen-v13-p13-test-final.log` and `/tmp/telescreen-v13-p13-test-final2.log`.
- INT-W2-02 is addressed. Implementation-phase work for this plan is complete; independent review and run integration remain downstream workflow steps.
