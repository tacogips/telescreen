# P12 UI Issues and Search Pages Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md (UI: Issues list, Issue detail, Search; Evidence)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

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
    sent through `evidenceApi.upload` as a raw body). A 422 `evidence_rejected` response
    shows the rejection message with rule classes only; 415 and 503 show their codes.
  - Comment box that calls `issuesApi.comment`.
- `pages/search/index.tsx`: a global search box; an index selector (issues, findings,
  runs, audit) using `searchApi.search({q, index})`; result lists per index linking to
  the issue or run.
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

- [ ] Issue list, issue detail (transitions, assignment, events, history, runs, evidence, comments) and search pages delivered
- [ ] Tests listed above pass
- [ ] No edits outside the two page directories
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
