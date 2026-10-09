# P13 UI Workflows and Runs Pages Implementation Plan

**Status**: Ready (run B revision; the run A implementation is the starting point)
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 6 (Workflows, Runs), 7, 8.1, 8.1a, 8.2; design-docs/specs/design-workflow-engine.md (DSL step fields); design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09 (run B, session 341)

## Plan Metadata

- planId: `p13-ui-workflows`
- planPath: `impl-plans/active/p13-ui-workflows.md`
- wave: B2 (parallel with p12, p14, p28)
- dependsOn: `p30-ui-design-system`, `p31-ui-mock-api`
- writePaths:
  - `impl-plans/active/p13-ui-workflows.md`
  - `src/web/src/pages/workflows`
  - `src/web/src/pages/runs`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

Run B rebuilds the Workflows and Runs pages on the design system (design-ui-sentry.md
section 6 constrained by 8.1). Wave B1 delivered the components (`components/ui`,
`TopBar.Slot` in `components/shell/TopBar.tsx`), the `yaml` npm package (p30) and the
aligned API types plus mock API (p31; read `src/web/src/api/types.ts` and p31's Progress
Log for the mechanical edits already made in your directories). Keep the `*Api` calls and
the behaviors covered by existing tests; change markup and add the patterns below.

## Non-goals

- No graph-drawing library (inline SVG only); no npm package changes; no backend
  changes; no edits outside your two page directories.
- Omitted per design 8.1: workflow trigger summary, last run, runs in period; runs-list
  current step and SLA due; human-task due time and tier.

## Run B Deliverables (these supersede the run A list below where they differ)

- `pages/workflows/graph.ts`: `parseWorkflowGraph(text: string, format: "yaml" | "json"):
  { nodes: {id: string; type: string}[]; edges: {from: string; to: string; kind: "next" |
  "branch" | "error" | "send_back"}[] } | null`. YAML via `import { parse } from "yaml"`,
  JSON via `JSON.parse`; `null` on any parse error or missing `steps` list. Edges: `next`,
  each `outcomes` value, each `cases[].next` and `default` (branch), `branches` items and
  `join` (next), `on_error` and `on_exhausted` (error), `send_back.to` / step `to` of a
  `send_back` step (send_back). Ignore unknown targets.
- `pages/workflows/WorkflowGraph.tsx`: read-only inline SVG layered layout (rank by BFS
  from the first step), lucide icon per step type, dashed return edges for send_back,
  `role="img"` with an aria label; renders "Fix validation errors to preview" when the
  graph is null.
- Workflows list (`pages/workflows/index.tsx`): TopBar title "Workflows"; `Table` grouping
  `workflowsApi.list()` items (versions `{id, name, version, active}`) by name: name,
  latest version, active version; link to `/workflows/<name>`; `EmptyState`.
- Workflow detail (`WorkflowEditor.tsx`): TopBar title = name, actions Validate /
  Save as new version / Activate (`Button`s); versions from `workflowsApi.get(name)`;
  source from `workflowsApi.version(name, v)`; mono editor with line numbers side by side
  with `WorkflowGraph`; validation errors listed with JSON paths styled like invalid
  search tokens; a create 422 (`WorkflowValidationError`) shows its errors the same way;
  `raw_value_detected` shows "Source contains sensitive values" and never echoes text;
  Start run form (optional issue id) -> `{run_id}` -> link to `/runs/<run_id>`; schema
  link.
- Runs list (`pages/runs/index.tsx`): TopBar title "Runs"; filters status (run statuses
  of `RunStatus`) and workflow; `Table`: started (`RelativeTime`), workflow (name from one
  `workflowsApi.list()` map by `definition_id`), issue link, trigger, status
  `StatusIndicator` + text.
