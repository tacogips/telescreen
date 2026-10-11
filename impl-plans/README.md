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

There are no active plans.

Telescreen v1 is complete (all 32 plans integration-accepted, final commit 7a8c600).
Telescreen v1.1 (F1-F10 of `design-docs/specs/design-v1-1-dspm-adoption.md`) is complete:
all 21 v11 plans are integration-accepted and archived under `completed/` (2026-10-11).
Telescreen v1.2 (N1-N3, R1, R2 of
`design-docs/specs/design-v1-2-notifications-and-identity.md`) is complete: all 12 v12
plans are integration-accepted and archived under `completed/` (2026-10-12).
Run dispatch manifests, including the v1.1 manifests `telescreen-v11-20261010-c1` to `-c4`
and the v1.2 manifests `telescreen-v12-20261011-d1` to `-d4`, are archived under
`completed/dispatch/`.

## Completed Plans

### v1.2 (notification templates, notification sets, roles, OIDC SSO, live verification)

Created 2026-10-11 at base 7402f51 from
`design-docs/specs/design-v1-2-notifications-and-identity.md` (section 10). Waves ran in
order, and plans within a wave had disjoint write paths. v12-p01 to v12-p04 and v12-p07 to
v12-p09 were integration-accepted at fcfb198 (session 9). In session 10 (manifest d4),
v12-p05 and v12-p06 were re-verified on the committed tree, v12-p10 to v12-p12 were
implemented, and the combined tree was integration-accepted (comm-000471).

| Plan | Wave | Depends On | Design Reference |
|------|------|------------|------------------|
| `completed/v12-p01-foundation.md` | W1 | - | v1.2 s1, s2, s5.1; command.md |
| `completed/v12-p02-db.md` | W1 | - | design-data-model.md v1.2 additions |
| `completed/v12-p03-ui-foundation.md` | W1 | - | design-ui-sentry.md s10.1, s10.2, s10.4 |
| `completed/v12-p04-notification-templates.md` | W2 | p01, p02 | v1.2 s3, s4.3, s8 |
| `completed/v12-p05-workflow-notification-sets.md` | W2 | p01, p02 | v1.2 s4; design-workflow-engine.md DSL v2 |
| `completed/v12-p06-rbac-enforcement.md` | W2 | p01, p02 | v1.2 s5.1, s5.2, s6.4 |
| `completed/v12-p07-directory-roles.md` | W2 | p01, p02 | v1.2 s5.3, s5.4, s6.3 |
| `completed/v12-p08-oidc-identity.md` | W2 | p01 | v1.2 s6.2, s6.3, s8 |
| `completed/v12-p09-ui-pages.md` | W2 | p03 | design-ui-sentry.md s10.3 |
| `completed/v12-p10-api-routers.md` | W3 | p04, p06, p07, p08 | design-api-and-ui.md v1.2 endpoints |
| `completed/v12-p11-wiring-cli.md` | W4 | p04-p08, p10 | v1.2 s5.2, s7.1, s7.4 |
| `completed/v12-p12-verification-docs.md` | W5 | p11, p09, p03 | v1.2 s6.5, s7, s8, s10 |

### v1.1 (F1-F10)

