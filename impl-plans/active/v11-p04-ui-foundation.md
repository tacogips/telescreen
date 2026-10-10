# V11-P04 UI Foundation: API Types, Client, Mock API, Components, Shell, Screenshots

**Status**: Completed
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

- [x] Types, client methods and mock endpoints exist for every v1.1 endpoint in
      design-api-and-ui.md.
- [x] New components, tokens, rail items, panel links and screenshot routes exist.
- [x] typecheck, test and build pass; the fixture leak test passes.
- [x] No file under `src/web/src/pages/**` changed by this plan.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 16:34
**Tasks Completed**: Added v1.1 API unions and DTOs, client methods, safe mock fixtures and endpoint behavior, exposure/risk/status components, critical styling, tasks/policies routes and navigation, attention badge, and screenshot targets. Added client, mock, fixture-safety, component and shell assertions.
**Tasks In Progress**: The frontend typecheck remains blocked by two existing exhaustive status maps in the downstream issues-page plan.
**Blockers**: None. Reconciliation request below is owned by v11-p15, not a missing dependency for this plan.
**Reconciliation requests**: v11-p15: update `src/web/src/pages/issues/detail/IssueDetailHeader.tsx` and `src/web/src/pages/issues/IssueList.tsx` transition maps for `allowed` and `pending_verification` (empty transition lists are appropriate until the page defines supported actions). This is required for the repository typecheck after IssueStatus widened. Both files are outside this plan's writePaths and were left untouched.
**Hashes**: `src/web/src/api/types.ts` before=eb211899449ddeee072a226478829067118b9c8474d391e3f565af91c1f99c2e after=6aff94867bf125a0044274dac8be4d3da6886a06f339a15cc577b4a4c1e69ab5; `src/web/src/api/client.ts` before=recorded in edit-02-client.before.sha after=b5067090de5976faac90bd27396af6a551609f46715d49d71923375a9881474d; `src/web/src/api/mock/router.ts` before=recorded in edit-05-router.before.sha after=e94564a231b8def2758e21be33c84127568d45be1132e9c3d639e3afaa88cdb4; `src/web/src/api/mock/fixtures/v11.ts` before=absent after=6da1875c7f4120057bd0039a7f3eb3ce9259ab317a066d5b0a4d250a5cd5e45a; remaining changed-file hashes are preserved in `tmp/telescreen-v11-20261010-c1/v11-p04-ui-foundation/final-hashes.txt`.
**Verification**: `bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v11-p04-ui-foundation-install.log`); `bun run typecheck` -> exit 2, missing the two status keys noted above (log: `/tmp/telescreen-v11-p04-ui-foundation-typecheck.log`); `bun run test` -> exit 0, 123 passed / 0 failed (log: `/tmp/telescreen-v11-p04-ui-foundation-test.log`); `bun run build --outDir /tmp/telescreen-v11-p04-ui-foundation-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v11-p04-ui-foundation-build.log`); `uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py` -> exit 0, 1 passed / 0 failed (log: `/tmp/telescreen-v11-p04-ui-foundation-fixture-leak.log`); `test -z "$(git diff --name-only HEAD -- src/web/package.json src/web/bun.lock)"` -> exit 0. Initial test attempt failed only the newly added risk tooltip assertion (122 passed / 1 failed); corrected the assertion to target the tooltip wrapper, then the final full test run passed.
**Notes**: No page files or dependency manifests changed. `bun run screenshots` is owned by v11-p16 after its pages exist and was not run here.

**Owner resolution (2026-10-10)**: The owner added placeholder entries `allowed: []` and `pending_verification: []` to the exhaustive transition maps in `src/web/src/pages/issues/detail/IssueDetailHeader.tsx` and `src/web/src/pages/issues/IssueList.tsx` (v11-p15 replaces them with real transitions). `bun run typecheck` exits 0. Report fixed findings only in addressedFeedback (conventions 7a).

### Session: 2026-10-10 16:49
**Tasks Completed**: Re-ran the assigned frontend and fixture-leak gates on the combined tree; all passed. The owner-provided issue-page transition placeholders allowed typecheck to pass. No page or dependency-manifest files were edited by this plan.
**Tasks In Progress**: None for v11-p04.
**Blockers**: None.
**Reconciliation requests**: The earlier v11-p15 transition-map request is resolved by the owner-provided placeholders; v11-p15 owns replacing them with real transitions.
**Hashes**: `impl-plans/active/v11-p04-ui-foundation.md` before=67782864c2b3520f94f9fc7c0f99d9a26234ec503cdfb481b4530b3a4ddf9b96; after recorded after this entry is written.
**Verification**: `cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v11-p04-ui-foundation-install.log`); `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v11-p04-ui-foundation-typecheck.log`); `cd src/web && bun run test` -> exit 0, 26 files and 123 tests passed / 0 failed (log: `/tmp/telescreen-v11-p04-ui-foundation-test.log`); `cd src/web && bun run build --outDir /tmp/telescreen-v11-p04-ui-foundation-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v11-p04-ui-foundation-build.log`); `uv run --frozen pytest -p no:cacheprovider tests/leak/test_ui_fixtures.py` -> exit 0, 1 passed / 0 failed (log: `/tmp/telescreen-v11-p04-ui-foundation-fixture-leak.log`); `test -z "$(git diff --name-only HEAD -- src/web/package.json src/web/bun.lock)"` -> exit 0.
**Notes**: Frozen install and all required verification gates passed against the combined tree. Screenshot smoke remains assigned to v11-p16 after its pages exist.
