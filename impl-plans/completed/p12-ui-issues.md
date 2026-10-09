# P12 UI Issues and Search Pages Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 4, 5, 6 (Search), 7, 8.1, 8.1a, 8.2; design-docs/specs/design-api-and-ui.md (Issues, Evidence, Search endpoints)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09 (run B, session 341)

## Plan Metadata

- planId: `p12-ui-issues`
- planPath: `impl-plans/active/p12-ui-issues.md`
- wave: B2 (parallel with p13, p14, p28)
- dependsOn: `p30-ui-design-system`, `p31-ui-mock-api`
- writePaths:
  - `impl-plans/active/p12-ui-issues.md`
  - `src/web/src/pages/issues`
  - `src/web/src/pages/search`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

Run B rebuilds the Issues and Search pages on the Sentry-pattern design system
(design-ui-sentry.md sections 4, 5, 6 constrained by 8.1). Wave B1 delivered:

- p30: `src/web/src/components/ui/*` (exported from `components/ui/index.ts`), the shell
  with `TopBar.Slot` (`components/shell/TopBar.tsx`), tokens, and `lucide-solid`.
- p31: API types/client aligned with the router serializers (read
  `src/web/src/api/types.ts`; `IssueEvent.observed_at`, `IssueHistory.kind/from_value/
  to_value/note`, global `issuesApi.stats({bucket, days})`, `rawFetch`,
  `ApiError.ruleClasses`) and the mock API; p31 already made mechanical edits in your
  directories (read its Progress Log).

Keep the data flow (same `*Api` calls) and every behavior covered by the existing tests;
change markup and add the patterns below. Never call an endpoint the API does not have.

## Non-goals

- No edits to `src/web/src/api`, `components`, `styles`, routes, shell or other pages.
  Missing component feature -> local helper in your directory + Reconciliation request.
- No backend changes, no npm packages, no Trend column, no "For review" view, no
  Resources column, no Set priority, no Rescan now, no select-all-N banner (design 8.1).

## Deliverables

### Issues list (`pages/issues/IssueList.tsx` plus small files in `pages/issues/`)

