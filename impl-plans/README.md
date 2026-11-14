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
progress-log rules). Wave = 1 + max(wave of dependencies).

| Plan | Wave | Status | Design Reference | Last Updated |
|------|------|--------|------------------|--------------|
| `active/p01-foundation.md` | 1 | Ready | architecture.md s4-s8, command.md | 2026-10-08 |
| `active/p02-db-schema.md` | 2 | Ready | design-data-model.md | 2026-10-08 |
| `active/p03-detection-rules.md` | 2 | Ready | design-detectors.md | 2026-10-08 |
| `active/p04-ports.md` | 2 | Ready | architecture.md s4 | 2026-10-08 |
| `active/p05-police-contract.md` | 2 | Ready | design-police-contract.md | 2026-10-08 |
| `active/p07-web-shell.md` | 2 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p06-workflow-dsl.md` | 3 | Ready | design-workflow-engine.md | 2026-10-08 |
| `active/p08-masking.md` | 3 | Ready | design-masking.md | 2026-10-08 |
| `active/p09-detection-llm-ml.md` | 3 | Ready | design-detectors.md | 2026-10-08 |
| `active/p10-api-common.md` | 3 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p11-police-sdk.md` | 3 | Ready | design-police-contract.md | 2026-10-08 |
| `active/p12-ui-issues.md` | 3 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p13-ui-workflows.md` | 3 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p14-ui-ops.md` | 3 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p21-collectors-slack.md` | 3 | Ready | design-collectors-and-ingestion.md | 2026-10-08 |
| `active/p22-collectors-google.md` | 3 | Ready | design-collectors-and-ingestion.md, design-map-reduce-sweeps.md | 2026-10-08 |
| `active/p23-collectors-github.md` | 3 | Ready | design-collectors-and-ingestion.md | 2026-10-08 |
| `active/p15-audit-search.md` | 4 | Ready | design-data-model.md, design-api-and-ui.md | 2026-10-08 |
| `active/p16-issues-ingest.md` | 4 | Ready | design-collectors-and-ingestion.md | 2026-10-08 |
| `active/p17-notifications.md` | 4 | Ready | design-masking.md | 2026-10-08 |
| `active/p18-evidence.md` | 4 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p19-police-bureau.md` | 4 | Ready | design-police-contract.md | 2026-10-08 |
| `active/p20-collectors-core.md` | 4 | Ready | design-collectors-and-ingestion.md | 2026-10-08 |
| `active/p24-workflow-engine.md` | 4 | Ready | design-workflow-engine.md | 2026-10-08 |
| `active/p25-workflow-nodes.md` | 4 | Ready | design-workflow-engine.md | 2026-10-08 |
| `active/p26-workflow-mapreduce.md` | 4 | Ready | design-map-reduce-sweeps.md | 2026-10-08 |
| `active/p27-assistant.md` | 4 | Ready | design-api-and-ui.md | 2026-10-08 |
| `active/p29-examples-compose.md` | 4 | Ready | architecture.md s3 | 2026-10-08 |
| `active/p28-assembly.md` | 5 | Ready (depends on every wave-3/4 feature plan, including the UI plans p07, p12, p13, p14) | command.md, design-masking.md | 2026-10-08 |

## Completed Plans

| Plan | Completed | Design Reference |
|------|-----------|------------------|
| (No completed plans yet) | - | - |

## Phase Dependencies (for impl-exec-auto)

**IMPORTANT**: This section is used by impl-exec-auto to determine which plans to load.
Only plans from eligible phases should be read to minimize context loading.

### Phase Status

| Phase | Status | Depends On |
|-------|--------|------------|
| 1 | NOT_STARTED | - |
| 2 | BLOCKED | Phase 1 |
| 3 | BLOCKED | Phase 2 (per-plan dependencies) |
| 4 | BLOCKED | Phase 3 (per-plan dependencies) |
| 5 | BLOCKED | Phase 4 |

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
      "p26-workflow-mapreduce.md", "p27-assistant.md", "p29-examples-compose.md"],
  5: ["p28-assembly.md"]
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
