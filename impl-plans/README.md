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

### Service split (svc, created 2026-10-15)

These plans come from `design-docs/specs/design-service-split.md` (R1-R8, D1-D5) on
`feat/service-split`. Plans in the same wave have disjoint write paths.

| Wave | Plans |
| --- | --- |
| 1 | [svc-p01-foundation](active/svc-p01-foundation.md) (integration-accepted at `19074e9`) |
| 2 | [svc-p02-db](active/svc-p02-db.md) (integration-accepted, checkpoint `077b71a`) |
| 3 | [svc-p03-publishers](active/svc-p03-publishers.md), [svc-p07-service-runtime](active/svc-p07-service-runtime.md) (integration-accepted, checkpoint `a5392d9`) |
| 4 (g7 wave B) | [svc-p04-inventory](active/svc-p04-inventory.md), [svc-p05-notification-service](active/svc-p05-notification-service.md), [svc-p06-composition](active/svc-p06-composition.md) |
| 5 (g7 wave C) | [svc-p08-verification-deploy](active/svc-p08-verification-deploy.md) |

The current dispatch manifest is
[`telescreen-svc-20261016-g7`](active/telescreen-svc-20261016-g7-dispatch.json) (run 7,
base `a5392d9`). It supersedes g6 (run 6, base `077b71a`), g5 (run 5, base `8b09786`),
g4 (run 4, base `c3ed4bc`), g3 (run 3, base `6017743`), g2 (run 2, base `19074e9`) and
g1. g7 keeps g6's plans, write paths and DAG. It records svc-p01, svc-p02, svc-p03 and
svc-p07 as accepted dependencies at `a5392d9`. Each wave B plan has a 'Run 7 Scope'
section: svc-p04 adds its R5 retention-dispatch cases, svc-p05 is re-verified only, and
svc-p06 adds the `notification-store` dispatch. Earlier session logs are in
[`logs/svc-p02-db-2026-10-15.md`](logs/svc-p02-db-2026-10-15.md),
[`logs/svc-p02-db-2026-10-15-run3.md`](logs/svc-p02-db-2026-10-15-run3.md),
[`logs/svc-p04-inventory-2026-10-16.md`](logs/svc-p04-inventory-2026-10-16.md) and
[`logs/svc-p06-composition-2026-10-16.md`](logs/svc-p06-composition-2026-10-16.md).
Serial finalization after the fanout applies the Nodetto layout fix
(design-service-split.md section 12), archives these plans and the completed
`inventory-alert-boundaries.md` to `completed/`, the seven manifests to
`completed/dispatch/`, and updates this index.

The [inventory and alert boundary plan](active/inventory-alert-boundaries.md) is complete:
independent persistence, durable handoffs, inventory master authority, owner migrations
and verified cutover/rollback. Its [completion audit](../design-docs/specs/design-inventory-boundary-verification.md)
records final backend/web/schema checks. The completed plan retains its original path.

Telescreen v1 is complete (all 32 plans integration-accepted, final commit 7a8c600).
Telescreen v1.1 (F1-F10 of `design-docs/specs/design-v1-1-dspm-adoption.md`) is complete:
all 21 v11 plans are integration-accepted and archived under `completed/` (2026-10-11).
Telescreen v1.2 (N1-N3, R1, R2 of
`design-docs/specs/design-v1-2-notifications-and-identity.md`) is complete: all 12 v12
plans are integration-accepted and archived under `completed/` (2026-10-12).
Telescreen v1.3 (S0-S10 of `design-docs/specs/design-v1-3-sentry-parity.md`) is complete:
all 17 v13 plans are accepted and archived under `completed/` (2026-10-13).
Telescreen v1.4 (R1-R9 of `design-docs/specs/design-v1-4-data-inventory-requirements.md`)
is complete: all 9 v14 plans are accepted and archived under `completed/` (2026-10-14).
Run dispatch manifests, including the v1.1 manifests `telescreen-v11-20261010-c1` to `-c4`,
the v1.2 manifests `telescreen-v12-20261011-d1` to `-d4`, the v1.3 manifests
`telescreen-v13-20261012-e1`, `-e2`, `telescreen-v13-20261013-e3` and `-e4`, and the v1.4
manifests `telescreen-v14-20261013-f1`, `telescreen-v14-20261014-f2` and `-f3`, are
archived under `completed/dispatch/`.

