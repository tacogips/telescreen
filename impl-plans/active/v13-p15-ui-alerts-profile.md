# V13-P15 UI Alerts and Profile: Alert Rules List, Editor, History; Notification Settings, My Tokens, Default View, Theme

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.2 (Alerts, Profile); design-docs/specs/design-v1-3-sentry-parity.md sections 9, 10.1, 12 (self tokens), D22, D27
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p15-ui-alerts-profile`
- planPath: `impl-plans/active/v13-p15-ui-alerts-profile.md`
- wave: 2 (run 2; Last Updated 2026-10-13 with the run-2 screenshot contract)
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

- Screenshot contract (design section 15, run 2): on `/alerts/alert-01` the detail view
  has a `tab` whose accessible name is exactly "History", and selecting it shows the
  history table. Add a test that asserts `getByRole("tab", { name: "History" })`.

## Run-2 shared-file rules

- Read-only in this run: `src/web/src/api/**` (client, types, mock router and fixtures),
  `src/web/src/components/**`, `src/web/src/routes.tsx`, `src/web/scripts/screenshots.ts`
  and every page directory other than `pages/alerts` and `pages/profile`.
- The p03 client and mock already serve `/api/alert-rules*`, `/api/auth/me/*` and
  `/api/views`. A missing client call or mock field is a Reconciliation request, not an
  edit.
- This plan does not run `bun run screenshots`: v13-p16 is the single wave-2 screenshot
  runner and owner. Its run covers `alerts-list`, `alerts-new`, `alerts-detail-history`
  and `profile`.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile > /tmp/telescreen-v13-p15-install.log 2>&1; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p15-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p15-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p15-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p15-build.log; echo "exit=$?"
test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"
```

Expected: each command exits 0. A vitest failure caused only by v13-p16's in-progress
files is recorded as such.

## Completion Criteria

- [x] The "History" tab test passes.

- [x] The alerts list, editor and history, and the profile page, are implemented per
  section 11.2.
- [x] The permission-aware actions are tested.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-13 01:45
**Tasks Completed**: Implemented the alerts list, structured rule editor with validation, multiple notification targets and filtered template/workflow choices, per-rule History tab and cursor pagination, permission-aware actions, and profile notification matrix, self tokens, default view, and theme. Added seven UI regression tests.
**Tasks In Progress**: None for this plan. Wave integration review, screenshots, and run-3 verification remain downstream.
**Blockers**: None.
**Reconciliation requests**: `src/web/src/api/client.ts` and `src/web/src/api/types.ts` still expose the legacy alert shape, mute `{until}`, unpaged history, and legacy notification settings type while p08/p11 implement structured trigger/filter/action fields, mute `{minutes}`, cursor history, and stored/effective/availability settings. P15 uses the authenticated `apiFetch` boundary locally for mute and cursor history and locally casts the serialized response/payload shapes; update the shared client and types during serial API reconciliation. `src/web/src/api/mock/router.ts` and `src/web/src/api/mock/fixtures/v13.ts` still need p08/p11 response-shape alignment for screenshots and mock mode; those paths are read-only for p15 and remain a reconciliation request.
**Hashes**:
- `impl-plans/active/v13-p15-ui-alerts-profile.md` before=890cca72c88ec2bb98ef97d1b6c75d1374f00001a2af4f7d8fa33df900015dff after=12bda233d55b266b83761aabf9db40fbb717b5af48f618fa740773f2998f5c8c (before this final verification update)
- `src/web/src/pages/alerts/alertTypes.ts` before=absent after=0fa4333066bf04704e061b34c48220fd0786540365edcbd06e91f26bb58c98a5
- `src/web/src/pages/alerts/AlertEditor.tsx` before=absent after=3ec1a74cb014a3daa2dc7b4b351cb07fd38f1d1a0facff5f8cc9c9dbf6f8ffa7
- `src/web/src/pages/alerts/AlertHistory.tsx` before=absent after=4e1a59ddbb5d9041a4565bd33c4834d1cb51ea32b7f200276b6cfd3799580c8b
- `src/web/src/pages/alerts/index.tsx` before=absent after=8a4d5b6c585e0f32be0b14c64fee45a97a74c8f8a97ea86cee16e6f9f6e0fc64
- `src/web/src/pages/alerts/index.test.tsx` before=absent after=bd249920d0607360faa54a1aabc24a4d29d107ef36cd80d0d1416573f3f1fa98
- `src/web/src/pages/profile/ProfilePage.tsx` before=absent after=48a0319c0a8e2085725be4be3d791a4cf166186aaa56ecdd2d8a9b35eac559c7
- `src/web/src/pages/profile/ProfilePage.test.tsx` before=absent after=25992a57d9cf50e6ca3cc3101a7ec1c7fecd1445931c737d3b92ea0a9aa5807f
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v13-p15-install.log`)
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v13-p15-typecheck.log`)
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/alerts/index.test.tsx src/pages/profile/ProfilePage.test.tsx --reporter=dot` -> exit 0, 7 passed (log: `/tmp/telescreen-v13-p15-focused.log`)
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-v13-p15-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v13-p15-build.log`)
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test` -> exit 0, 215 passed / 0 failed (log: `/tmp/telescreen-v13-p15-test.log`).
- `test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"` -> exit 0.
- `test "$(grep -c '^- \[ \]' impl-plans/active/v13-p15-ui-alerts-profile.md)" -eq 0` -> exit 0.
**Notes**: Initial p15 typecheck and focused-test attempts exposed and fixed issues in owned files. An earlier whole-suite run exited 1 on transient p16 dashboard and UsersRoles test failures; the final rerun passed all 215 tests. `bun install --frozen-lockfile` made no lockfile changes. No edits were made outside this plan's `writePaths`.

### Session: 2026-10-13 01:52
**Tasks Completed**: Addressed test-integrity finding `P15-TI-01` in `src/web/src/pages/profile/ProfilePage.test.tsx`: the Alert rules test now selects email before returning to "As configured" and asserts the latest PUT stores `null`; a separate test selects Slack for the available Subscribed issues category and asserts the latest PUT stores `slack`. The Mentions disabled-Slack assertion remains.
**Tasks In Progress**: None for this plan. Independent review and workflow finalization remain downstream.
**Blockers**: None.
**Reconciliation requests**: None for this repair.
**Hashes**: `src/web/src/pages/profile/ProfilePage.test.tsx` before=25992a57d9cf50e6ca3cc3101a7ec1c7fecd1445931c737d3b92ea0a9aa5807f after=685b9dbcaa52872a9dc5a3af96bb92e70a5fb641420b59322a600f0618b67c60; this plan before=6e0926eefef3918c4f90aea35f9930ae4f2051acb2bd4c5586c99fe538c5822e (final after hash recorded in the Step 6 result).
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/profile/ProfilePage.test.tsx --reporter=dot 2>&1 | tee /tmp/telescreen-v13-p15-profile.log` -> exit 0, 4 passed (log: `/tmp/telescreen-v13-p15-profile.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p15-typecheck.log` -> exit 0 (log: `/tmp/telescreen-v13-p15-typecheck.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-v13-p15-test.log` -> exit 0, 42 files / 218 passed / 0 failed (log: `/tmp/telescreen-v13-p15-test.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-v13-p15-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p15-build.log` -> exit 0 (log: `/tmp/telescreen-v13-p15-build.log`).
- `test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"` -> exit 0.
- `test "$(grep -c '^- \[ \]' impl-plans/active/v13-p15-ui-alerts-profile.md)" -eq 0` -> exit 0.
**Notes**: The current test suite now fails if the category mapping, the `stored: null` reset, or unavailable Slack option behavior regresses. No production change or dependency change was needed.

### Session: 2026-10-13 02:03
**Tasks Completed**: Addressed adversarial findings `P15-ADV-01` through `P15-ADV-03`. `AlertEditor.save` now compares create state with the server-created rule's `enabled` value (default true) and update state with `initial.enabled`; validation errors for p08 codes and fields render static inline text with a generic fallback; `AlertHistory` renders the full server page. Added regression coverage for both create enabled-state directions, validation/no-echo/fallback, and 12 returned history rows.
**Tasks In Progress**: None for this plan. Independent adversarial re-review and workflow finalization remain downstream.
**Blockers**: None.
**Reconciliation requests**: None for these repairs.
**Hashes**: `src/web/src/pages/alerts/AlertEditor.tsx` before=3ec1a74cb014a3daa2dc7b4b351cb07fd38f1d1a0facff5f8cc9c9dbf6f8ffa7 after=ab30520667e82868bdeb7adc2c3ef3684fcf7f02fd5d460172214b72d1732044; `src/web/src/pages/alerts/AlertHistory.tsx` before=4e1a59ddbb5d9041a4565bd33c4834d1cb51ea32b7f200276b6cfd3799580c8b after=f5145236ddeb2343993dcf52697ff6aa3fb93344216dc1b2a1ccde13dd0a3468; `src/web/src/pages/alerts/index.test.tsx` before=bd249920d0607360faa54a1aabc24a4d29d107ef36cd80d0d1416573f3f1fa98 after=e1a30931c8cc995e79f114bf44328234e75706d9b0fd8615ec8c8f7dcf038c0f; this plan before=9ac80682df274c9e568efe3c2aa909f454536e9029e8f1047fe26515a369e7a3 (final after hash recorded in the Step 6 result).
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p15-adv-install.log 2>&1` -> exit 0 (log: `/tmp/telescreen-v13-p15-adv-install.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/alerts/index.test.tsx --reporter=dot 2>&1 | tee /tmp/telescreen-v13-p15-adv-alerts-rerun2.log` -> exit 0, 8 passed (log: `/tmp/telescreen-v13-p15-adv-alerts-rerun2.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p15-adv-typecheck-rerun2.log` -> exit 0 (log: `/tmp/telescreen-v13-p15-adv-typecheck-rerun2.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-v13-p15-adv-test-rerun2.log` -> exit 0, 42 files / 222 passed / 0 failed (log: `/tmp/telescreen-v13-p15-adv-test-rerun2.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-v13-p15-adv-web-rerun2 --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p15-adv-build-rerun2.log` -> exit 0 (log: `/tmp/telescreen-v13-p15-adv-build-rerun2.log`).
- `test -z "$(git diff --name-only HEAD -- src/web/src/api src/web/src/components src/web/src/routes.tsx src/web/package.json src/web/bun.lock)"` -> exit 0.
- `test "$(grep -c '^- \[ \]' impl-plans/active/v13-p15-ui-alerts-profile.md)" -eq 0` -> exit 0.
**Notes**: The initial focused attempt exited 1 with 4 passed / 4 failed because the expanded history fixture made a single-result assertion ambiguous and new form tests submitted before permissions enabled the button (`/tmp/telescreen-v13-p15-adv-alerts.log`). The test assertions and waits were corrected; final focused and full-suite reruns passed. No dependency, API, backend, or out-of-scope files were edited.
