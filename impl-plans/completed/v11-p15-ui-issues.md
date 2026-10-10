# V11-P15 UI Issues Pages: Exposure, Risk, New Statuses, Remediation, Exceptions, Occurrences

**Status**: Completed (integration-accepted at 809b02d; archived 2026-10-11)
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

- [x] Deliverables 1-9 implemented in `pages/issues/**` only.
- [x] typecheck, test and build pass (or typecheck/test fail only in the named v11-p16
      directories, with the grep and the `--exclude` rerun showing `exit=0`).
- [x] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)

**Owner note (2026-10-10)**: `IssueDetailHeader.tsx` and `IssueList.tsx` contain owner placeholders `allowed: []` and `pending_verification: []` in their status transition maps (added so v11-p04's widened IssueStatus typechecks). Replace them with the transitions specified in the v1.1 design (verified resolution, policy-allowed issues).

### Session: 2026-10-10 17:20 JST
**Tasks Completed**: Added v1.1 issue tokens and risk sorting; critical severity, exposure, coverage and risk in list rows; replaced bulk Resolve/Ignore with per-issue remediation; implemented detail remediation and reasoned resolution dialogs, exception requests, status banners, exposure/risk/owner/policy/exception sidebar, credential-only linked occurrences and key-version note, timeline labels/icons, and progress mapping. Updated issue-page tests.
**Tasks In Progress**: Admin-only forever exception duration is not representable by the accepted v11-p04 API contract.
**Blockers**: `ExceptionDuration` in `src/web/src/api/types.ts` and `issuesApi.requestException` in `src/web/src/api/client.ts` accept only `7d | 30d | 90d | until_regressed`; p15 may edit only `src/web/src/pages/issues/**` and its plan. Sending `forever` through an unsafe cast or inventing a local request path would bypass the accepted typed client contract.
**Reconciliation requests**: v11-p04 (or serial reconciliation) should extend the accepted exception duration contract, typed client and mock behavior to support admin-only no-expiry requests; then p15 can add the option using the typed client. Design source: `design-docs/specs/design-ui-sentry.md` §9.3; p04 currently owns `src/web/src/api/**`.
**Hashes**: `src/web/src/pages/issues/issue-detail.css` before=3d759a306bcb5aa299df9e7eb4d2a6c62c3f29028266c31a5ae781721e4bdc5e after=a8c55710d0c5f194c84d7ccda12cf44fb4776fca4f8e183c58b4cdab4c967ed1; `detail/types.ts` before=a1ca1e688dd92e4dee57ea9ad5a99e9fb6add25322c8173eb60c0a47ddb9ef58 after=dc3e497e60c1386025cd1fd0c5f8d332d9ff2898b593c7e3a612176172612a1c; `IssueDetail.test.tsx` before=a38d111749f7bbae53941006ab41cb3983170287d6ea9b90b1df5db5ac282911 after=d4419caa02450da7e0e4bc1700fd017ee2c5b100c326ac7a0790d0953bb6cd4e; `IssueList.tsx` before=fe205013d84c3688aafe7f227940d621019ac6bc9f737a205da2f762731861d9 after=896851ef3e7bf2ddcc983b4c0d94770967704b7ba44cae5c86eb2437e8450cd8; `IssueList.test.tsx` before=feedbb0da8170207d36112920f5fcea65fd544cf3f602583c0afc2213afce150 after=c763056ed483d1e7eb1619fa6cc20f382df4b98d21bff434fd8dfd8bfc3229e5; `query.ts` before=6dbdfc34d6ea224a3708a683313a3a53f4c0b6864175e9278e7e3c2c1b0dc9b0 after=a0d9f68043599287665d86ed30e7455cbf3492049bc0ea64e9cf8bd81839870e; `query.test.ts` before=8fbedaea428cba4d645c646872d079812094879b7efbd50f83dfcbe67ba30716 after=db4f1359c93d1cf431f4309f5e71130d4171b6cf82a722164811f14339d1eb2d; `detail/IssueDetailHeader.tsx` before=ef9823aa16dce0248915f165f602ad96ff0a7dbdb59f845e96aff398fa275240 after=69e5667338c49985f28df093e0044f30d5d6d7acdab2da8ef3ffe18fcdd562da; `IssueBulkActions.tsx` before=ca7949275641c7136a7b6855b6eeb06b961cad8e2ee3f7b3ec9b3de46aa33d53 after=5b87e15ca02010d281386d942c3aff803415dbcef2c3e1e3650f49b2207d4aab; `IssueListRows.tsx` before=6f8be8daf1308f24c1456085e20087b778280fb9d5cc55dda10440bd4ea8aa63 after=6d77d4da58c111fea1060824f351e8dcd7cc756b01cb9f9c781f3df173111b69; `IssueDetail.tsx` before=d5edb207d0faa4f528369f96aa3f83a1c097c4ac7c63819f55d7d795e114f0ba after=1e2f5360f23d58cd3303ec7a2af37013fe5ba6985c83e57ded2cb400fbedcdb0; `detail/IssueDetailSections.tsx` before=029e147ff8fdc632571de58998a0ddeba73f86b948d391f8c1e2f9bb6516a051 after=4f7813d05d23279816e8671a77f67d0420a2b7e7036b9926728e87f26fea5304; `issues.css` before=82e0e17880071e5211d66091ad228aaebeddc6e782f7f34c9b450a63dde58b51 after=90bf4bb5aba0a639c2b7e17278d78e6a2352ae466ace18bec16535cfe3ace57a; `IssueListControls.tsx` before=8a36d48bafce88ba24ea699b8f070536233b566bdb220f6c54417eabe86a1e15 after=e814f17e0ce8b5535ed1ed4fe20d52c697c926d57aa73364fdb3ff6b832ad27f; `detail/IssueDetailSidebar.tsx` before=b43f12d8d5659cde99280da1f372cb3d3536c410da25db43284aa8e31782c71e after=f06a06dfc9cc12c5cf048fa444f53bf2076bd58e64a3856948dfc588c9566296.
**Verification**: `cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v11-p15-ui-issues-install.log`); `cd src/web && bun run typecheck` -> exit 0, no TS diagnostics (log: `/tmp/telescreen-v11-p15-ui-issues-typecheck.log`); `cd src/web && bun run test` -> exit 0, 28 files and 145 tests passed (log: `/tmp/telescreen-v11-p15-ui-issues-test.log`); `cd src/web && bun run test --exclude 'src/pages/tasks/**' --exclude 'src/pages/policies/**' --exclude 'src/pages/collectors/**' --exclude 'src/pages/police/**' --exclude 'src/pages/escalation/**' --exclude 'src/pages/settings/**'` -> exit 0, 22 files and 116 tests passed (log: `/tmp/telescreen-v11-p15-ui-issues-test-own.log`); `cd src/web && bun run build --outDir /tmp/telescreen-v11-p15-ui-issues-web --emptyOutDir` -> exit 0, built in 3.39s (log: `/tmp/telescreen-v11-p15-ui-issues-build.log`); p15 typecheck scope grep -> exit 0 (no out-of-scope or p15 diagnostics).
**Notes**: The deliverable criterion remains unchecked pending the API-contract reconciliation for `forever`. All other p15 UI behaviors and required verification pass. `git diff --check -- src/web/src/pages/issues` exits 0. The plan is 161 lines, below the 400-line limit.