- Run detail (`RunDetail.tsx`): TopBar title = workflow name + run id, actions Cancel
  run; ordered step list (order by step-run order returned) with status glyph (lucide
  spinner for running, check for succeeded, x for failed, clock for waiting/ready, slash
  for cancelled), step id, step type (from the run's workflow version: map
  `definition_id` -> `{name, version}` via `workflowsApi.list()`, then
  `workflowsApi.version(name, version)` and `parseWorkflowGraph`; omit when unavailable),
  visit count, attempt, `error_code`, collapsible masked input/output (`JSON.parse`
  defensively, render as text; invalid shows "unavailable"); send_back transitions drawn
  as a return arrow from the step run `from_step_run_id` to `to_step_id` with the masked
  reason; current step highlighted (latest step not in a terminal status).
- Waiting human task card: title, assignee, outcome `Select` from `outcomes`, comment,
  Complete -> `humanTasksApi.complete`; 409 `evidence_required` shows "Upload evidence
  first"; no due time.
- Map step progress bar: when a step's `output_json` (or waiting `input_json`) has a
  string `map_job_id`, `mapJobsApi.get(id)` -> bar of succeeded / failed / in flight
  (absent `counts` keys are 0), partitions done/total, paused until, failed shard keys;
  admin Cancel and Retry failed (responses are `{id, status}` -> re-fetch).
- Polling: every 5 s only while the run status is running or waiting; keep form input
  across polls (run-A behavior).

## Run A Deliverables (history; behaviors still covered by tests)

- `pages/workflows/index.tsx`: list view (`workflowsApi.list`: name, latest version,
  active version, trigger summary) or, with `:id` = workflow name, a detail view.
- `pages/workflows/WorkflowEditor.tsx`:
  - A format select (yaml or json).
  - A `JsonTextArea` for the source text.
  - A "Validate" button calling `workflowsApi.validate`; the response errors
    (`{path, code}`) are listed next to the editor.
  - "Save as new version" calling `workflowsApi.create`; `raw_value_detected` shows
    "Source contains sensitive values" and never echoes the text.
  - A version list with an "Activate" button (`workflowsApi.activate`).
  - A "Start run" form with an optional issue id (`workflowsApi.startRun`).
  - The schema is loaded from `workflowsApi.schema()`, and a link to it is shown.
- `pages/runs/index.tsx`: a runs list (filters status and workflow; links to
  `/runs/<id>`), or with `:id` a `RunDetail`.
