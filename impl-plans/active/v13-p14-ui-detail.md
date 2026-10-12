# V13-P14 UI Issue Detail: Header, Event Navigation, Chart, Tags, Activity, Mentions, Follow, External Issues, Merge

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Issue detail); design-docs/specs/design-v1-3-sentry-parity.md sections 3 (B1, B4, B6, B7), 8, 13 (detail shortcuts)
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p14-ui-detail`
- planPath: `impl-plans/active/v13-p14-ui-detail.md`
- wave: 3
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

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p14-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p14-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p14-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p14-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p14-build.log; echo "exit=$?"
```

Expected: each command exits 0, and the whole vitest suite passes.

## Completion Criteria

- [ ] B1, B4, B6 and B7 UI regression tests pass.
- [ ] `grep -n "eventGraph\|tagCounts\|setComments" src/web/src/pages/issues` prints
  nothing.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