**Owner resolution (2026-10-10)**: `ExceptionDuration` in `src/web/src/api/types.ts` now includes `"forever"` (label `Forever (admin)` in `components/labels.ts`); the backend already enforces admin-only for forever. Offer forever only to admins in the exception dialog. Not a blocker.

### Session: 2026-10-10 Step 6 verification continuation
**Tasks Completed**: Final-source install, typecheck, tests, build and completion checks were rerun after adding the `Forever (admin)` selector option and typed-client regression test.
**Verification**: `cd src/web && bun install --frozen-lockfile` -> exit 0 (`/tmp/telescreen-v11-p15-ui-issues-install-step6-final.log`); `cd src/web && bun run typecheck` -> exit 0, no TS diagnostics (`/tmp/telescreen-v11-p15-ui-issues-typecheck-step6-final.log`); `cd src/web && bun run test --exclude 'src/pages/tasks/**' --exclude 'src/pages/policies/**' --exclude 'src/pages/collectors/**' --exclude 'src/pages/police/**' --exclude 'src/pages/escalation/**' --exclude 'src/pages/settings/**'` -> exit 0, 117 passed, including all 12 IssueDetail tests (`/tmp/telescreen-v11-p15-ui-issues-test-own-step6-final.log`); `cd src/web && bun run build --outDir /tmp/telescreen-v11-p15-ui-issues-web-step6-final --emptyOutDir` -> exit 0 (`/tmp/telescreen-v11-p15-ui-issues-build-step6-final.log`); typecheck scope filter, `git diff --check -- src/web/src/pages/issues impl-plans/active/v11-p15-ui-issues.md`, and the unchecked-criteria count all exit 0.
**Concurrent sibling run**: The unfiltered `cd src/web && bun run test` exited 1 with 145 passed and one failure of 146: `src/pages/settings/index.test.tsx > adds an identity without displaying the submitted value and saves the team lead`. This is the known v11-p16 ADV-V11-P16-001 Settings failure, outside p15 writePaths; the required p16-excluded rerun above passes. This failure is recorded here as tolerated concurrent-sibling evidence, not as a passing gate.
**Notes**: The p15 deliverable and verification criteria are complete. The API remains authoritative for admin authorization; p15 has no admin-role signal and preserves its error-banner path. Formal review and serial integration are downstream.

### Session: 2026-10-10 Step 6 continuation
**Tasks Completed**: Added the accepted `forever` exception duration to the issue detail selector as `Forever (admin)` and added a UI regression test proving the typed `issuesApi.requestException` path posts `{ reason, duration: "forever" }`. The shared `ExceptionDuration` contract now includes this value. The existing API error banner remains the authorization feedback path; the UI has no admin-role signal to pre-filter the option.
**Blockers**: None for the assigned p15 paths. Authorization remains enforced by the API.
**Verification**: Final-source install, typecheck, full frontend tests, p16-excluded tests, isolated build, diff check and completion checkbox gate are recorded in Step 6 output with complete logs.
**Notes**: This session supersedes the earlier 17:20 in-progress note about the missing duration type. Historical evidence is preserved. Formal review and serial integration remain downstream workflow work.