- `pages/runs/RunDetail.tsx`:
  - Ordered steps with step id, type, visit, status badge, outcome, and started/finished
    times.
  - The current step is highlighted.
  - Transitions as a timeline; `send_back` transitions are marked with their masked
    reason.
  - Waiting human tasks show the assignee, SLA `due_at` and tier, plus a completion form
    (an outcome select from the task's declared outcomes and a comment) calling
    `humanTasksApi.complete`. 409 `evidence_required` shows "Upload evidence first".
  - For `map` steps: a progress bar from `mapJobsApi.get(map_job_id)` (counts by
    status, partitions done/total, paused_until, failed shard keys with error codes) and,
    for admins, Cancel and Retry-failed buttons.
  - A "Cancel run" button.
  - The output JSON (`output_json`) is shown pretty-printed with `JSON.parse`.
- Tests (`*.test.tsx`, mocked fetch):
  - validate errors render with paths;
  - save sends `{source_format, source_text}`;
  - activate calls the right URL;
  - run detail renders 3 steps with one `send_back` transition marked;
  - human task completion posts the outcome and comment;
  - a map step renders "120 / 1000" from the counts;
  - an evidence_required error shows the message.

## Run B Tests (add; keep existing tests green with serializer-shaped responses)

- `graph.ts`: YAML with steps a(next b), b(condition cases -> c, default d), d(send_back
  to a) -> 4 nodes, edges a->b next, b->c and b->d branch, d->a send_back; invalid YAML
  -> null; JSON input parses the same.
- Editor: typing invalid YAML shows "Fix validation errors to preview"; validate errors
  render with paths; a create 422 `{valid:false, errors:[...]}` renders the errors;
  Start run navigates to `/runs/<run_id>`.
- Runs list: workflow name resolved from `definition_id`; status filter sends `status=`.
- Run detail: steps from `{run, steps, transitions, human_tasks}` render in order; the
  send_back transition renders a return arrow labelled with its reason; human task
  completion posts `{outcome, comment}`; a map step with `map_job_id` renders
  "120 / 1000" partitions from the map job detail; a terminal run does not poll.

## Key Pitfalls

- Pages never render `<h1>`; use `TopBar.Slot name="title"`. Never use `innerHTML`.
- Every file under 400 lines; split the run detail into `runs/StepList.tsx`,
  `runs/HumanTaskCard.tsx`, `runs/MapProgress.tsx` if needed.
- `output_json` and `context_json` are strings; parse them defensively (invalid JSON
  shows "unavailable").
- The map-job id is in the map step's output (`map_job_id`) or in its waiting
  `input_json`. Handle both being absent.
- Do not poll faster than every 5 s; stop polling when the run is terminal.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p13-ui-workflows-typecheck.log; echo "exit=$?"
cd src/web && bunx vitest run src/pages/workflows src/pages/runs 2>&1 | tee /tmp/telescreen-p13-ui-workflows-test.log; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-p13-ui-workflows-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p13-ui-workflows-build.log; echo "exit=$?"
! grep -rn 'innerHTML\|<h1' src/web/src/pages/workflows src/web/src/pages/runs
test -z "$(find src/web/src/pages/workflows src/web/src/pages/runs -name '*.ts*' -exec wc -l {} + | awk '$1 >= 400 && $2 != "total"')"
```

All exit 0. If `typecheck` fails only in p12/p14 page files still in progress, record
the log and re-run later; your paths must show zero errors
(`grep -E 'src/pages/(workflows|runs)' <log>` prints nothing). The screenshot smoke is
run by p29.

## Completion Criteria

- [ ] Graph parser and preview, workflows list and detail rebuilt per Run B Deliverables
- [ ] Runs list and run detail (step list, send-back arrows, human task card, map progress) rebuilt
- [ ] Run B tests and the retained run A tests pass; verification commands pass with logs recorded
- [ ] Polling only for running/waiting runs; form input preserved across polls
- [ ] No source edits outside the two page directories and this plan
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:22 JST
**Tasks Completed**: Implemented workflow list/editor and run list/detail pages with validation, version activation, start-run, human-task completion, map progress/actions, run cancellation, defensive JSON rendering and terminal-aware 5-second polling. Added eight mocked-fetch tests. Source edits are limited to the two page directories.
**Tasks In Progress**: Required production build verification.
**Blockers**: The p13 checkpoint declares `artifactRoots: []`; the plan build command writes generated output to `/tmp/telescreen-p13-ui-workflows-web`, outside declared artifact roots. Build was not run. Request a checkpoint amendment declaring a p13-local artifact root, then rerun the isolated-output build.
**Reconciliation requests**: Amend p13 artifactRoots to include `tmp/telescreen-v1-20261008-s319/p13-ui-workflows/build-output` (or another plan-local root), then rerun `cd src/web && bun run build --outDir <declared-root> --emptyOutDir`.
**Hashes**: Source hashes are recorded in `tmp/telescreen-v1-20261008-s319/p13-ui-workflows/edits/008-final-hashes.json`; plan before=`73e9fe1e08c2905d5570fc72d35103e4b87c27a86c7bbd1b90bc2939f47fa260`; plan after is recorded in the same evidence entry.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-p13-ui-workflows-typecheck.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/workflows src/pages/runs` -> exit 0, 8 passed, 0 failed (log: `/tmp/telescreen-p13-ui-workflows-test.log`); `set -o pipefail; cd src/web && bun run build --outDir /tmp/telescreen-p13-ui-workflows-web --emptyOutDir` -> not run, blocked by empty `artifactRoots` (no build log).
**Notes**: Initial typecheck and test attempts found p13-local issues that were repaired before the passing final runs. Current logs are source-matched to the final page files. Formal test-integrity/adversarial and serial integration reviews remain downstream.

### Session: 2026-10-09 07:30 JST
**Tasks Completed**: Re-ran and completed all p13 verification gates on the current page sources. The earlier `/tmp` build-output blocker is resolved under `impl-plans/conventions.md` section 7, which explicitly permits isolated `/tmp/telescreen-<planId>-web` output without an `artifactRoots` entry. All completion criteria are checked; page-source edits remain confined to `src/web/src/pages/workflows` and `src/web/src/pages/runs`.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/pages/workflows/index.tsx`=75af9329a8951a0d6a898c3778bb3c7c4e42a5e589b9a009bc7317f32ad5a0c1; `WorkflowEditor.tsx`=58a0b1efdff98e3273291d15caa558fe3e6511ade2cd583a8b5aa8a499d3ce8c; `WorkflowEditor.test.tsx`=b8ce567871db43dd7675331cdef217c665d6692a6aaac88d402a56785951a5d5; `src/web/src/pages/runs/index.tsx`=e4a2e88451019c78c52348cb296a9b176af6b5f39a983e62a5c92ad15a0079da; `RunDetail.tsx`=12ae0c3c543129a969fa97d53d820505fb7f62ad2072f052ec90c322c776def5; `RunDetail.test.tsx`=85a5abb6187493aa89c8683b00d9c2fdfff8681b32947a565747413ddd264db0.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-p13-ui-workflows-typecheck.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/workflows src/pages/runs` -> exit 0, 8 passed, 0 failed (log: `/tmp/telescreen-p13-ui-workflows-test.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p13-ui-workflows-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-p13-ui-workflows-build.log`).
**Notes**: The final logs include explicit exit=0 records. Formal review and combined-tree integration are downstream workflow steps.

### Session: 2026-10-09 07:37 JST — adversarial repair
**Tasks Completed**: Addressed `P13-POLL-REMOUNTS-TASK-FORM`. RunDetail and MapProgress loading fallbacks now show only before their first value resolves, preserving the initial loading and error fallbacks during background refreshes. Human-task outcome and comment drafts are held in RunDetail by `task.id`, so fresh task objects cannot reset the responder's form. Added two fake-timer regressions; the original eight tests and assertions remain unchanged.
**Tasks In Progress**: None for p13 implementation. Formal test-integrity/adversarial review and final serial integration are downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `RunDetail.tsx`=068745a1c34fa5ef3636d4dd88bf4ce643c6d760588e3648f363165a7187bc81; `RunDetail.test.tsx`=0510adb9a4f089d94aa11b8bb040a112614109124c7cd4a601a9614f1d7346ed. Plan after-hash is recorded in `tmp/telescreen-v1-20261008-s319/p13-ui-workflows/edits/016-final-repair-hashes.json`.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (`/tmp/telescreen-p13-ui-workflows-typecheck-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/workflows src/pages/runs` -> exit 0, 10 passed, 0 failed (`/tmp/telescreen-p13-ui-workflows-test-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p13-ui-workflows-web-final --emptyOutDir` -> exit 0 (`/tmp/telescreen-p13-ui-workflows-build-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test` -> exit 0, 39 passed, 0 failed across 21 files (`/tmp/telescreen-p13-ui-workflows-combined-test-final.log`).
**Prior repair attempts**: `/tmp/telescreen-p13-poll-remount-test-attempt1.log` had 4 passed/2 failed because the installed testing library has no `act` export; `/tmp/telescreen-p13-poll-remount-test-attempt2.log` had 5 passed/1 failed because three microtask turns did not resolve the initial resource. Both test-harness issues were corrected without changing product behavior; attempt 3 passed 6/6. The final gates above were rerun after those corrections and include the final source.
