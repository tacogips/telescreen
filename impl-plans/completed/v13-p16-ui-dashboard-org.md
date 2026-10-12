# V13-P16 UI Dashboard and Organization: Dashboard, Onboarding, Integrations, Tokens, Ownership Rules, Member Edit, Workflows List

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Dashboard, Settings, Workflows list); design-docs/specs/design-v1-3-sentry-parity.md sections 11, 12, 13 (onboarding, workflows list), D26, D28
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p16-ui-dashboard-org`
- planPath: `impl-plans/active/v13-p16-ui-dashboard-org.md`
- wave: 2 (run 2; Last Updated 2026-10-13 with the run-2 screenshot contract)
- dependsOn: `v13-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v13-p16-ui-dashboard-org.md`
  - `src/web/src/pages/dashboard`
  - `src/web/src/pages/settings`
  - `src/web/src/pages/workflows`
  - `src/web/scripts/screenshots.ts`
- sharedPaths: none
- sharedPathNotes:
  - `src/web/scripts/screenshots.ts`: created by v13-p03, edited by v13-p13 (run 1). This
    plan is its single run-2 editor (design section 15). Change ONLY the `member-edit`
    action name from "Edit Alex" to "Edit Alex Morgan". Keep every other state,
    selector and the `TELESCREEN_SCREENSHOTS_STRICT` handling byte-identical.
- artifactRoots: none

This plan creates `pages/dashboard/index.tsx` (discovered by `routes.tsx`) and owns all of
`pages/settings/`, including the new sub-routes `profile`, `integrations`, `tokens` and
`ownership` and the secondary-panel entries.

`/settings/profile` renders v13-p15's `pages/profile/ProfilePage.tsx`, which runs in
parallel. Load it with `import.meta.glob("../profile/ProfilePage.tsx")` plus `lazy`, and
fall back to `Unavailable` when the glob is empty, so that this plan typechecks and runs
without p15. Do not import it statically.

Read `impl-plans/conventions.md` first.

## Intent and Context

The pages are:

- the overview dashboard, with the onboarding checklist;
- the Settings pages for integrations, admin tokens, ownership rules and member
  edit/deactivate (in Users and roles);
- the enriched workflows list.

Use the p03 client (`dashboardApi`, `integrationsApi`, existing `tokens` calls,
`ownershipApi`, `membersApi`, `workflowsApi`), the components (`PeriodPicker`, `Avatar`,
`MiniBarChart`, `Table`) and `useAuth()`.

Imitate:

- `pages/settings/UsersRoles.tsx` (a table with inline errors, such as `last_admin`);
- `components/ui/MiniBarChart.tsx` (SVG only);
- `pages/workflows/index.tsx` (the list).

## Non-goals

- No new chart library. Stacked bars and lines are hand-rendered SVG inside
  `pages/dashboard`.
- No onboarding backend (D28): the checklist derives from existing endpoints.
  - An item whose endpoint the user cannot read (a 403, or a missing permission) is
    hidden.
  - Dismissal is stored under the localStorage key `telescreen.onboarding.dismissed`.
- No edits outside the three page directories.

## Invariants

- Every dashboard tile links to a stream query, for example unassigned high priority ->
  `/issues?query=is:unresolved assigned:none priority:high`.
- Integrations show only the booleans, times and codes from the API. A Test button
  appears with `integrations.test`, and its result is shown inline.
- Ownership rules:
  - reorder with move up and down calls `reorder` with the full id list;
  - the "Test locator" box shows the matched rule or "No match";
  - a 422 shows "The value looks like a secret and was not saved", without echoing the
    input.
- Admin tokens: the secret is shown once (the same rule as p15).
- Users and roles: Edit (display name, email, Slack id) and Deactivate or Reactivate. A
  409 `last_admin` shows inline. Deactivated members are tagged.

## Deliverables

1. **`pages/dashboard/index.tsx`** (with helpers in the same directory):
   - `PeriodPicker` (default 14d);
   - stacked severity bars, and new vs resolved as two series in SVG;
   - open by exposure and by source as horizontal bars;
   - tiles for MTTR and MTTA (hours, with one decimal; "-" when null), unassigned high
     priority, archived and escalating;
   - a top-issues table with links;
   - `OnboardingChecklist.tsx` with items: connect a collector (`/api/collectors` has an
     item), configure notifications (`/api/integrations` slack or email configured),
     invite users (`/api/members` more than 1 active), create an alert rule
     (`/api/alert-rules` has an item). Each item has a docs link to a README anchor.
2. **`pages/settings/IntegrationsSettings.tsx`**: one card per kind, plus a "Collectors"
   card summarizing `/api/collectors` by kind.
   **`TokensSettings.tsx`**: list, create (label, scopes, member) and revoke.
   **`OwnershipRules.tsx`**: an ordered table, a create and edit dialog (source select,
   pattern, team select, enabled), and the test box.
   **`UsersRoles.tsx`**: member edit and deactivate.
   **`index.tsx`**: the sub-routes and panel entries for Profile (glob-loaded, see above),
   Integrations, Tokens and Ownership rules. Integrations and Tokens are hidden without
   `integrations.read` and `tokens.read`. Update `index.test.tsx` for the new entries.
3. **`pages/workflows/index.tsx`**: columns name, trigger events (tags), active, last run
   (relative) and 30-day run counts (small tags).

## Tests (vitest)

- The dashboard renders the tiles from mock data. MTTR null shows "-". The unassigned
  tile's link has the expected query. Changing the period refetches with `period=7d`.
- Onboarding:
  - shows 4 items with the right done states from the mocks;
  - hides integrations for a viewer;
  - dismiss persists.
- Integrations: the Test button appears only for admins; a test result renders ok or the
  error code.
- Ownership: move-down calls reorder with the swapped ids; a test locator match renders;
  a 422 shows the generic message.
- Users: deactivating the last admin shows the `last_admin` inline error; edit submits the
  changed fields only.
- Workflows list: trigger tags and run counts render.

- Screenshot contract (design section 15, run 2):
  - each Users and roles row's Edit button is named `Edit <display_name>`, for example
    "Edit Alex Morgan" for the mock member `alex`. Test it with `getByRole("button",
    { name: "Edit Alex Morgan" })`;
  - the dashboard has a `button` named exactly "Getting started" that opens the
    onboarding checklist. Test it.

## Run-2 shared-file rules

- Read-only in this run: `src/web/src/api/**` (client, types, mock router and fixtures),
  `src/web/src/components/**`, `src/web/src/routes.tsx`, `pages/alerts`, `pages/profile`
  (v13-p15, same wave) and every other page directory.
- `screenshots.ts`: only the one action-name change described in the sharedPathNotes.
- The existing tests in `pages/settings/*.test.tsx` and `pages/workflows/*.test.ts(x)`
  are inside this plan's writePaths. Change an assertion only when this plan's accepted
  design change breaks it, and record the reason in the Progress Log.
- This plan is the single wave-2 screenshot runner. Its own states are `dashboard`,
  `integrations`, `tokens`, `ownership-rules`, `member-edit`, `workflows-list` and
  `onboarding`. The alerts and profile states belong to v13-p15 and may be skipped while
  p15 is in progress.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p16-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p16-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p16-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p16-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p16-build.log; echo "exit=$?"
bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p16-shots.log; echo "exit=$?"
! grep -E '^state-skipped: (member-edit|onboarding)$' /tmp/telescreen-v13-p16-shots.log
test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"
```

Expected:

- each command exits 0, including the existing settings and workflow page tests;
- the screenshot run has 0 browser errors and no skip for `member-edit` or `onboarding`.

A missing browser (`exit=2`) is a recorded verification gap, never a pass. A vitest or
screenshot failure caused only by v13-p15's in-progress files is recorded as such.

## Completion Criteria

- [x] The "Edit Alex Morgan" and "Getting started" tests pass, and the screenshot log has
  no `state-skipped:` line for `member-edit` or `onboarding`.
- [x] `git diff HEAD -- src/web/scripts/screenshots.ts` shows exactly one changed line
  (the member-edit action name).

- [x] The dashboard, onboarding, integrations, tokens, ownership rules, member edit and
  workflows list are implemented per section 11.2.
- [x] No new dependency (`git diff src/web/package.json` is empty).
- [x] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)

### Session 1 — 2026-10-13 (Step 6 implementation)

- **Tasks completed:** Dashboard and onboarding checklist, integrations, admin tokens,
  ownership rules, member edit/deactivate, enriched workflows list, screenshot action
  name, and regression tests. `/settings/profile` uses p15's page through `import.meta.glob`
  with an unavailable fallback. Member edits submit changed fields only; ownership test
  errors are generic and do not echo the locator. Local adapters handle p10/p11 contract
  fields absent from the shared client without editing shared API files.
- **In progress:** Settings sub-route links render in the page-local navigation; the
  design's secondary-panel links remain pending because the shell component is outside
  this plan's authorized write paths.
- **Reconciliation request:** Add
  `src/web/src/components/shell/SecondaryPanel.tsx` to a serial owner's write paths (or
  assign its settings entries to that owner). Add Profile, Integrations, Tokens and
  Ownership links there, hiding Integrations and Tokens without their read permissions.
  This file is not in p16's `writePaths`; no edit was made. Until reconciled, criterion 3
  remains incomplete and this plan is not complete.
- **Verification:**
  - `cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p16-install.log 2>&1` — exit 0; 175 installs checked, no changes.
  - `cd src/web && bun run typecheck > /tmp/telescreen-v13-p16-typecheck.log 2>&1` — exit 0.
  - `cd src/web && bun run test > /tmp/telescreen-v13-p16-test.log 2>&1` — exit 0; 42 files and 217 tests passed, 0 failed.
  - `cd src/web && bun run build --outDir /tmp/telescreen-v13-p16-web --emptyOutDir > /tmp/telescreen-v13-p16-build.log 2>&1` — exit 0; Vite build completed.
  - `cd src/web && bun run screenshots > /tmp/telescreen-v13-p16-shots.log 2>&1` — exit 0; browser ran with no browser/console/HTTP errors; `member-edit` and `onboarding` were not skipped. Light and dark screenshots exist for both states.
  - `cd src/web && bun run test -- src/pages/dashboard/index.test.tsx src/pages/settings/UsersRoles.test.tsx src/pages/settings/OrganizationSettings.test.tsx src/pages/workflows/index.test.tsx > /tmp/telescreen-v13-p16-focused-final.log 2>&1` — exit 0; 4 files and 16 tests passed, 0 failed.
  - `! grep -E '^state-skipped: (member-edit|onboarding)$' /tmp/telescreen-v13-p16-shots.log` — exit 0.
  - `! grep -E 'pageerror|console error|HTTP error|Browser reported' /tmp/telescreen-v13-p16-shots.log` — exit 0.
  - `test "$(git diff --numstat HEAD -- src/web/scripts/screenshots.ts | awk '{print $1 "/" $2}')" = "1/1"` — exit 0; only the member-edit accessible name changed.
  - `git diff --quiet HEAD -- src/web/package.json src/web/bun.lock` — exit 0; no dependency changes.
  - `git diff --quiet HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx` — exit 0; shared/read-only paths unchanged.
- **Pre-node/current SHA-256 (changed source files):**
  - `src/web/scripts/screenshots.ts` before `93484cc881da05c7da49f8af3e05f88a40b9dee73e0a75c9aeac04f4a4df52ca` after `622b972b538ac1adffb5dfe5529d58ab9a27d638abfdf8cd15e62c81d44f41f4`.
  - `src/web/src/pages/dashboard/OnboardingChecklist.tsx` before `absent` after `49b41b9da03ded201a933b3d4c1c75be5aaff746faa4d3e1e773c4196be94f77`; `dashboard.css` before `absent` after `1b7b160c17a160a01c05aac443d287596a04314a0efb0fca3c600b20a9728f3d`; `index.test.tsx` before `absent` after `d4e157c8ff4ac6d8225eb1914c16080841a93a88d53821a8f6c1188163257eb1`; `index.tsx` before `absent` after `500772602d6f261b4d58da962e8347665d1b58cbbd3ee5b4249b55049094b8f8`.
  - Settings: `IntegrationsSettings.tsx` before `absent` after `eac8a5a7745ff88e9edc589425f4f4bf0a51c2afd9616374409180e444645feb`; `OrganizationSettings.test.tsx` before `absent` after `727002b990382859ebc1aa62fd93d7607326b34861061fb8199a598ddbc29e18`; `OwnershipRules.tsx` before `absent` after `766da6ea48954c3f21896d7c0fb76efb63ea2bd5889591d7628054a7b31cd02d`; `TemplatesList.test.tsx` before `e06e27f1a117ff943ae642aad2d12a5489c1324e5bfe59c288ac73397bebd596` after `3c2279a0e667e4e49612a26335d651361df63e8c3887dc219438bf0f4e8cd2c4`; `TokensSettings.tsx` before `absent` after `7f0f0ff594c94fa8473207c160402d3d3bed56d3693c21653c02fcce8753c818`; `UsersRoles.test.tsx` before `1db896731fa8581adf9570ae326c97b49c122541b3483deaf9420d1024c3e3ce` after `23545d9ed1e23009e37ee5c2ca238681aac21efe877d6b7b28358c3ac0c9d623`; `UsersRoles.tsx` before `e12a74ecddf551de90edb5f3f118e3ef214a1104aaf9bdc2a2b7f30ecd22bbb4` after `f0caddab99f254d6d7198167eaf3402e30407649b4d9aa7867dba22c7fc431ad`; `index.tsx` before `eaec6ca882f4bdd551dfeb8857d122dbc13bbc03b57bb67df92a18a9170c4bb9` after `ea3f9f59616459949e47b233f90c7abdac7c79f3566e7262cfb6055cb4eb76d7`; `settings.css` before `fba3e27be5e9bb477bafa0df8db6bae6a7e8b369c30753dc5d183f0b749f8d16` after `32776c5a3c5f30015e32cfc3009ecfaee4ca6ac9b15a7cf7868dfad4f62c379f`.
  - `src/web/src/pages/workflows/index.test.tsx` before `absent` after `45835dd51113235786b055f60e0ef1fc666f5ca7fcbf14eb6b3c28862624eed7`; `index.tsx` before `1ede3e1e339c425d3fa822a20dd964fd1c18faac25ba8ca545200077ecb8bd66` after `56ec90dc6b85bf2c4ec9f346ba26e421a3902fa908146c92538e937521f03bf7`.
- **Notes:** The earlier focused run `/tmp/telescreen-v13-p16-focused.log` exited 1 (14 tests: 12 passed, 2 failed: ambiguous dashboard `-` text and changed member-row accessible name); `/tmp/telescreen-v13-p16-focused-rerun.log` then exited 0 (14/14). Later page-title assertions exposed by settings navigation were corrected; final focused and whole-suite logs above pass on the current source. No review findings were supplied (`reviewFeedback.findings` empty). Formal review and integration remain downstream.

### Serial reconciliation (session 13, run 2, after the wave-2 join)

- Owner: the serial reconcile-implementations writer (not a Luna worker). Pre and post SHA-256 of every touched file: `tmp/telescreen-v13-20261013-e3/reconcile/pre-hashes.txt`, `tmp/telescreen-v13-20261013-e3/reconcile/post-hashes.txt`.
- Combined tree: `uv run --frozen ruff format --check --no-cache .`, `ruff check --no-cache .`, `python tools/typelint.py` (489 files clean), `ty check` -> exit 0; `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 947 passed / 8 skipped (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-all.log`); `uv build` -> exit 0; web install/typecheck/test (223 passed)/build/screenshots -> exit 0, 0 browser errors, no `state-skipped` lines (`tmp/telescreen-v13-20261013-e3/reconcile/web-*.log`).
- Repair: `src/web/src/components/shell/SecondaryPanel.tsx` adds Profile (`profile.self`), Integrations (`integrations.read`), Tokens (`tokens.read`) and Ownership rules (`ownership.read`), the same gates as the page-local navigation. Regression test in `src/web/src/shell.test.tsx`; it fails against the HEAD panel (`tmp/telescreen-v13-20261013-e3/reconcile/telescreen-v13-reconcile-shell-negative.log`, exit 1) and passes now. Criterion 3 is ticked.

### Session: 2026-10-13 02:21 UTC — INT-E3-P16-01 implementation rerun
**Tasks Completed**: Confirmed the serially reconciled Settings secondary-panel entries and permission gates with the shell regression test; reran the p16 frontend gates against the current combined tree. No source files were edited in this session.
**Tasks In Progress**: Independent test-integrity and adversarial decisions are owned by the downstream review steps; no review approval is claimed here.
**Blockers**: None for p16 implementation. The original HEAD-based shared-path guard sees the already-reconciled `src/web/src/components/shell/SecondaryPanel.tsx`; its unchanged hash is recorded below. A scoped guard excluding that one known reconciliation path passes, and no API, route, dependency, or other shared component path changed.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/v13-p16-ui-dashboard-org.md` before=`24c1b5b333fe2dfcac8747b94261967161883f4d1d6d8fd90225ce0d18ebaaa4`; after=see `tmp/telescreen-v13-20261013-e3/p16-step6-review-INT-E3-P16-01/final-hashes.txt`. Reconciled `src/web/src/components/shell/SecondaryPanel.tsx`=`50ce1b3b04306499c53fdb44f5393eaa575a45d6ddb8d9e62b4b84dc5791907b`; `src/web/src/shell.test.tsx`=`66690cfc2a5b10c7b1a615d32ea211e8482507821e34ec57a7264ae2071e10e9`; owned `src/web/scripts/screenshots.ts`=`622b972b538ac1adffb5dfe5529d58ab9a27d638abfdf8cd15e62c81d44f41f4`.
**Verification**:
  - `set -o pipefail; cd src/web && bun install --frozen-lockfile` -> exit 0; log `/tmp/telescreen-v13-p16-install-rerun.log`.
  - `set -o pipefail; cd src/web && bun run typecheck` -> exit 0; log `/tmp/telescreen-v13-p16-typecheck-rerun.log`.
  - `set -o pipefail; cd src/web && bun run test` -> exit 0; 42 files, 223 tests passed, 0 failed; log `/tmp/telescreen-v13-p16-test-rerun.log`.
  - `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v13-p16-web-rerun --emptyOutDir` -> exit 0; log `/tmp/telescreen-v13-p16-build-rerun.log`.
  - `set -o pipefail; cd src/web && bun run screenshots` -> exit 0; log `/tmp/telescreen-v13-p16-shots-rerun.log`; seven p16 states have light and dark PNGs under `/tmp/telescreen-v13-p03-screenshots/` (89 total screenshots), with no browser/console/HTTP errors and no skipped `member-edit` or `onboarding` state.
  - `cd src/web && bun run test -- src/shell.test.tsx` -> exit 0; 1 file / 13 tests passed, 0 failed; log `/tmp/telescreen-v13-p16-shell-reconcile-rerun.log`.
  - `! grep -E '^state-skipped: (member-edit|onboarding)$' /tmp/telescreen-v13-p16-shots-rerun.log` -> exit 0; log `/tmp/telescreen-v13-p16-required-screenshot-states-rerun.log`.
  - `! grep -Ei 'pageerror|console error|HTTP error|Browser reported|\\[http ' /tmp/telescreen-v13-p16-shots-rerun.log` -> exit 0; log `/tmp/telescreen-v13-p16-browser-errors-rerun.log`.
  - `test "$(git diff --numstat HEAD -- src/web/scripts/screenshots.ts | awk '{print $1 "/" $2}')" = "1/1"` -> exit 0; log `/tmp/telescreen-v13-p16-screenshot-diff-rerun.log`.
  - Shared-path guard, excluding the one previously reconciled `SecondaryPanel.tsx`: exit 0; log `/tmp/telescreen-v13-p16-shared-paths-excluding-reconcile-rerun.log`. The exact HEAD guard without this exclusion sees the serial reconciliation change; no such file was edited in this session.
  - Every p16 completion checkbox remains checked. Per-edit intent is preserved in `tmp/telescreen-v13-20261013-e3/p16-step6-review-INT-E3-P16-01/plan-edit-intent.md`.
**Notes**: INT-E3-P16-01's implementation blocker is resolved by serial reconciliation, and the p16 implementation is complete. Independent typed review decisions remain downstream; this Step 6 entry does not claim them.

### Session: 2026-10-13 02:30 UTC — P16-TI-01 test-integrity repair

- **Finding addressed:** `P16-TI-01` on the workflows list. The page now reads `trigger_events`, `last_run_at`, and `runs_30d` from enriched `/api/workflows` items through a page-local `WorkflowListItem` adapter. It selects the active version (or latest when none is active), defaults missing values to no triggers, no last run, and zero counts, and renders the total plus nonzero status tags. It no longer imports or calls `runsApi`.
- **Regression coverage:** `src/web/src/pages/workflows/index.test.tsx` supplies p08's contracted fields, including a trigger with no matching run data, verifies active-version selection, the five-run total and status counts, relative time, `-` and `Never` fallbacks, and no `/api/runs` request. The strengthened test failed against the old implementation (2 tests failed) and passes against this implementation (2 passed).
- **Reconciliation request:** Update shared `src/web/src/api/types.ts` (`WorkflowVersionRef`) and `src/web/src/api/mock/router.ts` `/api/workflows` fixtures to include p08's `trigger_events`, `last_run_at`, and `runs_30d` fields for integration/p17 ownership. They remain unedited under p16's run-2 read-only API constraint; the page-local adapter preserves legacy mock compatibility meanwhile.
- **Verification:**
  - `set -o pipefail; cd src/web && bun run test -- src/pages/workflows/index.test.tsx 2>&1 | tee /tmp/telescreen-v13-p16-workflows-ti01.log` — exit 0; 1 file, 2 tests passed, 0 failed.
  - `set -o pipefail; cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p16-install.log 2>&1` — exit 0.
  - `set -o pipefail; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p16-typecheck.log` — exit 0.
  - `set -o pipefail; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-v13-p16-test.log` — exit 0; 42 files, 224 tests passed, 0 failed.
  - `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v13-p16-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p16-build.log` — exit 0; Vite built 2367 modules.
  - `set -o pipefail; cd src/web && bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p16-shots.log` — exit 0; refreshed 89 screenshots under `/tmp/telescreen-v13-p03-screenshots/`; no screenshot skip or browser/console/HTTP error lines. The member-edit, onboarding, and workflows-list light screenshots were refreshed at 02:28–02:29 UTC.
  - `set -o pipefail; ! grep -E '^state-skipped: (member-edit|onboarding)$' /tmp/telescreen-v13-p16-shots.log && ! grep -Ei 'pageerror|console error|HTTP error|Browser reported|\[http ' /tmp/telescreen-v13-p16-shots.log` — exit 0.
  - `test "$(git diff --numstat HEAD -- src/web/scripts/screenshots.ts | awk '{print $1 "/" $2}')" = "1/1"` — exit 0.
  - `test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"` — exit 0; protected API, route, dependency, and lock paths unchanged.
  - `test "$(grep -c '^- \[ \]' impl-plans/active/v13-p16-ui-dashboard-org.md)" -eq 0` — exit 0; no unchecked completion criteria.
- **Prior attempts retained:** `/tmp/telescreen-v13-p16-workflows-ti01-before-fix.log` records the expected red test against the old runs-derived logic (2 failures). The first typecheck attempt exposed an overly broad spy return annotation; after changing the fixture helper to return `void`, final typecheck and focused/full tests pass.
- **Hashes:** pre-edit workflow page SHA-256 `56ec90dc6b85bf2c4ec9f346ba26e421a3902fa908146c92538e937521f03bf7`; final workflow page `8a0e9136fe8b56002b67ca7f256a63477b9b2c7ea402b2d11dfc28666afcc999`. Test pre-repair SHA-256 `45835dd51113235786b055f60e0ef1fc666f5ca7fcbf14eb6b3c28862624eed7`; final test `063cdefb26ce3e58c64e29f2761f5925ca90559fa8f84125421d465bb5d6ebf0`. Plan pre-session SHA-256 `b4850d7cc2a9881953326e5449fcd3de1f9f5e4fd7d6d7ac1c61851527c39b52`; final plan SHA-256 is recorded in `tmp/telescreen-v13-20261013-e3/p16-step6-review-P16-TI-01/final-hashes.txt`.
- **Status:** Implementation complete. Independent test-integrity/adversarial re-review and integration acceptance remain downstream; no approval is claimed here.

### Session: 2026-10-13 02:39 UTC — P16-ADV-01 adversarial repair

- **Finding addressed:** `P16-ADV-01`. `TokensSettings.tsx` now offers exactly the backend `ApiScope` values `read`, `write`, `admin`, and `ingest`, and defaults to `read`. The admin token test now asserts the exact checkbox set, excludes `issues.read`, and verifies the POST body uses `scopes: ["read"]`. Existing once-shown-secret behavior remains asserted.
- **Red-first evidence:** The first focused attempt `/tmp/telescreen-v13-p16-adv01-before-fix.log` exited 1 before the auth page had rendered; it exposed an async test setup race. After awaiting the Token label, `/tmp/telescreen-v13-p16-adv01-before-fix-rerun.log` exited 1 on the intended mismatch: actual `issues.read/issues.write/workflows.read/workflows.write` versus expected `read/write/admin/ingest` (5 passed, 1 failed). The fixed focused suite then passed 6/6.
- **Hashes:** `src/web/src/pages/settings/OrganizationSettings.test.tsx` before=`727002b990382859ebc1aa62fd93d7607326b34861061fb8199a598ddbc29e18` after=`7b31d5526c86ae912932f85c136fc77b79ad1ec5381893c74fc9199f74770127`; `src/web/src/pages/settings/TokensSettings.tsx` before=`7f0f0ff594c94fa8473207c160402d3d3bed56d3693c21653c02fcce8753c818` after=`9e8f1168a9451d4ff53f46433f1f10359a7f79947aee1671042668fd5d385dc8`. Plan before=`aca8e0325ae29af86b1902104d6ecef152102dcdcc3244ddbdc65ceb94784f50`; post-edit plan hash is in `tmp/telescreen-v13-20261013-e3/p16-step6-review-P16-ADV-01/final-hashes.txt`.
- **Verification (final source):**
  - `set -o pipefail; bun run test -- src/pages/settings/OrganizationSettings.test.tsx 2>&1 | tee /tmp/telescreen-v13-p16-adv01-focused.log` — exit 0; 1 file, 6 tests passed, 0 failed.
  - `set -o pipefail; bun install --frozen-lockfile > /tmp/telescreen-v13-p16-adv01-install.log 2>&1` — exit 0.
  - `set -o pipefail; bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p16-adv01-typecheck.log` — exit 0.
  - `set -o pipefail; bun run test 2>&1 | tee /tmp/telescreen-v13-p16-adv01-test.log` — exit 0; 42 files, 224 tests passed, 0 failed.
  - `set -o pipefail; bun run build --outDir /tmp/telescreen-v13-p16-adv01-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p16-adv01-build.log` — exit 0; Vite built 2367 modules.
  - `set -o pipefail; bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p16-adv01-shots.log` — exit 0; 89 PNG screenshots present, including light and dark Tokens screenshots.
  - `! grep -E '^state-skipped: (member-edit|onboarding)$' /tmp/telescreen-v13-p16-adv01-shots.log` — exit 0; required states were not skipped.
  - `! grep -Ei 'pageerror|console error|HTTP error|Browser reported|\[http ' /tmp/telescreen-v13-p16-adv01-shots.log` — exit 0; no browser error output.
  - `test "$(git diff --numstat HEAD -- src/web/scripts/screenshots.ts | awk '{print $1 "/" $2}')" = "1/1"` — exit 0; required screenshot action-name diff preserved.
  - `test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"` — exit 0; no API, route, dependency, or lockfile changes. This repair changed only the two listed settings files; previously reconciled shell files and sibling backend changes remain untouched.
  - `test "$(grep -c '^- \[ \]' impl-plans/active/v13-p16-ui-dashboard-org.md)" -eq 0` — exit 0; all completion criteria remain checked.
  - `git diff --check -- src/web/src/pages/settings/TokensSettings.tsx src/web/src/pages/settings/OrganizationSettings.test.tsx impl-plans/active/v13-p16-ui-dashboard-org.md` — exit 0.
- **Per-edit intent:** `tmp/telescreen-v13-20261013-e3/p16-step6-review-P16-ADV-01/{test-edit-intent.md,plan-edit-intent.md}`.
- **Review status:** P16-TI-01 test-integrity review is accepted. P16-ADV-01 is repaired and awaits independent adversarial re-review; combined-tree integration gates remain downstream. No review approval is claimed by this implementation entry.
