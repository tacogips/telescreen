# V13-P03 UI Foundation: Types, Client, Mock Seed, Shell, Components, Routes, Screenshot List

**Status**: Ready
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
