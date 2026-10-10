# V11-P04 UI Foundation: API Types, Client, Mock API, Components, Shell, Screenshots

**Status**: Ready
**Design Reference**: design-docs/specs/design-ui-sentry.md section 9 (9.1, 9.2, 9.4, 9.5) and section 8 (still binding where 9 is silent); design-docs/specs/design-api-and-ui.md ("v1.1 endpoints")
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p04-ui-foundation`
- planPath: `impl-plans/active/v11-p04-ui-foundation.md`
- wave: 1
- dependsOn: none (base: main at 289699e; UI work depends only on the accepted design)
- writePaths:
  - `impl-plans/active/v11-p04-ui-foundation.md`
  - `src/web/src/api`
  - `src/web/src/components`
  - `src/web/src/styles`
  - `src/web/src/routes.tsx`
  - `src/web/src/App.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/scripts`
- sharedPaths: none
- artifactRoots: none. `src/web/node_modules` and `src/web/dist` are gitignored, never
  written by this plan and not declared. Builds go to `/tmp/telescreen-v11-p04-ui-foundation-web`.

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

The two wave-2 page plans (v11-p15 issues pages, v11-p16 ops pages) need typed API
functions, mock data and shared components for the v1.1 features before they start. This
plan pins those. The mock API lets every page render without the backend, and the
screenshot smoke runs only against it. The backend endpoints themselves are written
later (waves 3 and 4). The types here must match design-api-and-ui.md "v1.1 endpoints"
exactly; where the design gives a list of fields, use those names verbatim.

## Non-goals

- No edits under `src/web/src/pages/**` (owned by v11-p15 and v11-p16). If a type change
  breaks a page's typecheck, keep the old field names working (add fields; never rename
  or remove existing ones).
- No dependency change (`package.json` and `bun.lock` untouched); use
  `bun install --frozen-lockfile`.
- No backend changes.

## Invariants

- Keep exported names and signatures of `ApiError`, `apiFetch`, `rawFetch`, `getToken`,
  `setToken` and all existing `*Api` objects. Only add members.
- The `IssueStatus` union widens to include `allowed` and `pending_verification`.
  Existing `switch` or `Record<IssueStatus, ...>` usages in pages must still compile.
  Check with `bun run typecheck` before finishing, and if a page breaks, file a
  reconciliation request for v11-p15/v11-p16 instead of editing the page.
- Fixture value rules (design-ui-sentry.md 8.3 and 9.4): no raw-looking values, no real
  file names, emails only in reserved example domains (`example.com`, `example.org`).
  Identity rows show key prefixes only.

## Deliverables

### 1. `src/web/src/api/types.ts`

Add these types (string unions for enums):

- `Severity = "critical" | "high" | "medium"`; widen `IssueStatus`.
- `Visibility`, `CoverageState`, `UnsupportedReason`, `HealthStatus`, `HealthCondition`,
  `ExceptionStatus`, `ExceptionDuration`, `PolicyEffect`.
- `Exposure` (`visibility`, `external_principal_count`, `shared_drive`, `channel_kind`,
  `repo_visibility`, `is_fork`).
- `Issue` gains optional fields (keep them optional so existing fixtures compile):
  `base_severity`, `exposure_visibility`, `risk_score`, `policy_name`, `policy_version`.
- `IssueDetail = Issue & {exposure, exposure_changed_at, asset_tier, assignee_ref, owner:
  {present, member_ref}, active_exception: ActiveException | null}`.
- `Occurrence`, `OccurrencesResponse = {items, key_versions: number[]}`.
- `RemediationReceipt = {issue_ids, rescans_enqueued}`.
- `IssueException` with every column of `issue_exceptions` except `reason`, which is
  exposed as `reason` (redacted text).
- `PolicySummary`, `PolicyVersion`, `PolicyValidation = {valid, errors}`.
- `CoverageSummary = {since, counts: {state, reason, count}[], unscannable_total}`.
- `CoverageResource` (fields per design endpoint table).
- `CollectorHealth` and `CollectorConfig.health?: CollectorHealth`.
- `PoliceWorker.health?: {status, conditions}`.
- `MemberIdentity = {id, kind, origin, key_prefix, created_at}`; `Team.lead?: string |
  null`.
- `HumanTask` completion body type with `exception_duration?`.

### 2. `src/web/src/api/client.ts`

Add these members; every call goes through `apiFetch`:

- `issuesApi`: `list` accepts `exposure`, `sort: "risk"` and `assignee: "me" |
  "my_teams"`; `remediation(id, {action, include_linked, note?})`; `resolve(id, {reason,
  note})`; `occurrences(id)`; `requestException(id, {reason, duration})`.
- `exceptionsApi`: `list({status?, issue_id?, approver?})`, `approve(id, note?)`,
  `reject(id, note?)`, `revoke(id)`.
- `policiesApi`: `list()`, `get(name)`, `version(name, v)`, `validate(body)`,
  `create(body)`, `activate(name, version)`, `disable(name)`.
- `collectorsApi`: `coverage(id)` and `coverageResources(id, {state?, limit?, cursor?})`.
- `directoryApi`: `identities(handle)`, `addIdentity(handle, {kind, value})`,
  `removeIdentity(handle, id)`; team create and update accept `lead`.
- `humanTasksApi.complete` accepts `exception_duration`.

### 3. Mock API (`src/web/src/api/mock/`)

- New `fixtures/v11.ts` with the fixture coverage list of design 9.4. Extend
  `fixtures/issues.ts` issues with the new fields:
  - at least one issue per status, including `allowed` and `pending_verification`;
  - one `critical` issue;
  - one issue per visibility;
  - one `manual_unverified` resolution;
  - a credential issue with two linked occurrences;
  - risk scores spread across 5-95.
- `state.ts`: holds the new collections (exceptions, policies with versions, identities,
  coverage, collector and police health).
- `router.ts`: handles every endpoint of Deliverable 2 with in-memory mutations.
  - remediation moves an issue to `pending_verification`;
  - resolve sets `resolved` and the resolution;
  - an exception request creates `requested`, or `active` plus issue `ignored` when the
    mock token is admin;
  - approve makes it active and sets the issue to `ignored`;
  - reject and revoke behave as in design section 11;
  - policies create a new inactive version; activate and disable toggle;
  - `assignee=me` returns issues assigned to the mock member `member:alice` (use the
    existing mock member handle from `fixtures/directory.ts`);
  - `sort=risk` sorts by descending risk;
  - `exposure=` filters.
- `mock.test.ts` and `fixtures.test.ts`: extend them. The existing raw-value scan must
  also cover `fixtures/v11.ts`; add an assertion that no fixture string matches a file
  extension pattern such as `\.(xlsx|docx|pdf|csv)\b`, apart from display locators that
  already existed in v1 fixtures.

### 4. Components (`src/web/src/components/`)

- `styles/tokens.css`: the `--critical*` role for light and dark (values in design 9.1).
- `ui/SeverityBar.tsx`: a critical color.
- `ui/PriorityBadge.tsx`: 4/3/2 bars for critical/high/medium.
- `ui/Tag.tsx`: a `critical` variant.
- New `ui/ExposureBadge.tsx` (renders nothing for internal, private and unknown in
  `compact` mode) and `ui/RiskPill.tsx`; export both from `ui/index.tsx`.
- `StatusBadge.tsx`: new statuses and labels ("Allowed", "Verifying"); a `resolution`
  prop renders "Resolved (unverified)" with the warning variant for `manual_unverified`.
- `labels.ts`: `visibilityLabel`, `coverageLabel`, `exceptionDurationLabel`,
  `healthConditionLabel`.
- `ui/ProgressMarker.tsx`: unchanged. The status-to-step mapping lives in pages.

### 5. Shell and routes

- `routes.tsx`: `AreaKey` gains `"tasks"` and `"policies"`; add `AREAS` entries
  `{key: "tasks", label: "My tasks", path: "/my-tasks", group: "tasks"}` (after issues)
  and `{key: "policies", label: "Policies", path: "/policies", group: "policies"}` (after
  collectors). The page glob already resolves `pages/tasks/index.tsx` once v11-p16 adds
  it; until then `Unavailable` renders.
- `components/shell/nav.ts` and `PrimaryRail`: the rail order of design 9.2, icons from
  `lucide-solid` (already a dependency). The Issues secondary panel adds "For review"
  (`/issues?query=assigned:me`), and the views "Allowed" (`is:allowed`) and "Verifying"
  (`is:pending_verification`).
- The rail My tasks count badge comes from `humanTasksApi.list({assignee: "me", status:
  "open"})` plus `exceptionsApi.list({approver: "me", status: "requested"})`. On any
  error, show no badge; never throw.
- `shell.test.tsx`: assert the new rail items, their order and the badge rendering with a
  mocked fetch.

### 6. `src/web/scripts/screenshots.ts`

Add `/my-tasks`, `/policies` and the first policy (discover `a[href^="/policies/"]`), and
the first collector detail with `?tab=coverage` (discover `a[href^="/collectors/"]`, then
append `?tab=coverage`). Screenshot file names must stay unique: strip `?` and `=`
characters when building the name.

## Pitfalls

- Do not make new `Issue` fields required. Existing fixtures and pages would stop
  compiling.
- Do not put titles or locators of real-looking files in v1.1 fixtures (digest-like
  data, coverage rows). Coverage rows have no locator field at all.
- `ExposureBadge` text for `external_shared` with count 0 is "Shared externally" (no
  "(0)").
- Mock admin detection: reuse whatever the mock uses for scope today. If nothing exists,
  treat the mock token as admin and document this in the fixture file comment.

## Tests (vitest)

- `ui/ui.test.tsx`:
  - ExposureBadge per visibility, both compact and full;
  - RiskPill renders a number and a tooltip;
  - PriorityBadge renders 4 bars for critical;
  - StatusBadge renders "Verifying" and "Resolved (unverified)".
- `api/client.test.ts`: each new method calls the expected method, path and body.
- `api/mock/mock.test.ts`:
  - remediation sets `pending_verification`;
  - exception approve sets `ignored`;
  - policy create increments the version;
  - `assignee=me` filters;
  - `sort=risk` orders descending;
  - unknown v1.1 paths return 404 `not_found`.
- `shell.test.tsx`: rail order, and the For review / Allowed / Verifying links.

## Verification

Start with the conventions section 7 preamble, then, from `src/web`:

- `bun install --frozen-lockfile > /tmp/telescreen-v11-p04-ui-foundation-install.log 2>&1; echo "exit=$?"`
- `bun run typecheck > /tmp/telescreen-v11-p04-ui-foundation-typecheck.log 2>&1; echo "exit=$?"`
- `bun run test > /tmp/telescreen-v11-p04-ui-foundation-test.log 2>&1; echo "exit=$?"`
- `bun run build --outDir /tmp/telescreen-v11-p04-ui-foundation-web --emptyOutDir > /tmp/telescreen-v11-p04-ui-foundation-build.log 2>&1; echo "exit=$?"`

All must show `exit=0`. Also run
`uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py > /tmp/telescreen-v11-p04-ui-foundation-fixture-leak.log 2>&1; echo "exit=$?"`,
which must show `exit=0`.

The screenshot smoke (`bun run screenshots`) is run by v11-p16 after the pages exist. It is
not required here.

## Completion Criteria

- [ ] Types, client methods and mock endpoints exist for every v1.1 endpoint in
      design-api-and-ui.md.
- [ ] New components, tokens, rail items, panel links and screenshot routes exist.
- [ ] typecheck, test and build pass; the fixture leak test passes.
- [ ] No file under `src/web/src/pages/**` changed by this plan.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
