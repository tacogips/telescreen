# V11-P16 UI Ops Pages: My Tasks, Policies, Collector Coverage and Health, Police Health, Escalation, Settings

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md section 9.3 (rows: My tasks, Policies, Collectors, Police, Escalation, Settings) and 9.4 (screenshots)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p16-ui-ops`
- planPath: `impl-plans/active/v11-p16-ui-ops.md`
- wave: 2
- dependsOn: `v11-p04-ui-foundation`
- writePaths:
  - `impl-plans/active/v11-p16-ui-ops.md`
  - `src/web/src/pages/tasks` (new)
  - `src/web/src/pages/policies` (new)
  - `src/web/src/pages/collectors`
  - `src/web/src/pages/police`
  - `src/web/src/pages/escalation`
  - `src/web/src/pages/settings`
- sharedPaths: none
- artifactRoots: none (builds go to `/tmp/telescreen-v11-p16-ui-ops-web`; screenshots go
  to the gitignored `tmp/ui-screenshots/`)

Read `impl-plans/conventions.md` first.

## Intent and Context

New pages My tasks (`pages/tasks/index.tsx`, route `/my-tasks`) and Policies
(`pages/policies/index.tsx`, `/policies` and `/policies/:name`). Extend the Collectors,
Police, Escalation and Settings pages. `routes.tsx` (v11-p04) already registers the
`tasks` and `policies` areas and discovers pages by the `./pages/*/index.tsx` glob. All
data comes from the v11-p04 client and mock API.

Imitate:

- `pages/workflows/WorkflowEditor.tsx` and `index.tsx` for the policy editor (YAML/JSON
  textarea, validate, versions, activate);
- `pages/runs/RunDetail.tsx` for the human task completion form;
- the existing Collectors and Police pages for tables, drawers and status dots.

## Non-goals

- No edits outside the six page directories. Missing client pieces -> a reconciliation
  request for v11-p04.
- No graph preview for policies, no dashboard page.

## Invariants

- Pages render title and actions only through `TopBar.Slot` (design 8.2); never an
  `<h1>`.
- Identity values typed by an admin are sent once and never displayed again. Lists show
  the key prefix only.
- Coverage rows show the resource id prefix, never a locator. Linked issues come from
  the API.

## Deliverables

1. **My tasks** (`pages/tasks/`):
   - Owner reviews: open human tasks with kind `owner_review`. Outcome buttons Remove /
     Restrict / Exception / Not mine. Exception requires a comment and a duration select
     (7 days, 30 days, 90 days, until regressed); the other outcomes take an optional
     comment. It calls `humanTasksApi.complete`.
   - Other tasks: other open human tasks, each linking to its run.
   - Approvals: Approve and Reject with an optional note.
   - Assigned issues: `issuesApi.list({assignee: "me", sort: "risk"})`, with a Verifying
     group for `pending_verification`.
   - Empty state "Nothing needs your attention".
2. **Policies** (`pages/policies/`):
   - List: name, active version, effect, enabled, owner team.
   - Detail: editor, Validate (shows path and code errors), Save as a new version,
     version list, Activate, Disable.
3. **Collectors**:
   - Health column (`StatusIndicator` + tooltip with conditions and last success) and an
     "Event only" warning tag.
   - Detail at `/collectors/:id` with tabs Overview and Coverage. The tab is in the query
     string `?tab=coverage`, so the screenshot route works.
   - Coverage: counts by state and reason; the Unscannable resources table (resource id,
     state, reason, `ExposureBadge`, last attempt, linked issues) with Pagination; public
     rows first, as returned by the API.
4. **Police**: the status dot from `worker.health.status` (fall back to the v1 client
   rule only when `health` is absent); the tooltip shows the conditions.
5. **Escalation**: tier blocks show `min_risk` when present.
6. **Settings**:
   - Members drawer: identities list (kind, origin, key prefix, created), an "Add GitHub
     login or email" form (kind select + value), and remove for manual rows.
   - Teams: a lead select (member handles) saved through the team update.
7. **Screenshot smoke**: after implementing, run `bun run screenshots` (the script is from
   v11-p04) and fix every page or console error on the new and changed pages. Record the
   count of PNGs (expected 35) and `exit=0`. If no browser is available, record it in the
   Progress Log; v11-p21 re-runs the smoke as a required final gate, so the run cannot
   finish without a passing smoke.

## Pitfalls

- `/my-tasks` uses the area key `tasks`; the page directory is `pages/tasks`. Do not name
  it `my-tasks`.
- Do not call endpoints the mock does not serve. Unknown mock paths return 404 and would
  make the screenshot smoke fail.
- Duration values sent to the API are exactly `7d`, `30d`, `90d`, `until_regressed`.

## Tests (vitest)

- My tasks:
  - Exception completion without a comment is blocked;
  - Remove calls complete with outcome `remove`;
  - approvals approve calls `exceptionsApi.approve`;
  - assigned issues are sorted by risk;
  - the empty state.
- Policies:
  - validate errors render paths;
  - activate calls the endpoint with the version;
  - a new version appears after save (mock).
- Collectors:
  - a health dot per status;
  - `?tab=coverage` renders counts and the unscannable table without any locator text;
  - event-only tag.
- Police: a `police_silent` tooltip.
- Escalation: `min_risk` shown.
- Settings: adding an identity clears the input and lists a key prefix; team lead save.

## Verification

Preamble from conventions section 7, then in `src/web`:

- `bun install --frozen-lockfile > /tmp/telescreen-v11-p16-ui-ops-install.log 2>&1; echo "exit=$?"`
- `bun run typecheck > /tmp/telescreen-v11-p16-ui-ops-typecheck.log 2>&1; echo "exit=$?"`
- `bun run test > /tmp/telescreen-v11-p16-ui-ops-test.log 2>&1; echo "exit=$?"`
- `bun run build --outDir /tmp/telescreen-v11-p16-ui-ops-web --emptyOutDir > /tmp/telescreen-v11-p16-ui-ops-build.log 2>&1; echo "exit=$?"`
- `bun run screenshots > /tmp/telescreen-v11-p16-ui-ops-screenshots.log 2>&1; echo "exit=$?"`

Expect `exit=0` and zero page or console errors. v11-p15 runs in the same wave and may
still be editing `src/web/src/pages/issues`. The only tolerated failures are typecheck
errors located in that directory, test files inside it, and screenshot failures caused
only by an issues route. If one occurs: record the file paths, test ids or routes in the
Progress Log as informational (and hand screenshot failures to v11-p15 as a
reconciliation note), then prove nothing else fails (run in `src/web`):

- typecheck: `! grep -E 'error TS' /tmp/telescreen-v11-p16-ui-ops-typecheck.log | grep -v -E '^src/pages/issues/'; echo "exit=$?"` -> `exit=0`;
- test: `bun run test --exclude 'src/pages/issues/**' > /tmp/telescreen-v11-p16-ui-ops-test-own.log 2>&1; echo "exit=$?"` -> `exit=0`.

Any error or failing test in this plan's six page directories, or anywhere outside
`src/web/src/pages/issues`, blocks completion. Runs with tolerated failures go only in
the Progress Log; only passing commands go in the step `verification` list (conventions
7a). The build must still exit 0.

## Completion Criteria

- [x] Both new pages and the four page extensions are implemented; Settings team-lead persistence uses the dedicated v11-p04 client endpoint.
- [x] typecheck, test and build pass (or typecheck/test fail only in
      `src/web/src/pages/issues`, with the grep and the `--exclude` rerun showing
      `exit=0`); the screenshot smoke passes, or fails only on issues routes as recorded.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:18 JST
**Tasks Completed**: Added My tasks and Policies pages; extended Collectors with health and event-only indicators plus Overview/Coverage detail tabs and cursor pagination; used Police API health; displayed and validated escalation `min_risk`; added member identities and team lead controls. Added page tests for the new behaviors.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**:
- `src/web/src/pages/collectors/index.tsx` before=0b47b9ab2fd0656cfb2d4f49099ca11499a76aabe48ca39ee2ff96ca5aae4270 after=61f8a0c7e1b339313240137498f2d6df23c19da84a975653a0a8ebcc5d8bdf09
- `src/web/src/pages/collectors/index.test.tsx` before=28667d7dfe56ed113e9063a0de2247eaf5b2203e198abd1fb0416ba8b809bd80 after=11f6104b4ad7f205db24b39cabea1164ab7588516553c4a8a7dcc2bb60db0515
- `src/web/src/pages/police/index.tsx` before=e8c105fbfc69861f926a006949e4bb646cb17d9649ad786644dd7bbde543eb5c after=323f43b899795d113b003baa1c0b73e3710b8e9eab2600e87c6c7aa4dc62c6cf
- `src/web/src/pages/police/index.test.tsx` before=82fed47c69182f45d18d2cba21e0bcea7efd1aa74ee3a9ebca1f5eee7349ed25 after=8642c4e52262308c7f235a88c9bff5ca2fa3206241d32e1678e080e3272be93f
- `src/web/src/pages/escalation/index.tsx` before=dee4987bdde4b9bccde151b99cbd3f874719864269d47930812ebb2e12acf790 after=3ce138a7e1c30b2f2b814c9235c2663bdbefa62b3a20134ca07fceca31448260
- `src/web/src/pages/escalation/index.test.tsx` before=2d926072d0c4f1972bd4ffc1dba19255cea6992a0d8b3067cc9d25ef72fc10ff after=42b81c29335de290ddb38b4ad0c6bd6491e31c50acc87b4fb0bdbd2dcac80e5b
- `src/web/src/pages/settings/index.tsx` before=ad157ba038e1e9b08ad046dbac517b2186515ace1f29f0ef8c1a57c114d05045 after=bd6be4806d3ee808f7b1dcce9d59c5b80c32770f39d481275279dfd2f8ba9bc7
- `src/web/src/pages/settings/index.test.tsx` before=181055a49f3823bb7930c4fa778c496ffcdd4bd71f7330947f3364888ef9ff38 after=9a2565a7f7daa5c6161e4257cc5d4a4cfebe92280e97abdf253d00ef82b878a9
- `src/web/src/pages/tasks/index.tsx` before=absent after=f423b06229e562cef751f22fb14471673ce5c414ac01ddf944952ddd944675cc
- `src/web/src/pages/tasks/index.test.tsx` before=absent after=23c95fe838ca443658e0f0ab909166e30e6dfb2cde1c01697a641ae04fdcab53
- `src/web/src/pages/policies/index.tsx` before=absent after=5afcb062cff4ff39813327ab6793b575ae92c799fb0b1bc87b395fddcc628e8a
- `src/web/src/pages/policies/index.test.tsx` before=absent after=3f4bcf285b62cf33e9e628bbe027a7774bf1683ae6161141008cc32eabd5366d
**Verification**:
- `set -o pipefail; cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v11-p16-ui-ops-install.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0 (log: /tmp/telescreen-v11-p16-ui-ops-install.log)
- `set -o pipefail; cd src/web && bun run typecheck > /tmp/telescreen-v11-p16-ui-ops-typecheck.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0 (log: /tmp/telescreen-v11-p16-ui-ops-typecheck.log)
- `set -o pipefail; cd src/web && bun run test > /tmp/telescreen-v11-p16-ui-ops-test.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0; 28 files, 142 passed (log: /tmp/telescreen-v11-p16-ui-ops-test.log)
- `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v11-p16-ui-ops-web --emptyOutDir > /tmp/telescreen-v11-p16-ui-ops-build.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0 (log: /tmp/telescreen-v11-p16-ui-ops-build.log)
- `set -o pipefail; cd src/web && bun run screenshots > /tmp/telescreen-v11-p16-ui-ops-screenshots.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0; 35 PNGs in `tmp/ui-screenshots/`, zero browser page or console errors (log: /tmp/telescreen-v11-p16-ui-ops-screenshots.log)
**Notes**: The coverage list displays only an eight-character resource id prefix and links issues by issue id. Identity input clears after submission and identity lists contain keyed prefixes only. No source changed after the final gates. Dependency v11-p04-ui-foundation is accepted in `acceptedPlanIds`. Formal independent reviews and integration acceptance remain owned by downstream workflow steps.

### Session: 2026-10-10 17:22 JST
**Tasks Completed**: Added assertions for approval endpoint calls and risk-sorted assigned issues with a separate Verifying group. Wrapped My tasks tests in a route so the issue links render in the test harness.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/pages/tasks/index.test.tsx` before=23c95fe838ca443658e0f0ab909166e30e6dfb2cde1c01697a641ae04fdcab53 after=8515b61c47886f408bc743b35c4b849a699bb37cb8b76278217046102483c9e4.
**Verification**:
- `set -o pipefail; cd src/web && bun run typecheck > /tmp/telescreen-v11-p16-ui-ops-typecheck-final.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0 (log: /tmp/telescreen-v11-p16-ui-ops-typecheck-final.log)
- `set -o pipefail; cd src/web && bun run test > /tmp/telescreen-v11-p16-ui-ops-test-final.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0; 28 files, 144 passed (log: /tmp/telescreen-v11-p16-ui-ops-test-final.log)
- `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-v11-p16-ui-ops-web --emptyOutDir > /tmp/telescreen-v11-p16-ui-ops-build.log 2>&1; code=$?; echo "exit=$code"; exit $code` -> exit 0 (log: /tmp/telescreen-v11-p16-ui-ops-build.log)
**Notes**: The first expanded full test attempt exited 1 because its new link-rendering assertion rendered `<A>` outside router context; the failure summary was 1 failing test in the new task test file and 27 other passing files. That intermediate log was replaced when the final run used the same log path, so its full output is not retained separately. The harness now provides `MemoryRouter`; the final complete log shows 28 files and 144 passing tests. Only test code changed after the screenshot run, so the passing 35-PNG smoke remains application-source-matched.

### Session: 2026-10-10 adversarial repair handoff
**Tasks Completed**: Reproduced the review finding from the current Settings code and server contract. Recorded the required v11-p04 reconciliation request below.
**Tasks In Progress**: Correct lead-only save behavior and its regression test after the v11-p04 API client and mock endpoint are authorized and available.
**Blockers**: `ADV-V11-P16-001` is unresolved. The accepted v11-p04 client exposes no `setTeamLead`; changing `src/web/src/api/**` is outside this plan's writePaths and explicitly forbidden by the review repair instructions. The current members endpoint replaces membership using `handles` only, so a lead-only save must not call it.
**Reconciliation requests**: v11-p04 owner: add `directoryApi.setTeamLead(slug, lead: string | null)` using `PUT /api/teams/{slug}/lead` with `{lead}`, add the matching mock route and client wire-contract test, and stop sending `lead` in `setTeamMembers`. This matches `impl-plans/active/v11-p18-api-ops.md` and its directory API contract. Resume p16 after an owner-authorized v11-p04 redispatch (or accepted contract amendment) supplies this method and mock route; then make the members PUT conditional on an edited handles input, save lead changes through `setTeamLead`, correct the toast, and assert both endpoint behaviors. No source or test files were changed in this repair node.
**Verification**: No behavioral gates were run in this node because the required client method and mock endpoint are outside p16 writePaths and are not present. Earlier p16 gate results above predate this finding and do not resolve it.

**Owner resolution (2026-10-10)**: `directoryApi.setTeamLead(slug, lead: string | null)` now exists in `src/web/src/api/client.ts` (PUT `/api/teams/{slug}/lead` with `{lead}`, matching v11-p18), with a mock route in `src/web/src/api/mock/router.ts`; `Team.lead` is in the types. Use it for the team-lead control. Not a blocker.

### Session: 2026-10-10 17:56 JST
**Tasks Completed**: Resolved `ADV-V11-P16-001` in `src/web/src/pages/settings/index.tsx`: membership saves call `/members` only after the handles input changes, and lead changes call `directoryApi.setTeamLead` on `/lead`. Renamed the save action and added regression coverage for lead-only and membership-only persistence. Marked p16 completion criteria complete.
**Tasks In Progress**: None.
**Blockers**: None. The v11-p04 client and mock route were present in the accepted shared tree.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/pages/settings/index.tsx` before=bd6be4806d3ee808f7b1dcce9d59c5b80c32770f39d481275279dfd2f8ba9bc7 after=ea011880ee41575ce15a441797b2f460cd1082feb76fbdd78a7d48d49c6d2fe5; `src/web/src/pages/settings/index.test.tsx` before=9a2565a7f7daa5c6161e4257cc5d4a4cfebe92280e97abdf253d00ef82b878a9 after=b87caa85aea8df8681c7d9522002f4a5021e7ab58f484def1d5ab196ad281013.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v11-p16-ui-ops-install-rerun.log 2>&1; code=$?; printf 'exit=%s\n' "$code"; exit "$code"` -> exit 0; frozen install reported no dependency changes.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck > /tmp/telescreen-v11-p16-ui-ops-typecheck-final-rerun.log 2>&1; code=$?; printf 'exit=%s\n' "$code"; exit "$code"` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test > /tmp/telescreen-v11-p16-ui-ops-test-final-rerun.log 2>&1; code=$?; printf 'exit=%s\n' "$code"; exit "$code"` -> exit 0; 28 files and 146 tests passed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-v11-p16-ui-ops-web-rerun --emptyOutDir > /tmp/telescreen-v11-p16-ui-ops-build-rerun.log 2>&1; code=$?; printf 'exit=%s\n' "$code"; exit "$code"` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run screenshots > /tmp/telescreen-v11-p16-ui-ops-screenshots-rerun.log 2>&1; code=$?; printf 'exit=%s\n' "$code"; exit "$code"` -> exit 0; 35 PNGs refreshed under `tmp/ui-screenshots/`; the script reported no page or console errors.
- `git diff --check -- src/web/src/pages/settings/index.tsx src/web/src/pages/settings/index.test.tsx` -> exit 0.
**Notes**: The first test run after the repair exited 1 because the page test harness does not render the global success toast. Replaced that unsupported DOM assertion with a wait for the `/lead` request; the final full suite passed. The failed attempt log is `/tmp/telescreen-v11-p16-ui-ops-test-rerun.log`. No page, API, mock, or screenshot source outside p16 write paths was changed by this repair.
