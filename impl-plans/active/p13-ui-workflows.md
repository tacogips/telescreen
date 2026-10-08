# P13 UI Workflows and Runs Pages Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md (UI: Workflows, Runs), design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p13-ui-workflows`
- planPath: `impl-plans/active/p13-ui-workflows.md`
- wave: 3
- dependsOn: `p07-web-shell`
- writePaths:
  - `impl-plans/active/p13-ui-workflows.md`
  - `src/web/src/pages/workflows`
  - `src/web/src/pages/runs`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 8).

## Intent and Context

These are the workflow editor and run-progress pages (acceptance: "workflow editor/run
view"). Use only the p07 API client, types and components. Do not edit them.

## Non-goals

- No graph-drawing library; no new npm packages; no backend changes.

## Deliverables

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

## Key Pitfalls

- `output_json` and `context_json` are strings; parse them defensively (invalid JSON
  shows "unavailable").
- The map-job id is in the map step's output (`map_job_id`) or in its waiting
  `input_json`. Handle both being absent.
- Do not poll faster than every 5 s; stop polling when the run is terminal.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)   # p01 installed it; do not reinstall concurrently
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p13-ui-workflows-typecheck.log   # own files: zero errors
cd src/web && bunx vitest run src/pages/workflows src/pages/runs 2>&1 | tee /tmp/telescreen-p13-ui-workflows-test.log   # exit 0
cd src/web && bun run build --outDir /tmp/telescreen-p13-ui-workflows-web --emptyOutDir   # exit 0
```

## Completion Criteria

- [ ] Workflow list, editor (validate, save, activate, start run), runs list and run detail (steps, transitions, human tasks, map progress) delivered
- [ ] Tests listed above pass
- [ ] No edits outside the two page directories
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
