# V11-P15 UI Issues Pages: Exposure, Risk, New Statuses, Remediation, Exceptions, Occurrences

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 9.1 and 9.3 (rows: Issue search tokens, Sort, Secondary panel, List columns, Bulk actions, Issue detail actions, Status banner, Sidebar, Fold sections, Timeline); section 8 (still binding)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p15-ui-issues`
- planPath: `impl-plans/active/v11-p15-ui-issues.md`
- wave: 2
- dependsOn: `v11-p04-ui-foundation`
- writePaths:
  - `impl-plans/active/v11-p15-ui-issues.md`
  - `src/web/src/pages/issues`
- sharedPaths: none
- artifactRoots: none (builds go to `/tmp/telescreen-v11-p15-ui-issues-web`)

Read `impl-plans/conventions.md` first.

## Intent and Context

Rebuild the parts of the Issues list and detail pages that the v1.1 features touch,
using the types, client methods, mock endpoints and components from v11-p04 (`issuesApi`,
`exceptionsApi`, `ExposureBadge`, `RiskPill`, `StatusBadge`, `labels.ts`). Pages work in
mock mode (`VITE_TELESCREEN_MOCK=1`) now; the backend follows in later waves.

Files: `pages/issues/IssueList.tsx`, `IssueListControls.tsx`, `IssueListRows.tsx`,
`IssueBulkActions.tsx`, `query.ts`, `savedViews.ts`, `IssueDetail.tsx`,
`detail/IssueDetailHeader.tsx`, `detail/IssueDetailSections.tsx`,
`detail/IssueDetailSidebar.tsx`, `detail/types.ts`, and the CSS files.

## Non-goals

- No edits outside `src/web/src/pages/issues/**`. If a needed client method, type or
  component is missing, write a reconciliation request for v11-p04 and use a local
  workaround inside your pages.
- No raw value reveal of any kind. "Open in source" deep links already shown in v1 stay
  as they are; add no new links.

## Invariants

- The UI never offers PATCH to `resolved` or `ignored` (the backend rejects them).
- Bulk actions: Acknowledge, Mark in progress, Reopen, Assign to, Start workflow and
  Mark remediated only.
- Color is never the only signal: badges carry text (design section 3 accessibility).

## Deliverables

1. **`query.ts`** token mapping (design 9.3, Issue search tokens):
   - `is:allowed` and `is:pending_verification` -> `status`;
   - `exposure:<visibility>` -> `exposure`;
   - `is:public` -> `exposure=public`;
   - `assigned:me|my_teams` -> `assignee=me|my_teams`;
   - `severity:critical` is valid.

   Update the supported-key tooltip list. Sort options add `risk` ("Highest risk first").
2. **List**:
   - Risk column (`RiskPill`, visible from container `md`).
   - `ExposureBadge` (compact) in the row meta.
   - Status tags via `StatusBadge` with `resolution`.
   - `PriorityBadge` from the effective severity, with the base severity in a tooltip
     when it differs.
   - `savedViews.ts`: built-in links are the shell's job; the page only parses URLs.
3. **Bulk actions**: replace Resolve / Ignore with "Mark remediated"
   (`issuesApi.remediation(id, {action: "remove", include_linked: false})` per issue).
   The per-issue failure toast counts as in v1.
4. **Detail action bar** (design 9.3, Issue detail actions):
   - The Resolve split menu offers "Mark remediated..." (a dialog with the action radio
     removed or restricted, and an "Include linked occurrences" checkbox shown only for
     credential classes), "Resolve as false positive..." (note required; submit disabled
     while empty) and "Resolve without verification..." (shown only when the stored
     token is admin; detect admin the way the page already does if it does, otherwise
     always show it and render the API's 403 error banner).
   - "Request exception..." has a reason textarea and duration select (7 days, 30 days,
     90 days, until regressed, forever).
   - Reopen = PATCH `unresolved`.
5. **Status banner**: the texts of design 9.3 for `pending_verification`, `allowed`
   (policy name and version from the issue fields) and `ignored` (active exception
   expiry or "No expiry (legacy)").
6. **Sidebar**: sections Exposure, Risk, Owner, Policy and the Exception card. Approve
   and Reject buttons appear when `active_exception.status === "requested"`; on 403 show
   the error banner.
7. **Fold section** "Linked occurrences (N)" after Location chain
   (`issuesApi.occurrences`): rows link to `/issues/<id>` with source, masked display
   locator, status and `ExposureBadge`. Add the key-version note text from the design.
   Credential classes only; hide the section otherwise.
8. **Timeline**: labels and icons for the ten new history kinds.
9. **ProgressMarker mapping** in the sidebar: `pending_verification` -> Fix proposed;
   `allowed` -> no marker.

## Pitfalls

- Do not show `display_title` inside new notification-like UI elements. That is fine on
  issue pages (v1 field), but do not duplicate it into banners.
- Keep the existing tests green; update them where markup changed, without deleting
  coverage.
- Keep components small. Split `IssueDetailSidebar.tsx` into
  `detail/sidebar/*.tsx` if it grows beyond about 300 lines.

## Tests (vitest, mocked fetch or the mock router)

- query tokens: each new token serializes to the expected params; an invalid exposure
  value renders an invalid token;
- list: a critical row shows 4-bar priority and the critical severity bar; a public row
  shows "Public"; risk sort sends `sort=risk`;
- bulk "Mark remediated" calls the remediation endpoint once per selected issue;
- detail:
  - false-positive submit is disabled with an empty note;
  - the remediation dialog shows include-linked only for a credential issue;
  - the exception request posts the reason and duration;
  - the pending banner text;
  - the allowed banner shows policy name and version;
  - Linked occurrences renders 2 rows for the mock credential issue;
- timeline renders an `exception_expired` entry label.

## Verification

Preamble from conventions section 7, then in `src/web`:

- `bun install --frozen-lockfile > /tmp/telescreen-v11-p15-ui-issues-install.log 2>&1; echo "exit=$?"`
- `bun run typecheck > /tmp/telescreen-v11-p15-ui-issues-typecheck.log 2>&1; echo "exit=$?"`
- `bun run test > /tmp/telescreen-v11-p15-ui-issues-test.log 2>&1; echo "exit=$?"`
- `bun run build --outDir /tmp/telescreen-v11-p15-ui-issues-web --emptyOutDir > /tmp/telescreen-v11-p15-ui-issues-build.log 2>&1; echo "exit=$?"`

All must show `exit=0`, with exactly one tolerance. v11-p16 runs in the same wave and may
still be editing its page directories `src/web/src/pages/{tasks,policies,collectors,police,escalation,settings}`.
The only tolerated failures are typecheck errors located in those directories and test
files inside them. If one occurs: record the file paths and test ids in the Progress Log
as informational, then prove nothing else fails (run in `src/web`):

- typecheck: `! grep -E 'error TS' /tmp/telescreen-v11-p15-ui-issues-typecheck.log | grep -v -E '^src/pages/(tasks|policies|collectors|police|escalation|settings)/'; echo "exit=$?"` -> `exit=0`;
- test: `bun run test --exclude 'src/pages/tasks/**' --exclude 'src/pages/policies/**' --exclude 'src/pages/collectors/**' --exclude 'src/pages/police/**' --exclude 'src/pages/escalation/**' --exclude 'src/pages/settings/**' > /tmp/telescreen-v11-p15-ui-issues-test-own.log 2>&1; echo "exit=$?"` -> `exit=0`.

Any error or failing test in `src/web/src/pages/issues` or outside the v11-p16
directories blocks completion. Runs with tolerated failures go only in the Progress Log;
only passing commands go in the step `verification` list (conventions 7a). The build must
still exit 0.

## Completion Criteria

- [ ] Deliverables 1-9 implemented in `pages/issues/**` only.
- [ ] typecheck, test and build pass (or typecheck/test fail only in the named v11-p16
      directories, with the grep and the `--exclude` rerun showing `exit=0`).
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