## Completed Plans

The [inventory microservice extraction](completed/inventory-microservice.md) is complete:
independent API/worker deployment, owner database and authenticated HTTP adapters,
verified with real TCP processes, PostgreSQL, full backend/web CI and packaged artifacts.
See its [completion audit](../design-docs/specs/design-inventory-microservice-verification.md).

### v1.4 (data inventory: R1-R9 of `design-docs/specs/design-v1-4-data-inventory-requirements.md`)

Created 2026-10-13 on `feat/data-inventory` from `design-docs/specs/design-v1-4-data-inventory.md`
section 13. Waves ran in order, and the plans of a wave had disjoint write paths. All 9
plans were archived to `completed/` (and the f1-f3 manifests to `completed/dispatch/`) by
the serial finalization on 2026-10-14, after p08 passed test integrity and adversarial
review in run 3.

Dispatch runs (later runs record accepted plans as `acceptedDependencies`):

- run 1 (session 15, manifest `completed/dispatch/telescreen-v14-20261013-f1-dispatch.json`):
  p01-p08 in waves 1-4. p01-p07 were accepted; the owner's live verification then fixed O1
  and added the E3 full-listing flow (O2) (requirements section 8);
- run 2 (session 16, base `3c5c1dd`, manifest
  `completed/dispatch/telescreen-v14-20261014-f2-dispatch.json`, design section 17): wave A
  p09 (live fixes L1-L7), wave B p08 re-verify. p09 was integration-accepted; the run
  stopped in integration review on an unowned ruff E501 line. The owner fixed it (O3) and
  the item-count chart height (O4) (requirements section 8.1);
- run 3 (session 17, base `42864aa`, manifest
  `completed/dispatch/telescreen-v14-20261014-f3-dispatch.json`, design section 17.7): p08
  re-verify only, with no source change. ruff, typelint (603 files), ty, pytest (1111
  passed, 11 skipped), uv build, ddl-check, web typecheck, vitest (262 passed), build and
  strict screenshots (0 skipped states, 0 browser errors) passed.

| Plan | Wave | Depends On | Design Reference |
|------|------|------------|------------------|
| `completed/v14-p01-foundation.md` | 1 | - | v1.4 s3, s6.3, s6.4, s8.1, s13; Contract 1.2 |
| `completed/v14-p02-db.md` | 1 | - | v1.4 s4, D23; design-data-model.md v1.4 additions |
| `completed/v14-p03-ui-foundation.md` | 1 | - | v1.4 s9, s10; design-ui-sentry.md s12 |
| `completed/v14-p04-inventory-core.md` | 2 | p01, p02 | v1.4 s2, s5, s6, s9, s11, s12 |
| `completed/v14-p05-scan-hooks.md` | 2 | p01, p02 | v1.4 s5.1, s5.4, s6.3, s6.4; v1.4 inventory hooks |
| `completed/v14-p06-ui-pages.md` | 2 | p03 | v1.4 s7.2, s9, s10; design-ui-sentry.md s12 |
| `completed/v14-p07-fields-retention.md` | 3 | p04 | v1.4 s2 (D14-D18), s7, s8, s9, s11, s12 |
| `completed/v14-p09-live-fixes.md` | run 2, A | p01, p03, p04, p06, p07 | v1.4 s17; requirements s8 |
| `completed/v14-p08-wiring-verification.md` | 4 (re-verified in runs 2 and 3) | p04, p05, p06, p07, p09 | v1.4 s9, s12-s14, s17.7; design-masking.md v1.4 |

### v1.3 (Sentry parity: S0-S10 of `design-docs/specs/design-v1-3-sentry-parity.md`)

