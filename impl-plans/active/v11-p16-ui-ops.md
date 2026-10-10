# V11-P16 UI Ops Pages: My Tasks, Policies, Collector Coverage and Health, Police Health, Escalation, Settings

**Status**: Ready
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

- [ ] Both new pages and the four page extensions are implemented.
- [ ] typecheck, test and build pass (or typecheck/test fail only in
      `src/web/src/pages/issues`, with the grep and the `--exclude` rerun showing
      `exit=0`); the screenshot smoke passes, or fails only on issues routes as recorded.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
