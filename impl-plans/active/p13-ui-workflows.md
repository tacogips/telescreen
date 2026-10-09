# P13 UI Workflows and Runs Pages Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (UI: Workflows, Runs), design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

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

- [x] Workflow list, editor (validate, save, activate, start run), runs list and run detail (steps, transitions, human tasks, map progress) delivered
- [x] Tests listed above pass
- [x] A 5-second waiting-run poll preserves the human-task outcome and comment; terminal runs are not polled
- [x] No source edits outside the two page directories
- [x] Progress Log updated

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