Created 2026-10-12 at base 57f389b from `design-docs/specs/design-v1-3-sentry-parity.md`
section 15. Waves ran in order. Within a wave, write paths were disjoint. Files created by a
wave-1 plan and edited by a later plan are listed in that later plan's `sharedPaths` with a
note. All 17 plans were archived to `completed/` (and the e1-e4 manifests to
`completed/dispatch/`) by the serial finalization on 2026-10-13, after p17 passed test
integrity and adversarial review.

Dispatch runs (at most 8 plans each; later runs record accepted plans as
`acceptedDependencies`):

- run 1: p01, p02, p03 (wave 1); p04, p05, p09, p13 (wave 2) (7 plans). Manifest
  `completed/dispatch/telescreen-v13-20261012-e1-dispatch.json` was blocked on write paths
  (owner reconciled at `d8073b1`); `completed/dispatch/telescreen-v13-20261012-e2-dispatch.json`
  redispatches the same scope (p01 and p02 re-verify, p03 finishes). Session 12 (2026-10-13):
  all 7 run-1 plans are Completed (0 open boxes) and the combined wave-2 tree passed the
  integration review (comm-000580: pytest 922 passed / 8 skipped, vitest 199 passed,
  non-strict screenshots 0 browser errors). They were `acceptedDependencies` for runs 2
  and 3. The new routers were unregistered until v13-p12 (run 3). Open low items:
  INT-W2-04 (custom-period all-matching bulk lacks start/end), INT-W2-05 (all-matching
  request_verification is selection-only) and archive `until_escalating` parsing. All
  three are owned by p06 in run 2, on both server and UI (design section 6.1 and the
  section 15 run-2 notes);
- run 2: p08, p11, p15, p16 (wave 2); p06, p07, p10, p14 (wave 3) (8 plans). Manifest
  `completed/dispatch/telescreen-v13-20261013-e3-dispatch.json` (base 9accc07) records p01-p05, p09
  and p13 as `acceptedDependencies` and p12 and p17 as `laterRunPlans`. The shared-file
  owners in run 2 are: p11 for `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`;
  p16 for the one `screenshots.ts` selector; p06 for the bulk parts of `client.ts`, the
  mock router and `IssueList*`; p07 for the four existing issue tests it may break.
  Inventory tests (`tests/db/test_models.py`, `tests/notifications/**`, `tests/leak/**`,
  the route walk) are read-only;
