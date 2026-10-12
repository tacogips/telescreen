# V13-P15 UI Alerts and Profile: Alert Rules List, Editor, History; Notification Settings, My Tokens, Default View, Theme

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Alerts, Profile); design-docs/specs/design-v1-3-sentry-parity.md sections 9, 10.1, 12 (self tokens), D22, D27
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p15-ui-alerts-profile`
- planPath: `impl-plans/active/v13-p15-ui-alerts-profile.md`
- wave: 2
- dependsOn: `v13-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v13-p15-ui-alerts-profile.md`
  - `src/web/src/pages/alerts`
  - `src/web/src/pages/profile`
- sharedPaths: none
- artifactRoots: none

This plan creates `pages/alerts/index.tsx` (default export; `routes.tsx` discovers it
through `./pages/*/index.tsx`) and `pages/profile/ProfilePage.tsx` (default export
`ProfilePage`). v13-p16's `pages/settings/index.tsx` loads that file lazily by path for
`/settings/profile`, so keep the file name and the default export exactly.

Read `impl-plans/conventions.md` first.

## Intent and Context

Two pages:

- the Alerts area (`/alerts`, `/alerts/new`, `/alerts/:id` with editor and history);
- the Profile page rendered at `/settings/profile` (notification settings matrix, my
  tokens, default view, theme).

Use the p03 client (`alertsApi`, `meApi`, `viewsApi`), the components (`Table`, `Tag`,
`Button`, `Select`, `Avatar`), and `useAuth()` permissions.

Imitate `pages/settings/TemplatesList.tsx` and `TemplateDetail.tsx` (list, detail and
validate flows, permission-aware actions) and `pages/settings/UsersRoles.tsx`.

## Non-goals

- No edits outside `pages/alerts` and `pages/profile`. The settings sub-route is done by
  p16 and the nav entries by p03.
- No new npm packages. No charts beyond what exists.

## Invariants

- Permission-aware rendering:
  - create, edit, enable, disable and delete need `alerts.write`;
  - mute and unmute need `alerts.mute`;
  - the list and history need `alerts.read`.

  Hide the actions the user lacks.
- The editor calls `alertsApi.validate` before save and shows `{field, code}` errors
  inline, next to the field path. Never echo values in messages.
- A token secret is shown once in a dialog with Copy and a warning, and is never stored in
  any state after the dialog closes.
- Profile channel options: `email`, `slack` and `off`. For alerts and workflow tasks there
  is also "As configured", which sends `stored: null`. Slack is disabled when
  `available.slack` is false.

## Deliverables

1. **`pages/alerts/index.tsx`**: routes on `:id`.
   - **List**: name, trigger summary, filters summary, actions summary, interval, a status
     tag (enabled, disabled, or muted until a time), last fired, fires in 24 h, row actions
     (enable or disable, mute 1h/24h/7d, unmute), and "New alert rule".
   - **`AlertEditor.tsx`**, a form builder:
     - trigger kind; threshold and window shown only for frequency and values;
     - filter chips (min severity, priorities, sources, rule classes, exposures, owner
       teams, detectors);
     - target rows (recipient chips, channel checkboxes, optional template select from the
       templates list filtered to event kind `alert_fired`);
     - start workflow (select from workflows);
     - action interval (minutes);
     - enabled.
   - **`AlertHistory.tsx`**: a paged table (time, issue short-id link, trigger, outcome tag,
     notifications, run link).
2. **`pages/profile/ProfilePage.tsx`**:
   - a notification settings matrix (rows are categories; the select shows the effective
     value);
   - my tokens (list, create with label and scopes limited by role, revoke with confirm);
   - a default view select (from `viewsApi`);
   - a theme toggle (the existing `components/shell/theme.ts` API).

## Tests (vitest)

- The alerts list renders the 3 mock rules with the correct status tags. A viewer sees no
  "New alert rule" button. A responder sees Mute but not Edit.
- Editor: switching the trigger to frequency shows threshold and window. Save with a
  missing threshold shows the field error from validate and does not call create.
- History renders the outcome tags `delivered`, `suppressed_interval` and `muted`.
- Profile:
  - changing mentions to slack PUTs the list;
  - "As configured" sends `stored: null` for alerts;
  - Slack is disabled when unavailable.
- Token create shows the secret once; after the dialog closes, the secret text is not in
  the DOM. Viewer scopes offer only `read`.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p15-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p15-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p15-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p15-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p15-build.log; echo "exit=$?"
```

Expected: each command exits 0.

## Completion Criteria

- [ ] The alerts list, editor and history, and the profile page, are implemented per
  section 11.2.
- [ ] The permission-aware actions are tested.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