Created 2026-10-10 at base 289699e. Waves ran in order, and plans within a wave had
disjoint write paths. v11-p01 to v11-p20 were integration-accepted at 809b02d (v11-p07 at
af943da, after the owner's naive/aware datetime fix in the policy max-age check).
v11-p21 was re-verified on the committed tree at 366c16f and integration-accepted in
session 6.

| Plan | Wave | Depends On | Design Reference |
|------|------|------------|------------------|
| `completed/v11-p01-foundation.md` | V1 | - | v1.1 s2, s9, s10; command.md |
| `completed/v11-p02-db-migration.md` | V1 | - | design-data-model.md v1.1 additions |
| `completed/v11-p03-police-contract.md` | V1 | - | design-police-contract.md Contract 1.1 |
| `completed/v11-p04-ui-foundation.md` | V1 | - | design-ui-sentry.md s9 |
| `completed/v11-p05-issues-record.md` | V2 | p01, p02 | v1.1 s3-s5, s7, s9-s12 |
| `completed/v11-p06-issues-remediation.md` | V2 | p01, p02 | v1.1 s5, s7, s9, s11, s12 |
| `completed/v11-p07-policy-risk.md` | V2 | p01, p02 | v1.1 s4, s10 |
| `completed/v11-p08-collectors-core.md` | V2 | p01, p02 | v1.1 s5, s6, s8 |
| `completed/v11-p09-collectors-slack.md` | V2 | p01 | v1.1 s3, s4, s6, s7 |
| `completed/v11-p10-collectors-google.md` | V2 | p01 | v1.1 s3, s4, s6, s7 |
| `completed/v11-p11-collectors-github.md` | V2 | p01 | v1.1 s3, s4, s6, s7 |
| `completed/v11-p12-police-bureau.md` | V2 | p01, p02, p03 | Contract 1.1; v1.1 s8 |
| `completed/v11-p13-workflow.md` | V2 | p01 | v1.1 s5, s7, s10, s14 |
| `completed/v11-p14-directory-search.md` | V2 | p01, p02 | v1.1 s7, s10 |
| `completed/v11-p15-ui-issues.md` | V2 | p04 | design-ui-sentry.md s9.3 |
| `completed/v11-p16-ui-ops.md` | V2 | p04 | design-ui-sentry.md s9.3, s9.4 |
| `completed/v11-p17-api-issues.md` | V3 | p05, p06, p07, p14 | design-api-and-ui.md v1.1 endpoints |
| `completed/v11-p18-api-ops.md` | V3 | p05, p06, p08, p12, p13, p14 | design-api-and-ui.md v1.1 endpoints |
| `completed/v11-p19-wiring.md` | V4 | p05-p14, p17, p18 | v1.1 s12, s14 |
| `completed/v11-p20-docs-examples.md` | V4 | p13, p17, p18 | v1.1 s1, s7; command.md |
| `completed/v11-p21-leak-e2e.md` | V5 | p19, p20, p15, p16 | v1.1 s13 |

### v1

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
| 8 (v1.1 wave V1) | COMPLETED | Phase 7 |
| 9 (v1.1 wave V2) | COMPLETED | Phase 8 (per-plan dependencies) |
| 10 (v1.1 wave V3) | COMPLETED | Phase 9 |
| 11 (v1.1 wave V4) | COMPLETED | Phase 10 |
| 12 (v1.1 wave V5) | COMPLETED | Phase 11 |
| 13 (v1.2 wave W1) | COMPLETED | Phase 12 |
| 14 (v1.2 wave W2) | COMPLETED | Phase 13 (per-plan dependencies) |
| 15 (v1.2 wave W3) | COMPLETED | Phase 14 |
| 16 (v1.2 wave W4) | COMPLETED | Phase 15 |
| 17 (v1.2 wave W5) | COMPLETED | Phase 16 |

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
  7: ["p29-examples-compose.md"],
  8: ["v11-p01-foundation.md", "v11-p02-db-migration.md", "v11-p03-police-contract.md", "v11-p04-ui-foundation.md"],
  9: ["v11-p05-issues-record.md", "v11-p06-issues-remediation.md", "v11-p07-policy-risk.md",
      "v11-p08-collectors-core.md", "v11-p09-collectors-slack.md", "v11-p10-collectors-google.md",
      "v11-p11-collectors-github.md", "v11-p12-police-bureau.md", "v11-p13-workflow.md",
      "v11-p14-directory-search.md", "v11-p15-ui-issues.md", "v11-p16-ui-ops.md"],
  10: ["v11-p17-api-issues.md", "v11-p18-api-ops.md"],
  11: ["v11-p19-wiring.md", "v11-p20-docs-examples.md"],
  12: ["v11-p21-leak-e2e.md"],
  13: ["v12-p01-foundation.md", "v12-p02-db.md", "v12-p03-ui-foundation.md"],
  14: ["v12-p04-notification-templates.md", "v12-p05-workflow-notification-sets.md",
       "v12-p06-rbac-enforcement.md", "v12-p07-directory-roles.md", "v12-p08-oidc-identity.md",
       "v12-p09-ui-pages.md"],
  15: ["v12-p10-api-routers.md"],
  16: ["v12-p11-wiring-cli.md"],
  17: ["v12-p12-verification-docs.md"]
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
