# V13-P16 UI Dashboard and Organization: Dashboard, Onboarding, Integrations, Tokens, Ownership Rules, Member Edit, Workflows List

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Dashboard, Settings, Workflows list); design-docs/specs/design-v1-3-sentry-parity.md sections 11, 12, 13 (onboarding, workflows list), D26, D28
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p16-ui-dashboard-org`
- planPath: `impl-plans/active/v13-p16-ui-dashboard-org.md`
- wave: 2
- dependsOn: `v13-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v13-p16-ui-dashboard-org.md`
  - `src/web/src/pages/dashboard`
  - `src/web/src/pages/settings`
  - `src/web/src/pages/workflows`
- sharedPaths: none
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

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p16-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p16-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p16-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p16-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p16-build.log; echo "exit=$?"
```

Expected: each command exits 0, including the existing settings and workflow page tests.

## Completion Criteria

- [ ] The dashboard, onboarding, integrations, tokens, ownership rules, member edit and
  workflows list are implemented per section 11.2.
- [ ] No new dependency (`git diff src/web/package.json` is empty).
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
