# Implementation Plans

This directory contains implementation plans that translate design documents into actionable implementation specifications.

## Purpose

Implementation plans bridge design documents (what to build) and actual code (how to build). They provide:
- Clear deliverables without code
- Interface and function specifications
- Dependency mapping for concurrent execution
- Progress tracking across sessions

## Directory Structure

```
impl-plans/
├── README.md              # This file
├── active/                # Currently active implementation plans
│   └── <feature>.md       # One file per feature being implemented
├── completed/             # Completed implementation plans (archive)
│   └── <feature>.md       # Completed plans for reference
└── templates/             # Plan templates
    └── plan-template.md   # Standard plan template
```

## File Size Limits

**IMPORTANT**: Implementation plan files must stay under 400 lines to prevent OOM errors.

| Metric | Limit |
|--------|-------|
| Line count | MAX 1000 lines |
| Modules per plan | MAX 8 modules |
| Tasks per plan | MAX 10 tasks |

Large features are split into multiple related plans with cross-references.

## Active Plans

All plans share `impl-plans/conventions.md` (ownership, typing, verification and
progress-log rules).

No active plans. Telescreen v1 is complete (all 32 plans integration-accepted,
final commit 7a8c600). The v1.1 backlog is `design-docs/specs/design-v1-1-dspm-adoption.md`.
Run dispatch manifests are archived under `completed/dispatch/`.

## Completed Plans

| Plan | Wave | Completed | Design Reference |
|------|------|-----------|------------------|
| `completed/p01-foundation.md` | 1 | Integration-accepted (2026-10-09); archived 2026-10-10 | architecture.md s4-s8, command.md |
| `completed/p02-db-schema.md` | 2 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-data-model.md |
| `completed/p03-detection-rules.md` | 2 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-detectors.md |
| `completed/p04-ports.md` | 2 | Integration-accepted (2026-10-09); archived 2026-10-10 | architecture.md s4 |
| `completed/p05-police-contract.md` | 2 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-police-contract.md |
| `completed/p07-web-shell.md` | 2 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-api-and-ui.md |
| `completed/p06-workflow-dsl.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-workflow-engine.md |
| `completed/p08-masking.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-masking.md |
| `completed/p09-detection-llm-ml.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-detectors.md |
| `completed/p10-api-common.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-api-and-ui.md |
| `completed/p30-ui-design-system.md` | B1 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-ui-sentry.md |
| `completed/p31-ui-mock-api.md` | B1 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-ui-sentry.md, design-synthetic-test-dataset.md |
| `completed/p12-ui-issues.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-ui-sentry.md |
| `completed/p13-ui-workflows.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-ui-sentry.md |
| `completed/p14-ui-ops.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-ui-sentry.md |
| `completed/p22-collectors-google.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-collectors-and-ingestion.md, design-map-reduce-sweeps.md |
| `completed/p23-collectors-github.md` | 3 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-collectors-and-ingestion.md |
| `completed/p15-audit-search.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-data-model.md, design-api-and-ui.md |
| `completed/p16-issues-ingest.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-collectors-and-ingestion.md |
| `completed/p18-evidence.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-api-and-ui.md |
| `completed/p20-collectors-core.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-collectors-and-ingestion.md |
| `completed/p24-workflow-engine.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-workflow-engine.md |
| `completed/p25-workflow-nodes.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-workflow-engine.md |
| `completed/p27-assistant.md` | 4 | Integration-accepted (2026-10-09); archived 2026-10-10 | design-api-and-ui.md |
| `completed/p28-assembly.md` | 5 | Integration-accepted (2026-10-10); archived 2026-10-10 | command.md, design-masking.md |
| `completed/p11-police-sdk.md` | 3 | 2026-10-09 (run A2, session 340) | design-police-contract.md |
| `completed/p21-collectors-slack.md` | 3 | 2026-10-09 (run A2, session 340) | design-collectors-and-ingestion.md |
| `completed/p17-notifications.md` | 4 | 2026-10-09 (run A2, session 340) | design-masking.md |
| `completed/p19-police-bureau.md` | 4 | 2026-10-09 (run A2, session 340) | design-police-contract.md |
| `completed/p26-workflow-mapreduce.md` | 4 | 2026-10-09 (run A2, session 340) | design-map-reduce-sweeps.md |
| `completed/p28b-e2e-flows.md` | B4-1 | 2026-10-10 (run B4) | architecture.md s8-s9, design-workflow-engine.md, design-map-reduce-sweeps.md |
| `completed/p28c-leak-matrix.md` | B4-1 | 2026-10-10 (run B4) | design-masking.md, architecture.md s7-s9, design-ui-sentry.md 8.3 |
| `completed/p29-examples-compose.md` | B4-2 | 2026-10-10 (run B4) | architecture.md s3, s8, command.md |

## Phase Dependencies (for impl-exec-auto)

**IMPORTANT**: This section is used by impl-exec-auto to determine which plans to load.
Only plans from eligible phases should be read to minimize context loading.

### Phase Status

| Phase | Status | Depends On |
|-------|--------|------------|
| 1 | COMPLETED | - |
| 2 | COMPLETED | Phase 1 |
| 3 | COMPLETED | Phase 2 (per-plan dependencies) |
| 4 | COMPLETED | Phase 3 (per-plan dependencies) |
| 5 | COMPLETED | Phase 4 |
| 6 | COMPLETED | Phase 5 |
| 7 | COMPLETED | Phase 6 |

Run B added `p30-ui-design-system.md` and `p31-ui-mock-api.md` (wave B1, before the
reworked p12, p13 and p14), split the end-to-end and leak tests into p28b and p28c after
p28, and moved p29 after them.

### Phase to Plans Mapping

```
PHASE_TO_PLANS = {
  1: ["p01-foundation.md"],
  2: ["p02-db-schema.md", "p03-detection-rules.md", "p04-ports.md", "p05-police-contract.md", "p07-web-shell.md"],
  3: ["p06-workflow-dsl.md", "p08-masking.md", "p09-detection-llm-ml.md", "p10-api-common.md", "p11-police-sdk.md",
      "p12-ui-issues.md", "p13-ui-workflows.md", "p14-ui-ops.md", "p21-collectors-slack.md",
      "p22-collectors-google.md", "p23-collectors-github.md"],
  4: ["p15-audit-search.md", "p16-issues-ingest.md", "p17-notifications.md", "p18-evidence.md",
      "p19-police-bureau.md", "p20-collectors-core.md", "p24-workflow-engine.md", "p25-workflow-nodes.md",
      "p26-workflow-mapreduce.md", "p27-assistant.md"],
  5: ["p28-assembly.md"],
  6: ["p28b-e2e-flows.md", "p28c-leak-matrix.md"],
  7: ["p29-examples-compose.md"]
}
```

## Workflow

### Creating a New Plan

1. Use the `/impl-plan` command with a design document reference
2. Or manually create a plan using `templates/plan-template.md`
3. Save to `active/<feature-name>.md`
4. Update this README with the new plan entry
5. **IMPORTANT**: If plan exceeds 400 lines, split into multiple files

### Working on a Plan

1. Read the active plan
2. Select a subtask to work on (consider parallelization)
3. Implement following the deliverable specifications
4. Update task status and progress log
5. Mark completion criteria as done

### Completing a Plan

1. Verify all completion criteria are met
2. Update status to "Completed"
3. Move file from `active/` to `completed/`
4. Update this README

## Guidelines

- Plans contain NO implementation code
- Plans specify interfaces, functions, and file structures
- Subtasks should be as independent as possible for parallel execution
- Always update progress log after each session
- **Keep each plan file under 400 lines** - split if necessary