- run 3: p07 (wave 3, re-verify), p12 (wave 4), p17 (wave 5), then serial finalization.
  Manifest `completed/dispatch/telescreen-v13-20261013-e4-dispatch.json` (base ca818c3)
  records p01-p06, p08-p11 and p13-p16 as `acceptedDependencies`. Waves A (p07), B (p12)
  and C (p17) ran serially with one plan each, so every shared file a plan's change can
  break was in that plan's writePaths (design section 15 "Run 3" notes). p07 was
  re-verified green; p12 wired the v1.3 routers and passed the full Python suite (991
  passed, 8 skipped, 0 failed) plus the API, route-permission, typing, formatting, and
  build gates. p17 verified leak flows 1-9 (after the P17-LEAK-01 repair) and E1-E5
  offline (998 passed, 10 skipped, 0 failed), recorded the owner-only live checks (not run
  in the agent environment; 5 passed on the owner's dev stack), passed strict screenshots
  (89 outputs, no skipped state or browser error), and updated README/design status. Test
  integrity and adversarial review accepted p17; the serial finalization archived the v13
  plans and the e1-e4 manifests.

Every plan's wave is greater than the waves of its dependencies.

| Plan | Wave | Depends On | Design Reference |
|------|------|------------|------------------|
| `completed/v13-p01-foundation.md` | W1 | - | v1.3 s2, s15 |
| `completed/v13-p02-db.md` | W1 | - | v1.3 s13.1; design-data-model.md v1.3 additions |
| `completed/v13-p03-ui-foundation.md` | W1 | - | design-ui-sentry.md s11.1, s11.3 |
| `completed/v13-p04-issue-stream-backend.md` | W2 | p01, p02 | v1.3 s3 (B2, B3, B5), s4, s5 |
| `completed/v13-p05-triage-lifecycle-backend.md` | W2 | p01, p02 | v1.3 s6.2, s6.3, s7, s8.1 |
| `completed/v13-p08-alerts-backend.md` | W2 | p01, p02 | v1.3 s9 |
| `completed/v13-p09-notifications-backend.md` | W2 | p01, p02 | v1.3 s10 |
| `completed/v13-p11-directory-ownership-backend.md` | W2 | p01, p02 | v1.3 s12 |
| `completed/v13-p13-ui-stream.md` | W2 | p03 | design-ui-sentry.md s11.2 |
| `completed/v13-p07-issue-detail-backend.md` | W3 | p01, p02, p04 | v1.3 s3 (B1, B4, B6, B7), s8 |
| `completed/v13-p10-dashboard-integrations-backend.md` | W3 | p01, p02, p04 | v1.3 s11, s12 |
| `completed/v13-p14-ui-detail.md` | W3 | p03, p13 | design-ui-sentry.md s11.2 |
| `completed/v13-p15-ui-alerts-profile.md` | W2 | p03 | design-ui-sentry.md s11.2 |
| `completed/v13-p16-ui-dashboard-org.md` | W2 | p03 | design-ui-sentry.md s11.2 |
| `completed/v13-p06-bulk-merge-backend.md` | W3 | p03, p04, p05, p13 | v1.3 s6, s15 (run-2 carry-over) |
| `completed/v13-p12-wiring.md` | W4 | p04-p11 | v1.3 s2, s15 |
| `completed/v13-p17-verification-docs.md` | W5 | p12, p13-p16 | v1.3 s14, s16 |

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
| 18 (v1.3 wave W1) | COMPLETED | Phase 17 |
| 19 (v1.3 wave W2) | COMPLETED | Phase 18 (per-plan dependencies) |
| 20 (v1.3 wave W3) | COMPLETED | Phase 19 (per-plan dependencies) |
| 21 (v1.3 wave W4) | COMPLETED | Phase 20 |
| 22 (v1.3 wave W5) | COMPLETED | Phase 21 |
| 23 (v1.4 wave 1) | COMPLETED | Phase 22 |
| 24 (v1.4 wave 2) | COMPLETED | Phase 23 (per-plan dependencies) |
| 25 (v1.4 wave 3) | COMPLETED | Phase 24 |
| 26 (v1.4 run 2 wave A) | COMPLETED | Phase 25 |
| 27 (v1.4 wave 4) | COMPLETED | Phase 26 |

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
  17: ["v12-p12-verification-docs.md"],
  18: ["v13-p01-foundation.md", "v13-p02-db.md", "v13-p03-ui-foundation.md"],
  19: ["v13-p04-issue-stream-backend.md", "v13-p05-triage-lifecycle-backend.md",
       "v13-p08-alerts-backend.md", "v13-p09-notifications-backend.md",
       "v13-p11-directory-ownership-backend.md", "v13-p13-ui-stream.md",
       "v13-p15-ui-alerts-profile.md", "v13-p16-ui-dashboard-org.md"],
  20: ["v13-p06-bulk-merge-backend.md", "v13-p07-issue-detail-backend.md",
       "v13-p10-dashboard-integrations-backend.md", "v13-p14-ui-detail.md"],
  21: ["v13-p12-wiring.md"],
  22: ["v13-p17-verification-docs.md"],
  23: ["v14-p01-foundation.md", "v14-p02-db.md", "v14-p03-ui-foundation.md"],
  24: ["v14-p04-inventory-core.md", "v14-p05-scan-hooks.md", "v14-p06-ui-pages.md"],
  25: ["v14-p07-fields-retention.md"],
  26: ["v14-p09-live-fixes.md"],
  27: ["v14-p08-wiring-verification.md"]
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
