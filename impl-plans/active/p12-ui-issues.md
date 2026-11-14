# P12 UI Issues and Search Pages Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (UI: Issues list, Issue detail, Search; Evidence)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p12-ui-issues`
- planPath: `impl-plans/active/p12-ui-issues.md`
- wave: 3
- dependsOn: `p07-web-shell`
- writePaths:
  - `impl-plans/active/p12-ui-issues.md`
  - `src/web/src/pages/issues`
  - `src/web/src/pages/search`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 8).

## Intent and Context

These are the Sentry-like issue pages (acceptance: "issues list/detail"). The shell (p07)
discovers `src/web/src/pages/<area>/index.tsx` automatically and routes `/issues` and
`/issues/:id` to the same default export. Use only `src/web/src/api/client.ts`,
`src/web/src/api/types.ts` and `src/web/src/components/*` from p07. Do not edit them; if
something is missing, add a local helper inside your page directory and log a
Reconciliation request.

## Non-goals

- No backend changes, no shell changes, no new npm packages.

## Deliverables

- `pages/issues/index.tsx`: default export `IssuesPage`; renders `IssueList` when there
  is no `id` param, else `IssueDetail`.
- `pages/issues/IssueList.tsx`:
  - Filters: status, source, rule class, severity, assignee, and free text `q`. The free
    text goes to `issuesApi.list({q})`, which the backend serves through search.
  - Sort select: `last_seen`, `first_seen`, `times_seen`.
  - Columns: short id (a link to `/issues/<id>`), title, `StatusBadge`, first seen, last
    seen (`RelativeTime`), events (`times_seen`), and a 14-day `Sparkline` from
    `issuesApi.stats(id, {bucket: "day", days: 14})`, loaded lazily per row.
  - `LoadMore` paging with `next_cursor`.
- `pages/issues/IssueDetail.tsx`:
  - Header: short id, title, status, severity, rule class, display locator, and first
    seen / last seen / times seen.
  - Status transition buttons limited to the allowed transitions from the data-model
    status machine (encode the table as a constant).
  - Assignee input that calls `issuesApi.update({assignee_ref})`.
  - Masked event list from `issuesApi.events`, showing masked snippet, location, rule id,
    detector, confidence and observed time.
  - History timeline from `issuesApi.history`.
  - Linked runs from `runsApi.list({issue_id})`, showing the current step and status,
    each linking to `/runs/<id>`.
  - Evidence list from `evidenceApi.list({issue_id})` and an upload form (a file input
    sent as a raw body through a local typed helper). The helper reads only safe fields
    from the 422 `evidence_rejected` contract and shows rule classes; 415 and 503 retain
    their error codes through `ApiError`.
  - Comment box that calls `issuesApi.comment`.
- `pages/search/index.tsx`: a global search box; an index selector (issues, findings,
  runs, audit) using `searchApi.search({q, index})`; issue results link to issues and
  run results link to runs. Findings and audit use the non-link fallback until p15/p07
  expose an issue id for findings hits.
- Tests next to the pages (`*.test.tsx`, vitest with `@solidjs/testing-library`, a
  mocked `fetch` via `vi.stubGlobal`):
  - the list renders rows from a mocked page and shows a status badge and an events count;
  - changing the status filter refetches with `status=resolved` in the URL;
  - the detail shows only allowed transitions (for `resolved` only "Reopen");
  - clicking a transition sends a PATCH with `{status}`;
  - an evidence 422 response renders "Evidence rejected" plus the rule classes;
  - search renders results for the chosen index;
  - an error response renders `ErrorBanner` with the code.

## Key Pitfalls

- Never render raw request or response bodies of the evidence upload; show only the
  API's masked fields.
- Allowed transitions (user): unresolved -> acknowledged, in_progress, resolved, ignored;
  acknowledged -> in_progress, resolved, ignored, unresolved; in_progress -> resolved,
  ignored, unresolved; regressed -> acknowledged, in_progress, resolved, ignored;
  resolved -> unresolved; ignored -> unresolved.
- Keep components small; one file per component; no file over 400 lines.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)   # p01 installed it; do not reinstall concurrently
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p12-ui-issues-typecheck.log    # exit 0
cd src/web && bunx vitest run src/pages/issues src/pages/search 2>&1 | tee /tmp/telescreen-p12-ui-issues-test.log   # exit 0
cd src/web && bun run build --outDir /tmp/telescreen-p12-ui-issues-web --emptyOutDir      # exit 0
```

If `typecheck` fails only in files owned by other UI plans still in progress, record
that with the log and re-run after they finish. Your own files must have zero errors
(`grep "src/pages/\(issues\|search\)" <log>` prints nothing).

## Completion Criteria

- [x] Issue list, issue detail (transitions, assignment, events, history, runs, evidence, comments) and search pages delivered
- [x] Tests listed above pass
- [x] Contract-shaped evidence 422 renders rule classes; 415 and 503 retain their codes
- [x] No edits outside the two page directories
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