- `query.ts`: `ISSUE_QUERY_KEYS: QueryKeySpec[]` for `is` (6 statuses), `severity`
  (high, medium), `rule` (9 rule classes), `source` (slack, gdrive, gdocs, gsheets,
  github, custom, police), `assigned` (free value, description "member:<handle> or
  team:<slug>"); `issueQueryToParams(tokens: QueryToken[], sort: string):
  URLSearchParams` mapping `is->status`, `severity`, `rule->rule_class`, `source`,
  `assigned->assignee`, text tokens joined by spaces -> `q`; invalid tokens and
  `assigned:me|my_teams|none` are never sent.
- URL is the state: read and write `?query=` and `?sort=` with `useSearchParams`;
  default query `is:unresolved`, default sort `last_seen`.
- TopBar: `TopBar.Slot name="title"` "Issues"; actions: Save view `SplitButton`
  ("Save as new view", "Reset") with an accent dot when the query/sort differ from the
  active saved view.
- Saved views: `localStorage["telescreen.issueViews"]` JSON array of `{id, name, query,
  sort, starred}`; after writing, `window.dispatchEvent(new Event("telescreen:issue-views"))`.
- Layout grid `page-filters search sort` (wrapping on narrow containers):
  `PageFilterBar` (Source segment only; writes `source:` into the query),
  `SearchQueryBuilder` with `ISSUE_QUERY_KEYS`, `SortSelect` with Last seen ("Last time
  the item was found"), First seen ("When the issue was first reported"), Findings
  ("Number of findings").
- Above the list: one `MiniBarChart` "Findings, last 14 days (all issues)" from
  `issuesApi.stats({bucket: "day", days: 14})` (one request per page load).
- Rows (container queries on the list container): min-height 82px, stretched link to
  `/issues/<id>`, checkbox column; title 16px medium (rule class label + `display_title`,
  ellipsis); message line `SeverityBar` + `Redacted text={display_locator}`; meta row
  12px: `short_id` with a lucide source icon, location, status `Tag` when not
  unresolved. Columns: Last seen 86px (sm), Age 50px (2xl), Findings 60px (lg, tooltip
  "Since first seen"), Priority 64px (4xl, `PriorityBadge` from severity), Assignee 66px
  (sm). Shift-click selects a range, Cmd/Ctrl-click toggles.
- Bulk bar (sticky, 36px): select-all over loaded rows with indeterminate state; actions
  Resolve, Ignore, Acknowledge, Assign to (member/team picker from
  `directoryApi.members()`/`teams()` producing `member:<handle>`/`team:<slug>`), Start
  workflow (picker over `workflowsApi.list()` items with `active === true`; calls
  `workflowsApi.startRun(name, issueId)` per issue), more menu (Mark in progress,
  Reopen). Each action runs one request per selected issue, offers only transitions
  allowed for every selected status, then reloads and shows one `showToast` with
  succeeded/failed counts.
- Paging: `LoadMore` or `Pagination` with `next_cursor`.
- Empty states: no matches ("No issues match your search", tips on filters and syntax);
  zero unresolved for the default query ("Nothing exposed right now", link to
  `/collectors`). Loading: `Placeholder` row skeletons.

### Issue detail (`pages/issues/IssueDetail.tsx` plus `pages/issues/detail/*`)

- TopBar title: `Issues` > source icon + copyable `short_id`.
- Header: title (20px ellipsis + tooltip); counters Findings (`times_seen`) and Resources
  (distinct `location` among loaded events); subtitle: status text, rule class,
  `Redacted` display locator, badges (evidence count, active run).
- Action bar `role="toolbar"`: Resolve (split; when `resolution` says removal show
  "Resolved by removal" read-only), Ignore (plain), Acknowledge, Start workflow, more menu
  (copy issue as Markdown via `navigator.clipboard.writeText` using masked fields only);
  right: `PriorityBadge` (read-only) and Assignee `Select` (`PATCH {assignee_ref}`). When
  resolved/ignored: Reopen + status banner.
- Two columns at container width >= 1152px (main + 325px sidebar with Open/Close
  sidebar), stacked below.
- Main: findings graph (`MiniBarChart` "Loaded findings, last 14 days": events from
  `issuesApi.events(id, {limit: 200})` bucketed by UTC day of `observed_at`); tag bars
  (detector, first location segment) ; sticky finding navigation with tabs Findings /
  Evidence / Runs, Recommended (highest confidence, then latest) / First / Latest, prev/
  next; "Jump to" chips; `FoldSection`s in order: Highlights (rule class, rule id,
  detector, confidence, severity, location, first/last seen), Location chain (split
  `location` on ` > `, `/`, `:`; matched frame shows `Redacted text={masked_snippet}`),
  Timeline (`Timeline` from `issuesApi.history`: icon per `kind`, `from_value ->
  to_value`, `note`, `at`), Evidence (list + upload; keep the run-A rejection behavior:
  422 shows "Evidence rejected" + rule classes, 415/503 keep their codes), Detection
  details (detector, rule id, rule class, confidence, first 8 chars of `fingerprint`).
- Sidebar: First/last seen (`RelativeTime`); Workflow runs (`runsApi.list({issue_id})`;
  workflow name from one `workflowsApi.list()` id->name map using `definition_id`;
  status `StatusIndicator`; `ProgressMarker` from issue status: unresolved/regressed 0,
  acknowledged 1, in_progress 2, resolved 4); for the latest running/waiting run only,
  one `runsApi.get` -> current step (latest step not succeeded/failed/cancelled) and
  Escalation (open human task title, assignee, status); Activity (comment box "Add a
  comment..."; history entries plus comments posted this session; toast "Comment
  posted"); People (assignee display name from `directoryApi.members()`).

### Search (`pages/search/index.tsx`)

- TopBar title "Search"; free-text input; index tabs (issues, findings, runs, audit);
  `Table` of hits (title, `Redacted` snippet when non-null, status `Tag`, time); keep the
  run-A link rules (issues -> `/issues/<id>`, runs -> `/runs/<id>`, findings/audit no
  link); "Ask Assistant" link to `/assistant?q=<encoded query>`; `EmptyState` for none.

## Key Pitfalls

- Pages never render `<h1>`; use `TopBar.Slot name="title"`.
- Allowed transitions (unchanged): unresolved -> acknowledged, in_progress, resolved,
  ignored; acknowledged -> in_progress, resolved, ignored, unresolved; in_progress ->
  resolved, ignored, unresolved; regressed -> acknowledged, in_progress, resolved,
  ignored; resolved -> unresolved; ignored -> unresolved.
- Never use `innerHTML`; render masked text only through `Redacted` or text nodes.
- No per-row requests in the list (no stats, no runs, no evidence per row).
- An unknown `assigned:` ref makes the API return an error: show it in `ErrorBanner`,
  keep the query editable.
- One component per file; every file under 400 lines.

## Tests (`*.test.tsx`, mocked fetch via `vi.stubGlobal`, responses in serializer shapes)

- `query.ts`: `is:resolved rule:token foo` -> `status=resolved&rule_class=token&q=foo`;
  `severity:[high, medium]` and `assigned:me` -> not sent; default sort `last_seen`.
- List: rows render title, short id and Findings count; typing a status token updates
  `?query=` and refetches with `status=`; the global chart requests
  `/api/issues/stats?bucket=day&days=14` exactly once and no `/api/issues/<id>/stats`
  request is made; selecting two rows and clicking Resolve sends two PATCH requests and
  shows one toast; saving a view writes `telescreen.issueViews` and dispatches
  `telescreen:issue-views`; empty result shows "No issues match your search".
- Detail: only allowed transitions shown (resolved -> Reopen only); transition sends
  PATCH `{status}`; events with two days of `observed_at` produce two non-zero bars;
  history entry renders `from_value -> to_value`; evidence 422 renders "Evidence
  rejected" and `credential`; 415/503 codes kept; masked snippet renders inside a
  `Redacted` masked span; comment POST then appears in Activity.
- Search: renders hits for the chosen index; findings hit is not a link; Ask Assistant
  link carries `q`.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log; echo "exit=$?"
cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build.log; echo "exit=$?"
! grep -rn 'innerHTML\|<h1' src/web/src/pages/issues src/web/src/pages/search
test -z "$(find src/web/src/pages/issues src/web/src/pages/search -name '*.ts*' -exec wc -l {} + | awk '$1 >= 400 && $2 != "total"')"
```

All exit 0. If `typecheck` fails only in p13/p14 page files while they are in progress,
record the log and re-run later; your paths must have zero errors
(`grep -E 'src/pages/(issues|search)' <log>` prints nothing). Optionally run
`VITE_TELESCREEN_MOCK=1 bun run dev` and check the pages by eye; the screenshot smoke is
run by p29.

## Completion Criteria

- [x] Issues list per this plan (query builder, URL state, views, sort, chart, columns, bulk bar, empty states)
- [x] Issue detail per this plan (header, action bar, graph, tag bars, navigation, fold sections, sidebar)
- [x] Search page restyled; run-A behaviors kept
- [x] Tests listed above pass; verification commands pass with logs recorded
- [x] Issue and search error banners update reactively; real-envelope HTTP 500 regression tests pass
- [x] No p12 edits outside the two page directories and this plan
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:22 JST
**Tasks Completed**: Added the issues list/detail and global search pages with filter/sort/paging, lazy 14-day activity, allowed status transitions, assignment, events/history/runs/evidence/comments, upload rejection messaging, and cross-index search results. Added six page tests covering the plan's required behaviors.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None. The p07 `issuesApi.stats` helper has no bucket/day parameters, so the page uses its shared authenticated `apiFetch` locally for the planned 14-day query.
**Hashes**: `src/web/src/pages/issues/index.tsx` before=absent after=ba1da7d343dbd9c4fe9dcaae283e11a62f848c2e2f7dac3d9ab1d4ff74827813; `IssueList.tsx` before=e6c32e8fedc535667afced403f6b8aefaf5817babaf8c9db586b245d56d27217 after=ed2257ccf62b600b40dbd9f563e458c177ba45a7ee6420bdac4fa606ec3429e3; `IssueDetail.tsx` before=absent after=970c5199e20a324e5980fbbda4e36ec0f2fab412ac4e2a4830cd16fec303e1f1; `IssueList.test.tsx` before=absent after=ec9502d433a69db4704540e19241073ef2c970f37697db32e0ad164d176579f2; `IssueDetail.test.tsx` before=absent after=fbbeecce8c2f2e2821f313575ae68211faf1a1f17b2ab79640e147974100ba49; `src/web/src/pages/search/index.tsx` before=absent after=d8617dd050ed9bb4dbe5efcc973c6f203e370bf74f823bba1936d1b75667623f; `index.test.tsx` before=absent after=dd9c282b9ede67d6efbb37ad018817f0c4c6ebf01c87447852a2273620f410fc; plan before=38f3b694266363eb9ec19e0302bb805555fc924d37214d36bbbf68380ce4d7a4.
**Verification**: `test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)` -> exit 0; `cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log` -> exit 0; `cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log` -> exit 0, 6 passed; `cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build.log` -> exit 0.
**Notes**: Intermediate test attempts exposed missing router context in tests and an error-banner update issue; both were corrected within p12 paths. A subsequent combined-tree typecheck passed, including the previously in-progress p13/p14 pages. No production/test files outside the two page directories were changed. Files remain below the plan's 400-line limit.

### Session: 2026-10-09 Step 6 current-tree verification
**Tasks Completed**: Re-ran p12 typecheck, issue/search tests, and isolated production build against the current shared tree; confirmed the `p07-web-shell` dependency is in the runtime `acceptedPlanIds`.
**Tasks In Progress**: None.
**Blockers**: None.
**Verification**: `set -o pipefail; test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)` -> exit 0 (dependencies already present); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log` -> exit 0; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log` -> exit 0, 6 passed; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build-final.log` -> exit 0, 52 modules transformed.
**Current Source Hashes**: `issues/index.tsx` ba1da7d343dbd9c4fe9dcaae283e11a62f848c2e2f7dac3d9ab1d4ff74827813; `IssueList.tsx` ed2257ccf62b600b40dbd9f563e458c177ba45a7ee6420bdac4fa606ec3429e3; `IssueDetail.tsx` 970c5199e20a324e5980fbbda4e36ec0f2fab412ac4e2a4830cd16fec303e1f1; `IssueList.test.tsx` ec9502d433a69db4704540e19241073ef2c970f37697db32e0ad164d176579f2; `IssueDetail.test.tsx` fbbeecce8c2f2e2821f313575ae68211faf1a1f17b2ab79640e147974100ba49; `search/index.tsx` d8617dd050ed9bb4dbe5efcc973c6f203e370bf74f823bba1936d1b75667623f; `search/index.test.tsx` dd9c282b9ede67d6efbb37ad018817f0c4c6ebf01c87447852a2273620f410fc.
**Notes**: No page source changed in this verification pass. Existing implementation and six focused tests remain within p12's page-directory write scope. Formal review and serial combined-tree review remain downstream workflow steps.

### Session: 2026-10-09 P12-EVIDENCE-REJECTION-RULE-CLASSES repair
**Tasks Completed**: Added `issues/uploadEvidence.ts` to PUT the raw file with the existing endpoint, content type, and bearer token while parsing only safe error fields. It carries string-only `rule_classes` in a typed rejection error; `IssueDetail` renders those classes with the fixed label and falls back to the static contract detail. Other codes remain `ApiError`. The 422 fixture now matches p18 exactly, and the test asserts both the rejection label and `credential`; added 415/503 upload-code coverage. Existing transition, list, and search tests were retained unchanged.
**Tasks In Progress**: Independent test-integrity and adversarial reviews, then combined-tree integration review (downstream workflow steps).
**Blockers**: None.
**Reconciliation requests**: p07-web-shell should add optional `rule_classes?: string[]` to `ApiErrorBody` and expose it on `ApiError`; p12 uses the local helper until that shared client contract is updated.
**Verification**: `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log` -> exit 0; `set -o pipefail; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log` -> exit 0, 7 tests passed across 3 files, 0 failed; `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build.log` -> exit 0, 53 modules transformed.
**Hashes**: `IssueDetail.tsx` before=970c5199e20a324e5980fbbda4e36ec0f2fab412ac4e2a4830cd16fec303e1f1 after=fffb5aada2b30061761cab4ace2d6f680432f4a3f789e34770d39d0366f45f54; `IssueDetail.test.tsx` before=fbbeecce8c2f2e2821f313575ae68211faf1a1f17b2ab79640e147974100ba49 after=1193452c71a5f34caef178416fe06dc320f4af732f92c5c8e460d83adacbfdf0; `uploadEvidence.ts` before=absent after=5b57bb0a1bd9b203eaddf63c5756d2d727fcf7cc080023f86d340cd6db224157. Unchanged p12 pages retain the 2026-10-09 hashes above.
**Notes**: The seven p12 page files remain under 400 lines. No files under p07 API/components or outside p12 writePaths were edited. The formal review decisions are pending and are not claimed here.

### Session: 2026-10-09 P12-SEARCH-FINDINGS-LINK-WRONG-ID repair
**Tasks Completed**: Restricted search links to issue hits (`/issues/<issue id>`) and run hits (`/runs/<run id>`). Findings and audit render the existing non-link article fallback. Added a findings-hit regression test asserting the title/snippet render, no `/issues/finding-9` link exists, and the request uses `index=findings`. The existing issues-link assertion remains unchanged.
**Tasks In Progress**: Independent test-integrity and adversarial re-review, followed by combined-tree integration review (downstream workflow steps).
**Blockers**: None.
**Reconciliation requests**: p15 search response mapping and the p07 `SearchHit` type should expose `issue_id` for findings hits so the UI can link to the owning issue. Until that contract is available, findings hits remain non-links.
**Verification**: `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log` -> exit 0; `set -o pipefail; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log` -> exit 0, 8 passed across 3 files, 0 failed; `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build.log` -> exit 0, 53 modules transformed.
**Hashes**: `src/web/src/pages/search/index.tsx` before=d8617dd050ed9bb4dbe5efcc973c6f203e370bf74f823bba1936d1b75667623f after=8dd5a992af5ea20685c4b374f9c559df7ddb582a233101adb058affcc571bbb8; `src/web/src/pages/search/index.test.tsx` before=dd9c282b9ede67d6efbb37ad018817f0c4c6ebf01c87447852a2273620f410fc after=8e3f7e4345f5cede01d2a3707d3f7c37c65d507b854aaed3433f41390c597fe6; plan before=625bb5f40bc510646058f9db88b21b3b6a0c44b02558c125a4940610126752c0; final plan hash is recorded in `tmp/telescreen-v1-20261008-s319/p12-ui-issues/repair-comm-005500/final-sha256.txt`.
**Notes**: Prior findings-search hits used the finding row id as an issue id and always opened an unavailable issue. Current behavior preserves the results while preventing the incorrect navigation. p15/p07 contract work is deferred to its owners; no files outside p12 write paths were changed.

### Session: 2026-10-09 Step 6 run B implementation
**Tasks Completed**: Rebuilt the issues list around URL-backed query/sort, saved views, source filters, global 14-day chart, responsive loaded-row selection, cursor paging and bulk actions. Rebuilt issue detail with supported API data, status/assignee/workflow actions, masked display/copy, findings graph/tags/navigation/fold sections, evidence rejection, activity and sidebar. Restyled cross-index Search with masked snippets and valid issue/run links. Split page modules and CSS to stay below 400 lines; added/retained page tests.
**Tasks In Progress**: Independent test-integrity and adversarial review, then serial combined-tree integration review (downstream workflow steps).
**Blockers**: None for p12 implementation. p30 and p31 are accepted dependencies in the runtime `acceptedPlanIds`.
**Verification**: `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck-final2.log` -> exit 0; `set -o pipefail; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test-final2.log` -> exit 0, 4 files / 16 tests passed / 0 failed; `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web-final2 --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build-final2.log` -> exit 0, 2321 modules transformed; exact p12 `prettier --check` command -> exit 0 (`/tmp/telescreen-p12-ui-issues-format-final2.log`); `! rg -n 'innerHTML|<h1' src/web/src/pages/issues src/web/src/pages/search` -> exit 0 (`/tmp/telescreen-p12-ui-issues-static-patterns-final2.log`); TypeScript and CSS page line-limit checks -> exit 0 (`/tmp/telescreen-p12-ui-issues-line-limit-final2.log`, `/tmp/telescreen-p12-ui-issues-css-line-limit-final2.log`).
**Source Identity**: SHA-256 inventory for all p12 page files: `tmp/telescreen-v1-20261009-s341/p12-ui-issues/step6-implement/final-source-sha256.txt`.
**Prior Attempts**: `/tmp/telescreen-p12-ui-issues-typecheck-final.log` captured a type error because an event callback returned the signal setter's array; fixed by a block-bodied void callback, then the final2 typecheck passed. `/tmp/telescreen-p12-ui-issues-format-final.log` reported ten unformatted files; the exact p12-owned files were formatted, and final2 Prettier check passed. Earlier focused test attempts are recorded in the Step 6 evidence directory; the final2 16/16 run is current-source evidence.
**Notes**: No p12 implementation edits were made outside `src/web/src/pages/issues/**`, `src/web/src/pages/search/**`, and this plan. The working tree contains other plans' concurrent changes, which are not attributed to p12. Formal review and combined-tree integration checks are pending downstream and are not claimed complete here.

### Session: 2026-10-09 P12-ADV-01 repair
**Finding**: `P12-ADV-01` from step7-adversarial-review, communication `comm-005744` (mid).
**Resolution**: The default query/sort normalization now runs once when either URL parameter is missing and calls `setSearchParams({ query: query(), sort: sort() }, { replace: true })`. User-initiated query, sort and saved-view handlers remain unchanged and continue to push history. Added an IssueList memory-history regression test: after `/issues` normalizes, `history.go(-1)` returns to `/`.
**Verification**: `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck-adv1.log` -> exit 0; `set -o pipefail; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test-adv1.log` -> exit 0, 4 files / 17 tests passed / 0 failed; `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web-adv1 --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build-adv1.log` -> exit 0, 2321 modules transformed; Prettier on `IssueList.tsx` and `IssueList.test.tsx` -> exit 0 (`/tmp/telescreen-p12-ui-issues-format-adv1-final.log`); no `<h1>`/`innerHTML` -> exit 0 (`/tmp/telescreen-p12-ui-issues-static-patterns-adv1.log`); TS file-size gate -> exit 0 (`/tmp/telescreen-p12-ui-issues-line-limit-adv1.log`); source hash verification -> all 21 entries match (`tmp/telescreen-v1-20261009-s341/p12-ui-issues/step6-implement-adv1/source-sha256-check-adv1.log`).
**Review Decision**: P12-ADV-01 is addressed in source and behaviorally verified. Independent Opus adversarial re-review is pending; no review acceptance is claimed.

**Owner resolution (2026-10-09)**: P12-ADV-01 is fixed (history replace on default
normalization, regression test). Report it ONLY in `addressedFeedback`; do NOT list it in
`findings` or `risks` (conventions section 7a). Low notes that need no action are not risks.

### Session: 2026-10-09 P12-INT-01 repair
**Finding**: `P12-INT-01` from integration review, requiring fresh verification and independent re-review of the prior `P12-ADV-01` repair, plus reactive p12 error banners.
**Tasks Completed**: Wrapped the issues-list `ErrorBanner` in keyed `<Show>` so a server error set after initial mount is rendered. Added post-mount HTTP 500 regression tests for issues list and search using `{error:{code:'internal_error',detail:'failed'}}`. Search and issue-detail banners were already reactive.
**Tasks In Progress**: Independent test-integrity and adversarial re-review, followed by serial integration review (downstream workflow steps).
**Blockers**: None for p12 implementation.
**Reconciliation requests**: None.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck-int1-final.log` -> exit 0; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test-int1-final.log` -> exit 0, 4 files / 19 tests passed / 0 failed; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-p12-ui-issues-test-all-int1-final.log` -> exit 0, 26 files / 109 tests passed / 0 failed; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web-int1-final --emptyOutDir 2>&1 | tee /tmp/telescreen-p12-ui-issues-build-int1-final.log` -> exit 0, 2321 modules transformed; exact-file Prettier check -> exit 0 (`/tmp/telescreen-p12-ui-issues-format-int1-final.log`); no forbidden `<h1>`/`innerHTML` -> exit 0 (`/tmp/telescreen-p12-ui-issues-static-int1-final.log`); file-size and plan completion gate -> exit 0.
**Hashes**: `IssueList.tsx` before=2749edefa6477daeb05811540909c5cb947271587ea6f1e18aad5be5a8533d3e after=fd079f5a74e83d3e92ed0e24f09c58af70e73f09d9a62b048901878c322de0d9; `IssueList.test.tsx` before=9303ba56099e0b03dcefd6a08e896fbff0e6529c98481e2cc26775b34463a8c4 after=feedbb0da8170207d36112920f5fcea65fd544cf3f602583c0afc2213afce150; `search/index.test.tsx` before=ecda796c0b61a264b8f7985aa05bed9d50fb700a944761ed62191e1885a3e134 after=d984b696a95aed3b30a5cc73bbbc724a9390c258ef3a957e7f9850724b0703e6; search/index.tsx unchanged=98dd43a5095931f22931d471fa65bdfcb788285a887487128811da64052eeba8.
**Notes**: P12-ADV-01's replace-normalization and memory-history Back regression were re-run as part of the 19-test focused suite. No page source outside p12's assigned page directories was changed. The initial formatter check found style-only differences in the two new test files (`/tmp/telescreen-p12-ui-issues-format-int1.log`, exit 1); only those files were formatted, then the final exact-file check passed (`/tmp/telescreen-p12-ui-issues-format-int1-final.log`). All behavioral/build gates were rerun after formatting. No formal review acceptance is claimed.
