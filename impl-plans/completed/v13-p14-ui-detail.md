# V13-P14 UI Issue Detail: Header, Event Navigation, Chart, Tags, Activity, Mentions, Follow, External Issues, Merge

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Issue detail); design-docs/specs/design-v1-3-sentry-parity.md sections 3 (B1, B4, B6, B7), 8, 13 (detail shortcuts)
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p14-ui-detail`
- planPath: `impl-plans/active/v13-p14-ui-detail.md`
- wave: 3 (run 2; Last Updated 2026-10-13 with the run-2 screenshot contract)
- dependsOn: `v13-p03-ui-foundation`, `v13-p13-ui-stream` (for the read-only
  `ArchiveMenu.tsx`; p13 runs in run 1 and p14 in run 2)
- writePaths:
  - `impl-plans/active/v13-p14-ui-detail.md`
  - `src/web/src/pages/issues/detail`
  - `src/web/src/pages/issues/IssueDetail.tsx`
  - `src/web/src/pages/issues/IssueDetail.test.tsx`
  - `src/web/src/pages/issues/issue-detail.css`
  - `src/web/src/pages/issues/uploadEvidence.ts`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Make the detail page match Sentry's, and fix four defects:

- B1: First and Latest were reversed;
- B4: raw ids were shown in the assignee picker;
- B6: comments were session-local and the timeline had no actor;
- B7: the chart, tags and navigation used only the first 50 events.

All data comes from the p03 client:

- `eventAt`, `tags`, `issueStats`, `activity`, `editComment`, `deleteComment`;
- `setSubscription`, `bookmark`, `externalIssues`, `linkExternal`, `createGithubIssue`,
  `githubRepos`;
- `archive`, `unarchive`, `review`, `setPriority`, `unmerge`.

Imitate the existing `IssueDetail.tsx` resource and `run()` refetch pattern and the
`IssueDetail.test.tsx` fetch stubs.

## Non-goals

- No edits in `api/**`, `components/**` or the stream files (p13). Import `ArchiveMenu` from
  `../ArchiveMenu`, which p13 owns, read-only.
- No new npm packages.

## Invariants

- Navigation buttons are Oldest, Previous, Next, Latest and Recommended. Each calls the
  selector endpoint and uses only the returned ids. "Previous" is older (B1).
- Chart, tags and navigation never compute from a loaded page of events (B7). Remove
  `eventGraph` and `tagCounts`.
- No session-local comment echo (B6). After a post, edit or delete, refetch `activity`.
- Every person rendered (assignee, actors, suggestions, mentions) uses `Avatar` plus the
  display name (B4).
- Text from the API renders through the existing `Redacted` component where it is
  masked content (snippets, locators).

## Deliverables

1. **`detail/IssueDetailHeader.tsx`**:
   - a Resolve split (resolve with reason, request verification, mark reviewed);
   - an Archive split (`ArchiveMenu`; Unarchive when archived);
   - an assignee picker: suggested owners first, each with a reason tag; members and teams
     by display name; the current value shown as a name;
   - a priority select;
   - bookmark and subscribe toggles (subscribe shows the reason);
   - copy permalink and copy masked Markdown;
   - a More menu: request exception, start workflow, upload evidence, link or create an
     external issue, merge with, unmerge.
2. **`detail/IssueDetailSections.tsx`**:
   - the chart from `issueStats` with a `PeriodPicker`;
   - tag bars with percent from `tags`;
   - navigation with "n of total";
   - a "Merged into TELE-n" banner for a child, and a "Merged issues" section with Unmerge
     for a primary;
   - an "External issues" section with a link dialog (URL and optional title) and a
     GitHub create dialog (repo select from `githubRepos`; disabled with a hint when not
     configured).
3. **`detail/IssueDetailSidebar.tsx`**: an activity feed (avatar, actor, time, item text;
   comment edit and delete only when `can_edit`; a deleted comment shows "Comment
   deleted"), a comment box with `@` autocomplete over members, suggested assignees, and
   the subscription reason.
4. **`IssueDetail.tsx`**: load wiring for the above. Remove the `comments` signal. Detail
   shortcuts through `registerShortcuts("detail", ...)`: `r` resolve, `a` assign, `e`
   archive, `b` bookmark.
5. **`detail/types.ts`**: the updated `DetailData`.

## Tests (vitest)

- B1: with the mocked selector, clicking Oldest requests `/events/oldest` and shows the
  oldest timestamp; Previous from the latest requests the `previous_id`.
- B7: the chart requests `/stats`; tags render the percentages from `/tags` (for example
  "41.5%"); no code path counts the events array (assert that the stats request is made).
- B6: after posting a comment, the activity is refetched and shows the body with the actor
  "Alice Example"; there is no duplicate local item.
- B4: the picker's current option text is the display name.
- Archive split -> POST `/archive` with the chosen kind; Unarchive appears when archived.
- Edit and delete buttons appear only when `can_edit`.
- An `@al` input suggests "alice".
- The external link dialog posts the URL. With GitHub unconfigured, the create button is
  disabled.
- A merged child shows the banner link to the primary.

- Screenshot contract (design section 15, run 2): the header's More menu trigger is a
  `button` whose accessible name is exactly "More". Add a test that asserts
  `getByRole("button", { name: "More" })` exists on the primary `v13-iss-01` and on the
  child `v13-iss-02`.

## Run-2 shared-file rules

- Read-only in this run: `src/web/src/api/**` (client, types, mock router and fixtures),
  `src/web/src/components/**`, `src/web/scripts/screenshots.ts`,
  `src/web/src/pages/issues/{IssueList*,ArchiveMenu,IssueBulkActions,StreamViews*}` and
  every other page directory. v13-p06 edits `client.ts`, the mock bulk handler and
  `IssueList*` in the same wave.
- This plan is the only wave-3 plan that runs `bun run screenshots`. Its own states are
  `issue-merged-primary` and `issue-merged-child`.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p14-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p14-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p14-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p14-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p14-build.log; echo "exit=$?"
bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p14-shots.log; echo "exit=$?"
! grep -E '^state-skipped: issue-merged-(primary|child)$' /tmp/telescreen-v13-p14-shots.log
```

Expected:

- each command exits 0, and the whole vitest suite passes;
- the screenshot run reports 0 browser errors and does not skip the two detail states.

A missing browser (`exit=2`, "No Chrome or Chromium browser found") is a recorded
verification gap, never a pass. A vitest failure caused only by v13-p06's in-progress
`IssueList*` or `api/**` edits is recorded as such.

## Completion Criteria

- [x] B1, B4, B6 and B7 UI regression tests pass.
- [x] The "More" button test passes, and the screenshot log has no `state-skipped:` line
  for `issue-merged-primary` or `issue-merged-child`.
- [x] `git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/scripts/screenshots.ts src/web/package.json src/web/bun.lock`
  has only sibling changes: v13-p06 API/mock files, v13-p16's one-line member-edit name
  change in `screenshots.ts`, and its serially reconciled `SecondaryPanel.tsx`; p14 changed
  none of these paths.
- [x] `grep -n "eventGraph\|tagCounts\|setComments" src/web/src/pages/issues` prints
  nothing.
- [x] The loader normalizes p07/design `/stats` and `/tags` bodies and the existing p03
  fixture bodies into local detail types; the server-shape test renders `41.5%`.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

### Session: 2026-10-13 — Issue detail UI implementation

**Tasks Completed**: Implemented the Sentry-style issue detail header, selector navigation,
stats chart and tag percentages, merged issue controls, external issue dialogs, activity,
editable comments, @ suggestions, display-name avatars, comment refetch (no local echo),
subscribe/bookmark/priority/archive actions, and detail shortcuts. Added B1/B4/B6/B7 and
feature regression coverage. More is an accessible button named exactly `More`.

**Tasks In Progress**: None for p14 implementation.

**Blockers**: None.

**Reconciliation requests**: None. External issue link creation accepts the optional title
through the existing `apiFetch` boundary because the shared `issuesApi.linkExternal` helper
only accepts a URL; no shared API source was edited.

**Hashes** (SHA-256):

- `src/web/src/pages/issues/IssueDetail.tsx` — `679452d16e7a3bfd6c9c450861659b598a9d5865507e2fdc1deaee3927e49fd9`
- `src/web/src/pages/issues/IssueDetail.test.tsx` — `7abbbd4d93e956b8d4ef6b07f9e1897c6222a38580799a78548699c793a70d72`
- `src/web/src/pages/issues/issue-detail.css` — `492d17d23e74eaea68a71165473edbdf07ac5d204d2c61c6e51d62fd8658cc7f`
- `src/web/src/pages/issues/detail/types.ts` — `ecb063c1e85879958190316672e557fa79ce1003eee07f615157f96af87df8e4`
- `src/web/src/pages/issues/detail/IssueDetailHeader.tsx` — `164ef160fc56659eb81de91bde953c4a65456a00a64aaef3d61360fd6a9b9520`
- `src/web/src/pages/issues/detail/IssueDetailSections.tsx` — `9542e6fbc27e355d436bffa2ff50bb73f28889e71913db1f64f9ad123c17b5f2`
- `src/web/src/pages/issues/detail/IssueDetailSidebar.tsx` — `a5d2847658092076b70cc73d0ab4bec470fd8b1e0ed81386789d409b928fad0f`

**Verification**:

- `cd src/web && bun install --frozen-lockfile` — exit 0; `/tmp/telescreen-v13-p14-install.log`.
- `cd src/web && bun run typecheck` — exit 0; `/tmp/telescreen-v13-p14-typecheck-final.log`.
- `cd src/web && bun run test` — exit 0; 42 files, 232 tests passed; `/tmp/telescreen-v13-p14-test-final.log`.
- `cd src/web && bun run build --outDir /tmp/telescreen-v13-p14-web --emptyOutDir` — exit 0; `/tmp/telescreen-v13-p14-build-final.log`.
- `cd src/web && bun run screenshots` — exit 0; `/tmp/telescreen-v13-p14-shots-final.log`. No browser, HTTP, or console error diagnostics; no skip line for either merged state. Primary and child screenshots exist in light and dark modes under `/tmp/telescreen-v13-p03-screenshots/`.
- `! rg -n 'eventGraph|tagCounts|setComments' src/web/src/pages/issues` — exit 0; no matches.
- Owned-path status check lists only the seven changed files above, all inside p14 `writePaths`; `uploadEvidence.ts` is unchanged.
- The combined-tree forbidden-path diff lists p06 `api/client.ts`, `api/mock/router.ts`, and `api/mock/mock.test.ts`, plus p16 `screenshots.ts` (1/1) and serial-reconciliation `components/shell/SecondaryPanel.tsx`. These are sibling changes; p14 wrote none of them.

Formal test-integrity, adversarial, and integration reviews remain downstream workflow steps.

### Session: 2026-10-13 — TI-E3-P14-01 archive contract repair

**Finding Addressed**: `TI-E3-P14-01` (mid). `IssueDetail.archive()` passes the
`ArchiveRequest` through unchanged, so the detail page and the `e` shortcut send
`duration_minutes` as required by the server. The `ArchiveMenu` remains read-only.

**Tests Added**: The 24-hour archive test asserts `kind: for_duration`,
`duration_minutes: 1440`, and absence of `duration_days`. The archived issue test asserts
Unarchive visibility and its POST `/unarchive` request. Before the production fix, the
archive test failed because `duration_minutes` was undefined; after the fix both new tests
pass. No existing test changed or was weakened.

**Reconciliation requests**: v13-p03 / serial integration owns
`src/web/src/api/client.ts:195`; its `issuesApi.archive` type names `duration_days`, while
the server and mock use `duration_minutes`. p14 did not edit `api/**`.

**Current hashes** (SHA-256):

- `src/web/src/pages/issues/IssueDetail.tsx` — `d100e913ce75b04da990617cad944f5be36ebd5382374a6546cfb4594581b866`
- `src/web/src/pages/issues/IssueDetail.test.tsx` — `f64f6fda54be608388bf0fcdc8f86670bedfd0d5d9c1be094e2882438dabaca3`
- `src/web/src/pages/issues/issue-detail.css` — `492d17d23e74eaea68a71165473edbdf07ac5d204d2c61c6e51d62fd8658cc7f`
- `src/web/src/pages/issues/detail/types.ts` — `ecb063c1e85879958190316672e557fa79ce1003eee07f615157f96af87df8e4`
- `src/web/src/pages/issues/detail/IssueDetailHeader.tsx` — `164ef160fc56659eb81de91bde953c4a65456a00a64aaef3d61360fd6a9b9520`
- `src/web/src/pages/issues/detail/IssueDetailSections.tsx` — `9542e6fbc27e355d436bffa2ff50bb73f28889e71913db1f64f9ad123c17b5f2`
- `src/web/src/pages/issues/detail/IssueDetailSidebar.tsx` — `a5d2847658092076b70cc73d0ab4bec470fd8b1e0ed81386789d409b928fad0f`

**Verification**:

- `cd src/web && bun run test -- src/pages/issues/IssueDetail.test.tsx` — exit 0, 20 passed; `/tmp/telescreen-v13-p14-ti01-focused.log`.
- `set -o pipefail; cd src/web && bun run typecheck` — exit 0; `/tmp/telescreen-v13-p14-typecheck-ti01-final.log`.
- `set -o pipefail; cd src/web && bun run test` — exit 0, 42 files and 234 tests passed; `/tmp/telescreen-v13-p14-test-ti01-final.log`.
- `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v13-p14-web --emptyOutDir` — exit 0; `/tmp/telescreen-v13-p14-build-ti01-final.log`.
- `set -o pipefail; cd src/web && bun run screenshots` — exit 0; `/tmp/telescreen-v13-p14-shots-ti01-final.log`. The merged primary and child were captured in light and dark modes; diagnostic/skip scan is clean.
- `! grep -n 'duration_days' src/web/src/pages/issues/IssueDetail.tsx` — exit 0; no match.
- `! rg -n 'eventGraph|tagCounts|setComments' src/web/src/pages/issues` — exit 0; no matches.

**Red-first Evidence**: The new tests ran before the production fix: 19 passed, 1 failed
because the request omitted `duration_minutes`; log `/tmp/telescreen-v13-p14-ti01-focused-before-fix.log`.

### Session: 2026-10-13 — ADV-E3-P14-01 server response normalization

**Finding Addressed**: `ADV-E3-P14-01` (mid). The detail loader now normalizes both the
p03 fixture shape and the p07/design 8.3 server shapes before returning `DetailData`.
Stats accept `{buckets:[{start,count}]}` or `{items:[{at,count}],total}`. Tags accept
flat p03 `{items:[{key,value,count}],total}` or nested server
`{total,tags:[{key,values:[{value,count,percent}],other_count}]}`. Malformed entries are
skipped, missing arrays become empty, and server percentages are rendered as supplied.
`IssueDetailSections` consumes only the normalized shape. No API types or mock files were
changed.

**Regression Test**: Added `renders /stats and /tags in the server contract shape`, using
the p07 stats body and design 8.3 tags body and asserting the page renders, makes the stats
request, and shows `detector: rule · 41.5%`. The existing p03-shape test remains unchanged.
Red-first run `/tmp/telescreen-v13-p14-adv01-focused-before-fix.log` reproduced the
`stats.buckets.map` TypeError (20 passed, 1 failed); final focused run passed 21/21 at
`/tmp/telescreen-v13-p14-adv01-focused.log`.

**Reconciliation Requests**: v13-p03 or serial integration owns
`src/web/src/api/types.ts` and `src/web/src/api/mock/router.ts:190-191`: align the tag
distribution type and `/tags` and `/stats` mock responses with design 8.3 and p07, and
settle the per-issue stats key (`items/at` or `buckets/start`). The prior request for
`src/web/src/api/client.ts:195` to change archive `duration_days` to `duration_minutes`
also remains open. p14 edited none of these shared files.

**Current hashes** (SHA-256):

- `src/web/src/pages/issues/IssueDetail.tsx` — `17b09cdef72cd790fe31b6c7e036e8a4c8333db25fa92adc14496d34ba95fd20`
- `src/web/src/pages/issues/IssueDetail.test.tsx` — `093e548ed0f2766b0701eeee57de5ea4b675383e337cf90df5e5a9fa98eb10ca`
- `src/web/src/pages/issues/detail/types.ts` — `f35296d170c1a5da0b5f291be6608808fa39f1a2bc24a32779a845278dd1c5f1`
- `src/web/src/pages/issues/detail/IssueDetailSections.tsx` — `2c2e51aff8bc045937e1cf9d5c1e96efb5a57638a18ee823df9809ab9df34ec2`

**Verification**:

- `set -o pipefail; cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p14-install.log 2>&1` — exit 0.
- `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p14-typecheck.log` — exit 0.
- `set -o pipefail; cd src/web && bun run test -- src/pages/issues/IssueDetail.test.tsx 2>&1 | tee /tmp/telescreen-v13-p14-adv01-focused.log` — exit 0, 21 passed.
- `set -o pipefail; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-v13-p14-test.log` — exit 0, 42 files and 235 tests passed.
- `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v13-p14-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p14-build.log` — exit 0.
- `set -o pipefail; cd src/web && bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p14-shots.log` — exit 0; no browser/HTTP/console or merged-state skip diagnostics; merged primary and child screenshots exist in light and dark modes.
- Screenshot, event-counting, screenshots.ts 1/1, and unchecked-box guards — exit 0.

Formal test-integrity, adversarial, and integration decisions remain downstream workflow steps.
