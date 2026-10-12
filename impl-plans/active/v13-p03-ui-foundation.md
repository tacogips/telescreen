# V13-P03 UI Foundation: Types, Client, Mock Seed, Shell, Components, Routes, Screenshot List

**Status**: In Progress
**Design Reference**: design-docs/specs/design-ui-sentry.md section 11.1, 11.3; design-docs/specs/design-v1-3-sentry-parity.md sections 4, 5.1, 13, 16; design-docs/specs/design-api-and-ui.md "v1.3 endpoints"
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p03-ui-foundation`
- planPath: `impl-plans/active/v13-p03-ui-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v13-p03-ui-foundation.md`
  - `src/web/src/api`
  - `src/web/src/components`
  - `src/web/src/routes.tsx`
  - `src/web/src/App.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/scripts/screenshots.ts`
- sharedPaths: none
- artifactRoots: none
- run-1 manifest e2 (`telescreen-v13-20261012-e2`): mode `implement` (finish). The
  `src/web/src/components` writePath includes `components/shell/PrimaryRail.tsx` and
  `components/ui/**`; the owner already added the Dashboard and Alerts rail icons. The
  three vitest failures at `d8073b1` are in `src/web/src/shell.test.tsx` (rail order;
  nav "For review" renamed "Assigned to me"; localStorage saved views removed from the
  nav) and `src/web/src/api/mock/mock.test.ts` (risk-order expectation after the 40-issue
  seed). Fix the tests to the accepted design, not the design to the tests. Never edit
  `tests/leak/test_ui_fixtures.py`: if it fails, change the fixture. `screenshots.ts`
  is handed to v13-p13 after this wave.

No page files are created here. `routes.tsx:AreaPage` already renders `Unavailable` for an
area without `pages/<area>/index.tsx`. The page directories therefore belong entirely to
v13-p13 to v13-p16, and no file is handed over.

Read `impl-plans/conventions.md` first.

## Intent and Context

Give every wave-2 UI plan a stable base:

- typed client functions for every v1.3 endpoint;
- a mock API that serves them from a realistic seed;
- shared components;
- the new areas and routes (pages come later from p15 and p16);
- the full screenshot route and state list.

Pages own their behavior; this plan only provides the scaffolding.

Imitate:

- `src/web/src/api/client.ts` (the `apiFetch`, `query` and `issuesApi` style);
- `src/web/src/api/mock/router.ts` and `state.ts` (path matching and mutable mock state);
- `src/web/src/api/mock/fixtures/v12.ts` (fixture style, Masker formats);
- `src/web/src/components/Sparkline.tsx` and `components/ui/MiniBarChart.tsx` (hand-made
  SVG);
- `src/web/src/routes.tsx:AREAS` (areas are discovered through `./pages/*/index.tsx`).

## Non-goals

- No page behavior in `pages/issues/**`; that belongs to p13 and p14, which must not be
  edited here.
- No new npm dependency. `package.json` and `bun.lock` stay unchanged.
- No files under `src/web/src/pages/`.

## Invariants

- Existing pages and their tests typecheck and pass unchanged:
  - new fields on EXISTING response types (`Issue`, `IssueDetail`, `IssueEvent`,
    `WorkflowSummary`, `Member`, and so on) are OPTIONAL properties;
  - new response types have required fields;
  - existing client functions keep their signatures. New behavior gets new functions or
    new optional parameters.
- Fixture values follow the rules of design-ui-sentry.md 8.3 and 9.4: Masker formats,
  `example.*` names, no secret-shaped value. `tests/leak/test_ui_fixtures.py` (read-only
  for this plan) must keep passing.
- The mock never performs a network call.

## Deliverables

1. **`api/types.ts`**: types for every v1.3 response of design-api-and-ui.md "v1.3
   endpoints":
   - `IssueStatus` adds `archived` and `escalating`;
   - new `IssuePriority`, `ArchiveState`, `AssigneeDisplay {kind, ref, display_name}`,
     `TabCount`, `CountsResponse`, `SavedView`, `EventSelection {event, previous_id,
     next_id, position, total}`, `TagDistribution`, `ActivityItem`, `ExternalIssueLink`,
     `AlertRule`, `AlertFire`, `NotificationSetting`, `SelfToken`, `DashboardResponse`,
     `IntegrationStatus`, `OwnershipRule`, `BulkResult`, `WorkflowListItem` extras.
2. **`api/client.ts`**: add the client functions below. Each is one `apiFetch` call with
   typed parameters:
   - `issuesApi`: `listQuery({query, period, start, end, trend, sort, cursor, limit})`,
     `counts`, `archive`, `unarchive`, `review`, `setPriority`, `bulk`, `merge`,
     `unmerge`, `eventAt(id, selector)`, `tags`, `issueStats`, `activity`, `editComment`,
     `deleteComment`, `setSubscription`, `bookmark`, `unbookmark`, `externalIssues`,
     `linkExternal`, `unlinkExternal`, `githubRepos`, `createGithubIssue`;
   - `viewsApi`;
   - `alertsApi`: list, get, create, update, delete, validate, enable, disable, mute,
     unmute, history;
   - `meApi`: notification settings, tokens;
   - `dashboardApi`;
   - `integrationsApi`: list, test;
   - `membersApi`: update, deactivate, reactivate;
   - `ownershipApi`: list, create, update, delete, reorder, test.
3. **`api/issueQuery.ts`** (new; the single source of the query key spec): `ISSUE_QUERY_KEYS:
   readonly QueryKeySpec[]` covering every key of design 5.1. It also has
   `validateIssueTokens(tokens)` (unknown key or invalid enum -> `valid: false` with a
   reason) and `evaluateIssueQuery(issue, tokens, now)`, the mock-only evaluator that
   supports negation, `has:`, dates, comparisons and `TELE-n`. p13's `query.ts` imports
   the spec from here.
4. **Mock** (`api/mock/router.ts`, `state.ts`, new `fixtures/v13.ts`):
   - serve every v1.3 endpoint. The stream applies `evaluateIssueQuery`, the period, the
     sorts, the tab counts and the trend arrays; mutations update `state.ts`;
   - seed: at least 40 issues, with at least 2 in each of the 10 statuses; all 7 sources,
     3 severities, 3 priorities and 7 exposures; at least 5 assignees (members and
     teams), with display names `Alice Example`, `Bob Example` and so on;
   - one merged group (a primary with 2 children), comments with `@alice` mentions,
     external links (`https://github.example.test/org/repo/issues/12`), 3 alert rules
     with history (delivered, suppressed_interval, muted), notification settings, self
     tokens (masked previews only), 4 ownership rules, and integration statuses (slack
     configured, github_issues unconfigured);
   - findings with timestamps spread over 14 days, so that the trend and the chart are
     non-empty.
5. **Components** (`components/` and `components/ui/`):
   - `Avatar` (initials, deterministic hue from the ref, title = display name);
   - `PeriodPicker` (all, 24h, 7d, 14d, 30d, custom start and end);
   - `StreamTabs` (tabs plus count badges);
   - `ShortcutHelp` overlay;
   - `shell/shortcuts.ts`: a registry `registerShortcuts(scope, bindings) -> dispose` that
     ignores events from `input`, `textarea`, `select` and `[contenteditable]`, plus a `?`
     global binding;
   - `StatusBadge`: labels for `archived` and `escalating`.
6. **Shell**:
   - `shell/nav.ts`, issues entries: Feed (`/issues`), Assigned to me
     (`/issues?query=assigned%3Ame`), Bookmarks (`/issues?query=bookmarks%3Ame`), Allowed,
     Verifying, then sources and rules. Drop the localStorage saved-view entries (views
     move to the server and the stream page);
   - `shell/AppShell.tsx` palette: filter pages and actions locally and search issues with
     `issuesApi.listQuery({query: text, limit: 8})`, debounced by 200 ms. Arrow keys and
     Enter navigate.
7. **Routes**: `routes.tsx`: `AreaKey` adds `dashboard` and `alerts`; `AREAS` adds
   Dashboard (`/dashboard`) and Alerts (`/alerts`). The existing `:id` route serves
   `/alerts/new`. Until p15 and p16 add the pages, these areas render `Unavailable`. The
   Settings sub-routes (profile, integrations, tokens, ownership) belong to v13-p16 inside
   `pages/settings/index.tsx`.
8. **`scripts/screenshots.ts`**: add each route and state of design 16, in light and dark.
   Each scripted state is a `{name, path, actions}` entry, where actions are clicks or key
   presses by role and label:
   - each stream tab, period 7d, bulk selection, the archive menu;
   - the issue detail of a merged primary and of a child, header menus;
   - alerts list, new and detail with history;
   - dashboard, profile, integrations, tokens, ownership, users;
   - workflows, palette open, the `?` overlay, onboarding.

   The run fails on any page or console error, as today. An action whose target element is
  missing logs `state-skipped: <name>` and continues, unless the environment sets
  `TELESCREEN_SCREENSHOTS_STRICT=1`; then it fails. v13-p17 runs in strict mode.

## Tests

- `api/mock/mock.test.ts` and `fixtures.test.ts`:
  - the seed has at least 40 issues and covers every status, source, severity, priority
    and exposure;
  - `GET /api/issues?query=is:unresolved` returns the regressed, escalating, acknowledged
    and in_progress issues;
  - `!source:slack` excludes Slack;
  - `assigned:none` returns only unassigned issues;
  - `TELE-3` returns exactly issue 3;
  - counts tabs sum correctly;
  - the event selector `oldest` has `previous_id = null`.
- `issueQuery.test.ts`: every key of design 5.1 validates. `foo:bar` -> invalid
  `unknown_key`. `times_seen:>5` and `first_seen:-24h` are valid.
- Component tests: `Avatar` initials (`Alice Example` -> `AE`); `PeriodPicker` emits
  values; `StreamTabs` renders counts; the shortcut registry ignores keys typed in an
  input.
- `shell.test.tsx`: the nav shows "Assigned to me" and not "For review"; the palette issue
  search calls `/api/issues?query=` and lists results.

## Verification

```bash
set -o pipefail
cd src/web
bun install --frozen-lockfile 2>&1 | tee /tmp/telescreen-v13-p03-install.log; echo "exit=$?"
bun run typecheck 2>&1 | tee /tmp/telescreen-v13-p03-typecheck.log; echo "exit=$?"
bun run test 2>&1 | tee /tmp/telescreen-v13-p03-test.log; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p03-web --emptyOutDir 2>&1 | tee /tmp/telescreen-v13-p03-build.log; echo "exit=$?"
bun run screenshots 2>&1 | tee /tmp/telescreen-v13-p03-shots.log; echo "exit=$?"
cd ../.. && uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py; echo "exit=$?"
```

Expected:

- each command exits 0, and the whole vitest suite passes, including existing page tests;
- screenshots, run in non-strict mode, finish with zero page or console errors. Areas
  without pages render `Unavailable` here.

## Completion Criteria

- [ ] Every v1.3 endpoint has a typed client function and a mock handler.
- [ ] The seed meets the counts above (asserted in tests).
- [ ] The new areas are registered; the nav is renamed; the palette searches issues.
- [ ] The screenshot list contains every state of design 16.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)


### Session: 2026-10-12 17:30
**Tasks Completed**: Added v1.3 TypeScript DTOs and endpoint clients, query key specification/evaluator, a 40-issue masked seed with auxiliary records, partial v1.3 mock routes, shared shell components, nav labels, palette issue search, dashboard/alerts routes, and new status labels. Updated the fixture masking/coverage assertions.
**Tasks In Progress**: Complete v1.3 mock endpoint coverage, shell/component regression tests, strict-aware screenshot state list, and required final verification.
**Blockers**: The assigned writePaths exclude `src/web/src/pages/issues/IssueList.tsx` and `src/web/src/pages/issues/detail/IssueDetailHeader.tsx`, but extending `IssueStatus` makes their transition maps fail TypeScript (`TS2739`, missing `archived` and `escalating`). Resume after serial reconciliation updates those transition maps or adjusts the shared status contract while preserving the two statuses. The assigned writePaths also exclude `src/web/src/components/shell/PrimaryRail.tsx`; adding `dashboard` and `alerts` to `AREAS` makes its icon lookup undefined at runtime. Resume after write ownership is amended to include the rail or a serial owner adds both icons.
**Reconciliation requests**: (1) `src/web/src/pages/issues/IssueList.tsx` and `src/web/src/pages/issues/detail/IssueDetailHeader.tsx`: extend status transition handling for `archived` and `escalating`, required to typecheck the accepted new `IssueStatus` values; these paths are outside p03 writePaths. (2) `src/web/src/components/shell/PrimaryRail.tsx`: add icons for the registered `dashboard` and `alerts` areas or otherwise safely register them; the file is outside p03 writePaths and route/screenshot smoke currently depends on it.
**Hashes**: src/web/src/api/types.ts before=74cb3ece175703dbaf0ea8e2c454526fddc175f24b2333f45cc7916ea087a4f2 after=9c3e7b740a34f55b1324007a0c2326350e0085e7706ffe5bc678f1a5ff9db0e2; src/web/src/api/issueQuery.ts before=absent after=5e0fa9686be0d4dc2eba0637a6ba6bfd6429b51f082f0b40a6cf11e32f5cf480; src/web/src/api/client.ts before=d1e441885549b2eb8c17e89a7eac7a52c060c064415f76e3c09d6e2e3caebce9 after=38cf17de20ebfb983c928a052e94a07568af189e7e2c139757897ffa27329638; src/web/src/api/mock/fixtures/v13.ts before=absent after=7eb95a73e0a34d22c79e1f04f17de18f7899d4f94f1a74cf58991c082cbcc5a8; src/web/src/api/mock/fixtures.test.ts before=2ed9d6ec4eb00e749a81bc06101549c388a085408f1c411f72b821c647a8977b after=a590d17029583422df98a070cfa6b549985bf30e1b3d356f39f7b6f27144d725; src/web/src/api/mock/state.ts before=c9b51a59b20db839d0e051e31d9179a69b6505b0129e600f4b090f3ad488519f after=5ace6ecff7c9ed568b799a60b9c8a08f6e4775ee59be538cf28758c9c0105821; src/web/src/api/mock/router.ts before=ae4ac521aab9df9ab0c524aeca100ed2370a5cdd049e7d976097cc70a0170e2d after=c2e2b226474b27b1cc1ea2e24f170bea9d1b26b93727dcd94852bc825986de57; src/web/src/components/Avatar.tsx before=absent after=69d573c350754a128ce65595547bf22a3b7b82d7c46765b4c4fbea69a38c8c1a; src/web/src/components/PeriodPicker.tsx before=absent after=b33f5629cbbbfe092890ed8bfb1d67196806ae5851a14d9e8d2d799ab5b4f9d5; src/web/src/components/StreamTabs.tsx before=absent after=7c3b21fdd955c0c7e7c28a8ff9fc1bee6dbd4c1f6bdc0683b31029579d4812bf; src/web/src/components/ShortcutHelp.tsx before=absent after=30e328c2dbca5ca60b556bf1009f4e91af49639ab7a76b4154c6d8bbc3619d4c; src/web/src/components/shell/shortcuts.ts before=edfd06ebda3664aae999319c1022c5750aecb6a4957afc0f7f060d851eb76b68 after=3a027fa1b4199e0cef0bf24406799068f680ee53e6052346cdd926542a681eaf; src/web/src/components/shell/nav.ts before=81d5ef0083ea30d80206c4a8739735f158bdb395b7184b6607ba503f21146201 after=e31edc063f33eee20b0c5fc8929a914da0e0c982b49c627a0a6640a015ec6e76; src/web/src/components/shell/AppShell.tsx before=61ca7b793f369f98edb476c21b29cda5aec5c603ccadd68f9a3171bf8d10d9e1 after=99be4598e738b4c0847513e5bb3f9184b843e752875042e8dc378b7ba1280c04; src/web/src/components/StatusBadge.tsx before=2dc0a131c11d527816ef15c6826f838e963ddffe1ae5316da761e443f42bfde7 after=76636bb28b1c8775116ce4875794d7e657daa9f4d8587722db3fa9c34ebc6ea4; src/web/src/routes.tsx before=d699008bad2feb7017ee24c6e70d10aa54d3e734d47c318b6dac005cf2d3d21b after=32a199532e263affa0769cc40d36043b2911b00392e84bad3cfb20a61b13735b.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v13-p03-install.log`). `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 2 (log: `/tmp/telescreen-v13-p03-typecheck.log`; only the two out-of-scope transition-map errors above remain after in-scope fixes).
**Notes**: Shared tree also contains concurrent backend edits outside p03 paths; preserved untouched. `package.json` and `bun.lock` were not edited. No screenshots or behavioral test run is claimed.

### Owner note: 2026-10-12 (before run 2)
Owner resolution (2026-10-12, run-1 blockers): the owner added archived and escalating to the transition maps in src/web/src/pages/issues/IssueList.tsx and pages/issues/detail/IssueDetailHeader.tsx (mirroring issues/transitions.py) and Dashboard (LayoutDashboard) and Alerts (Bell) icons to src/web/src/components/shell/PrimaryRail.tsx. bun run typecheck now passes. This plan is NOT complete: finish the remaining deliverables (mock handlers for every v1.3 endpoint, 40-issue seed, screenshot state list) and fix the three vitest failures inside its writePaths (src/web/src/shell.test.tsx rail order and saved views, src/web/src/api/mock/mock.test.ts risk-order expectation), then run every Verification command. List only final passing commands in `verification`, leave `priorVerification` empty and follow conventions 7a.
