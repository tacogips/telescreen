# P31 UI API Client Alignment and Mock API Mode Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 8.1, 8.1a, 8.3, 8.7; design-docs/specs/design-synthetic-test-dataset.md (Value rules); design-docs/specs/design-masking.md (Masking policy)
**Created**: 2026-10-09
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p31-ui-mock-api`
- planPath: `impl-plans/active/p31-ui-mock-api.md`
- wave: B1 (run B, session 341)
- dependsOn: `p07-web-shell` (accepted at 28ce81b). The page code it edits is the
  accepted run-A output of p12/p13/p14 at 28ce81b; those plans are NOT dependencies here
  because their run-B revision depends on this plan (no cycle).
- writePaths:
  - `impl-plans/active/p31-ui-mock-api.md`
  - `src/web/src/api`
  - `src/web/src/pages`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

Two problems block run B (design 8.1a, 8.3):

1. `src/web/src/api/types.ts` and `client.ts` (p07) do not match what the accepted
   routers return, so the real UI served by `telescreen serve` would break. The router
   serializers in `src/telescreen/api/routers/*.py` are authoritative.
2. The UI needs a mock API mode (`VITE_TELESCREEN_MOCK=1`) with masked fixtures so every
   page renders without a backend (development, review, screenshot smoke).

Aligning the types breaks existing page code. In this wave no page plan runs, so this
plan also makes the mechanical page edits that keep `bun run typecheck` and
`bun run test` green (explicit wave B1 -> B2 handoff; design 8.1a). From wave B2 the page
directories belong to p12/p13/p14, which do the visual rebuild.

Runs in parallel with `p30-ui-design-system`, which owns `package.json`, `bun.lock`,
`styles`, `components`, `App.tsx`, `routes.tsx`, `main.tsx`, `shell.test.tsx`.

## Non-goals

- No backend change of any kind (no endpoint, parameter or field). If the UI needs data
  the API lacks, drop it from the display.
- No visual redesign, no new components, no use of `components/ui` (it may not exist yet).
- No npm dependency changes; no edits to `src/web/src/components`, `styles`, `App.tsx`,
  `routes.tsx`, `main.tsx`, `shell.test.tsx`, `package.json`, `bun.lock`.
- Police v1, ingest and webhook endpoints are not mocked.

## Invariants

- Keep exported signatures of `ApiError` (constructor and fields; you may only ADD an
  optional `ruleClasses: readonly string[]` field and an optional constructor parameter
  after `errorId`), `apiFetch`, `getToken`, `setToken`, token key `telescreen.token`.
  p30 files import them.
- `*Api` object names stay (`issuesApi`, `workflowsApi`, `runsApi`, `humanTasksApi`,
  `evidenceApi`, `escalationApi`, `directoryApi`, `collectorsApi`, `mapJobsApi`,
  `policeApi`, `auditApi`, `searchApi`, `chatApi`, `healthApi`, `ingestApi`).
- No raw-looking value anywhere in fixtures or tests (see Fixture rules).

## Deliverables

### 1. Type and client alignment (`src/web/src/api/types.ts`, `client.ts`)

Re-read each router before typing it; match field names, nullability and wrappers
exactly. Required changes (design 8.1a table; cite the router line in a comment only when
non-obvious):

- Issues (`routers/issues.py` `_issue_json`, `_finding_json`, `_history_json`): `Issue`
  gains `grouping_key, resource_key, resource_ref_json, collector_config_id,
  last_checked_at, sweep_covered, distinct_fingerprints`; `IssueEvent` = finding fields
  incl. `scan_job_id, source, resource_key, display_locator, rule_id, rule_class,
  fingerprint, fingerprint_key_id, observed_at` (no `at`); `IssueHistory` = `{id,
  issue_id, kind, actor_ref, from_value, to_value, note, at}`; replace `IssueStats` with
  `IssueStatsBuckets = {buckets: {start: string; count: number}[]}` and
  `issuesApi.stats(params: {bucket: "day" | "hour"; days: number})` calling
  `GET /api/issues/stats`; `issuesApi.comment` returns `{accepted: boolean}`.
- Lists: add `ItemsOnly<T> = {items: T[]}`. Runs, human tasks, workflows, evidence,
  escalation, collectors clients normalize to `Page<T>` with `next_cursor: null`.
  `auditApi.search` returns `{items: AuditRecord[]; total: number}` and accepts
  `limit`/`offset`; every `AuditRecord` field except `id` nullable. `SearchHit.snippet`
  nullable.
- Workflows: `WorkflowVersionRef = {id, name, version, active}` for list, `get(name)`
  (returns `ItemsOnly`), `create`, `activate`; `WorkflowVersion` = `{id, name, version,
  source_format, source_text, compiled_json, active}` (verify against the router);
  `schema()` returns text (`apiFetch` cannot parse `application/schema+json` as typed
  JSON; fetch text via `rawFetch`); `create` 422 body `{valid, errors}` is decoded into a
  thrown `ApiError` with code `validation_error` plus `validationErrors` available to the
  caller (add an exported `WorkflowValidationError extends ApiError` with
  `errors: {path, code}[]`); `startRun` returns `{run_id}`.
- Runs: `RunSummary = {id, definition_id, issue_id, status, trigger, started_at,
  finished_at}`; `RunDetail = {run: RunSummary; steps: StepRun[]; transitions:
  Transition[]; human_tasks: RunHumanTask[]}` with the `runs.py` dataclass fields
  (`StepRun`: `id, step_id, visit, branch_token, status, attempt, input_json,
  output_json, error_code`; `Transition`: `from_step_run_id, to_step_id, kind, reason,
  at`; `RunHumanTask`: `id, step_run_id, kind, title, assignee_ref, status, outcome,
  outcomes`); `cancel` returns `{run_id, status}`. `RunStatus = "running" | "waiting" |
  "succeeded" | "failed" | "cancelled"`; `StepStatus = "ready" | "running" | "waiting" |
  "succeeded" | "failed" | "cancelled"`.
- Human tasks: `HumanTask = {id, run_id, step_run_id, kind, title, assignee_ref, due_at,
  status, outcome}`, status `"open" | "completed" | "cancelled"`; `complete` returns
  `{id, status}`.
- Evidence: add `sha256`, remove `run_id`; `evidenceApi.upload` decodes a 422 body's
  `error.rule_classes` (string array only) into `ApiError.ruleClasses`.
- Escalation: `EscalationPolicy = {id, name, tiers_json}`; `create(tiers_json,
  source_format)`, `update(name, tiers_json, source_format)`.
- Directory: `Team = {id, slug, name, email, slack_channel}`; `setTeamMembers(slug,
  handles: string[])` returns `void` (204); `Member` adds `slack_user_id`.
- Collectors: `CollectorConfig` without `sweep_max_concurrency` (verify); `create` and
  `update(id, body)` take the full `CollectorBody` (`name, kind, trigger_mode, cron,
  sweep_cron, selector_json, credential_ref, enabled` as the router requires; nullable
  members sent as `null`) and return `{id}`; `run` returns `{id}`; `sweep` returns
  `{map_job_id}`; delete the `webhook` helper.
- Map jobs: list items `MapJobSummary = {id, origin, status, collector_config_id,
  created_at}`; detail keeps `MapJob` with `counts` as a partial record (absent keys mean
  0); `cancel`/`retryFailed` return `{id, status}`.
- Police: `PoliceWorker` without `contract_version`; `PoliceStatus = "active" |
  "disabled"`; `disable` returns `{id, status}`.
- Add `rawFetch(path: string, init?: RequestInit): Promise<Response>`: sets the bearer
  header, honors the mock switch, never throws on non-2xx (callers decode). Use it for
  the evidence blob, schema text and evidence upload.

### 2. Mock API (`src/web/src/api/mock/`)

- Switch in `client.ts`: `const MOCK = import.meta.env.VITE_TELESCREEN_MOCK === "1";`
  In `apiFetch` and `rawFetch`: when `MOCK`, `const { mockFetch } = await
  import("./mock");` and use `mockFetch(path, init)` instead of `fetch`. Keep the dynamic
  import inside the `if` so Vite drops it when the flag is unset.
- `src/web/src/api/env.d.ts` (inside this plan's write path `src/web/src/api`; tsconfig
  includes all of `src`): `/// <reference types="vite/client" />` and
  `interface ImportMetaEnv { readonly VITE_TELESCREEN_MOCK?: string }`.
- `mock/index.ts`: `export async function mockFetch(path: string, init: RequestInit):
  Promise<Response>` and `export const MOCK_FIXTURE_MARKER = "telescreen-mock-fixtures-v1"`
  (referenced by the router so it is bundled only in mock builds).
- `mock/router.ts`: table of `{method, pattern: RegExp, handler}`; unknown path -> 404
  `{"error": {"code": "not_found", "detail": "Not found."}}`. Handlers return JSON in the
  exact serializer shapes from section 1. Query filters honored: issues (`status,
  source, rule_class, severity, assignee, q, sort, limit, cursor`), runs (`status,
  workflow, issue_id`), evidence (`issue_id`), map-jobs (`collector_config_id`), audit
  (`q, action, actor, target_kind, from, to, limit, offset`), search (`q, index`).
- `mock/state.ts`: in-memory state seeded from fixtures on module load; mutations
  (issue PATCH, comment, human-task complete, workflow create/activate, run start/cancel,
  collector create/update/run/sweep, police disable, escalation create/update, map-job
  cancel/retry) update it for the session only. Evidence upload never reads the body;
  it returns an accepted record with display filename `evidence-<n>.txt`. Chat returns a
  canned answer built from fixture counts (for example the number of unresolved issues).
- `mock/fixtures/*.ts` (split by area, each under 400 lines): coverage per design 8.3:
  at least one issue per status and per source, a regressed issue, events and history
  for every issue, runs in every `RunStatus` incl. a waiting human task, a `send_back`
  transition and a `map` step whose `output_json` carries `map_job_id` of a running map
  job, police workers active (pull and push, one with a stale heartbeat) and disabled, one
  collector per kind, two escalation policies, 20+ audit records, search hits for every
  index, members and teams.
- Timestamps: `new Date(LOAD_TIME - minutes * 60_000).toISOString()` from a single
  `LOAD_TIME = Date.now()` captured at module load.

Fixture rules (design-synthetic-test-dataset.md value rules + masking policy):

- Masked forms only, exactly as the Masker writes them: `ghp_****************[40]`,
  `AKIA****************[20]`, `********[24]`, `**** **** **** 1111`, `************`,
  `j***@example.com`, `***-****-**78`, `[ADDRESS REDACTED]`, `-----BEGIN PRIVATE
  KEY----- [REDACTED 1675 chars]`.
- Emails only on `example.com/.org/.net` or `*.invalid`; hosts `example.invalid`.
- Ids like `iss-01`, `run-03`; no digit run of 8 or more anywhere; no `@` except in
  masked emails; display titles and locators plain words (`#general`, `acme/api:config.py:12`).

### 3. Mechanical page handoff (`src/web/src/pages/**`)

Fix every compile error and test failure caused by section 1, nothing more:

- Rename fields (`event.at` -> `event.observed_at`; history `action`/`summary` ->
  `kind` and `from_value -> to_value`/`note`).
- Remove the per-row stats call and the Sparkline column in `IssueList.tsx` (the route
  does not exist); p12 adds the global chart later.
- Where a write endpoint now returns `{id...}`, re-fetch the record instead of using the
  response.
- `RunDetail`: read `detail.run`, `transitions` by `from_step_run_id`/`to_step_id`;
  drop due time/tier display; step type display may be omitted.
- Collectors form/actions: send the full body; show sweep id from `{map_job_id}`.
- Escalation page: edit `tiers_json` text, send `{tiers_json, source_format: "json"}`.
- Police page: status `active`/`disabled`.
- `issues/uploadEvidence.ts`: use `rawFetch`; keep its typed rejection behavior.
- Update page tests' mocked responses to the serializer shapes; keep each test's intent.

## Key Pitfalls

- Do not "keep compatibility" by mapping new wire fields back to old names; types must
  equal the wire shapes (mock mode would otherwise hide real-backend breakage).
- `import.meta.env` is constant-folded by Vite; do not read it through a helper in
  another module or the mock chunk ships in production.
- `bun.lock` changes from p30 can land while you run; if `bun run typecheck` fails with
  module-resolution errors for `lucide-solid`/`yaml`, that is p30's work in progress:
  wait and re-run. Never run `bun install` without `--frozen-lockfile`.
- p30 deletes `components/Layout.tsx` and `Sidebar.tsx`; pages must not import them.
- Never use `innerHTML`; never log tokens.

## Tests

- `src/web/src/api/client.test.ts`: issuesApi.stats calls `/api/issues/stats?bucket=day&days=14`;
  workflows create 422 `{valid:false, errors:[{path:"$.steps[0]",code:"x"}]}` -> throws
  `WorkflowValidationError` with that error list; evidence 422 with `rule_classes:
  ["credential"]` -> `ApiError.ruleClasses` equals it; collectors list `{items}` ->
  `next_cursor === null`; `rawFetch` sends the bearer token.
- `src/web/src/api/mock/mock.test.ts`: with `vi.stubEnv("VITE_TELESCREEN_MOCK", "1")`
  and a fresh module import, `issuesApi.list()` returns fixture issues without calling
  global `fetch` (assert the `fetch` spy was not called); unknown path -> `ApiError`
  404 `not_found`; PATCH issue status then GET returns the new status; every endpoint the
  pages call returns 200 (table-driven list of paths); `runsApi.get` of the waiting run
  has a `send_back` transition and a human task with outcomes.
- `src/web/src/api/mock/fixtures.test.ts`: serialize all fixtures with `JSON.stringify`
  and assert no match for each raw pattern: `AKIA[0-9A-Z]{16}`, `ghp_[A-Za-z0-9]{36}`,
  `xox[abp]-[A-Za-z0-9-]{10,}`, `AIza[0-9A-Za-z_-]{35}`, `BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{20}`,
  `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`, a Luhn-valid 13-19 digit run
  (implement the check in the test), `\d{12}`, and emails whose domain is not an example
  domain. Also assert at least one masked form of each listed kind is present.
- Existing page tests keep passing with updated mocked responses.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p31-typecheck.log; echo "exit=$?"
cd src/web && bunx vitest run src/api src/pages 2>&1 | tee /tmp/telescreen-p31-test.log; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-p31-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p31-build.log; echo "exit=$?"
! grep -rl 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web            # production build has no fixtures
cd src/web && VITE_TELESCREEN_MOCK=1 bun run build --outDir /tmp/telescreen-p31-web-mock --emptyOutDir > /tmp/telescreen-p31-build-mock.log 2>&1; echo "exit=$?"
grep -rlq 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web-mock        # mock build has them
uv run --frozen python -c "import pathlib,sys; from telescreen.masking.redact import Redactor; r=Redactor.default(); bad=[str(p) for p in pathlib.Path('src/web/src/api/mock').rglob('*.ts') if r.has_hits(p.read_text())]; print(bad); sys.exit(1 if bad else 0)"
test -z "$(find src/web/src/api src/web/src/pages -name '*.ts*' -exec wc -l {} + | awk '$1 >= 400 && $2 != "total"')"
```

Every command must exit 0 (the `!` lines must print nothing). The Redactor line is the
same check p28 adds as a permanent test; fix fixtures until it passes. If typecheck fails
only in p30 paths (`src/web/src/components`, `routes.tsx`, `App.tsx`, `shell.test.tsx`)
while p30 is running, record it and re-run after p30 finishes.

## Completion Criteria

- [x] types.ts and client.ts match every router serializer listed in section 1
- [x] Mock mode serves every page endpoint with masked fixtures; production build has no fixtures
- [x] Fixture raw-pattern test and the Redactor check pass
- [x] Pages compile and their tests pass after the mechanical handoff edits
- [x] Verification commands pass with logs recorded
- [x] Progress Log updated (include the list of page files touched and why)

## Progress Log

### Session: 2026-10-09 00:00
**Tasks Completed**: Plan created (run B, session 341)
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 16:40
**Tasks Completed**: Aligned API types and clients with router serializers; added constant-folded mock mode, page endpoint router, mutable session state, masked fixtures, raw-pattern/Redactor coverage, and mock/client tests. Updated pages and tests to consume the wire shapes and dropped unsupported per-row issue stats. Final TypeScript check, API/page tests (40 passed), production build, fixture-marker checks for production and mock builds, Redactor check, and source line limits passed. Final source SHA-256 inventory: `tmp/telescreen-v1-20261009-s341/p31-ui-mock-api/source-sha256-final.txt`.
**Page Files Touched and Why**:
- `src/web/src/pages/issues/IssueList.tsx`, `IssueList.test.tsx`: remove unsupported per-row stats and align list behavior.
- `src/web/src/pages/issues/IssueDetail.tsx`, `uploadEvidence.ts`: use serialized event/history/run fields and raw evidence upload response handling.
- `src/web/src/pages/runs/index.tsx`, `RunDetail.tsx`, `RunDetail.test.tsx`: consume run summary/detail serializers, map job references, transitions, and supported polling states.
- `src/web/src/pages/workflows/index.tsx`, `WorkflowEditor.tsx`, `WorkflowEditor.test.tsx`: consume workflow version references/details and run IDs.
- `src/web/src/pages/escalation/index.tsx`, `index.test.tsx`: edit serialized tiers JSON.
- `src/web/src/pages/collectors/index.tsx`: send full collector bodies and consume map job IDs.
- `src/web/src/pages/police/index.test.tsx`: align status fixture with the police serializer.
**Tasks In Progress**: Awaiting authorized write-path amendment and serial reconciliation for `src/web/src/env.d.ts`.
**Blockers**: Plan deliverables require `src/web/src/env.d.ts`, but `fanoutItem.writePaths` authorizes only `impl-plans/active/p31-ui-mock-api.md`, `src/web/src/api`, and `src/web/src/pages`. Resume after checkpoint/dispatch authorizes `src/web/src/env.d.ts`; serial reconciliation should then review the existing declaration and rerun any required final gates.
**Verification**:
- `cd src/web && bun run typecheck` — exit 0; complete log `/tmp/telescreen-p31-typecheck-final.log`.
- `cd src/web && bunx vitest run src/api src/pages` — exit 0; 14 files and 40 tests passed, 0 failed; complete log `/tmp/telescreen-p31-test-final.log`.
- `cd src/web && bun run build --outDir /tmp/telescreen-p31-web --emptyOutDir` — exit 0; complete log `/tmp/telescreen-p31-build-final.log`.
- `! grep -rl 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web` — exit 0; no production fixture marker; log `/tmp/telescreen-p31-production-marker-check.log`.
- `cd src/web && VITE_TELESCREEN_MOCK=1 bun run build --outDir /tmp/telescreen-p31-web-mock --emptyOutDir` — exit 0; complete log `/tmp/telescreen-p31-build-mock-final.log`.
- `grep -rlq 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web-mock` — exit 0; mock marker present; log `/tmp/telescreen-p31-mock-marker-check.log`.
- `uv run --frozen python -c "import pathlib,sys; from telescreen.masking.redact import Redactor; r=Redactor.default(); bad=[str(p) for p in pathlib.Path('src/web/src/api/mock').rglob('*.ts') if r.has_hits(p.read_text())]; print(bad); sys.exit(1 if bad else 0)"` — exit 0; no Redactor hits; log `/tmp/telescreen-p31-redactor-final.log`.
- `test -z "$(find src/web/src/api src/web/src/pages -name '*.ts*' -exec wc -l {} + | awk '$1 >= 400 && $2 != "total"')"` — exit 0; log `/tmp/telescreen-p31-line-limit.log`.
**Notes**: An intermediate fixture Redactor check identified unmasked-looking email strings; fixture values were corrected to masked forms and the final check passed. Intermediate test/typecheck attempts were superseded by the final-source passing runs above.
**Owner resolution (2026-10-09)**: The write-path blocker is resolved without a checkpoint
amendment. The declaration now lives at `src/web/src/api/env.d.ts`, which is inside this
plan's write paths (`src/web/src/env.d.ts` no longer exists). Verify typecheck, tests and
build with the moved file and continue; this is not a blocker.
### Session: 2026-10-09 17:05
**Tasks Completed**: Resolved P31-INT-01. Confirmed the Vite env declaration is at authorized path `src/web/src/api/env.d.ts`; the former `src/web/src/env.d.ts` is absent. Updated plan status to Completed after all current-tree gates passed.
**Tasks In Progress**: Independent step6 test-integrity and step7 adversarial reviews run after this implementation step.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/api/env.d.ts` SHA-256 `fa7a6bda65b022e4debcdd4dda45ef1a6000451e072d047059dde8e3fabe7e1a`; refreshed p31 source inventory: `tmp/telescreen-v1-20261009-s341/p31-ui-mock-api/source-sha256-int1.txt` (includes the moved declaration).
**Verification**:
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-p31-typecheck-int1.log`).
- `cd src/web && bunx vitest run src/api src/pages` -> exit 0, 14 files and 40 tests passed (log: `/tmp/telescreen-p31-test-int1.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-p31-web-int1 --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-p31-build-int1.log`).
- `! grep -rl 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web-int1` -> exit 0, production marker absent (log: `/tmp/telescreen-p31-production-marker-int1.log`).
- `cd src/web && VITE_TELESCREEN_MOCK=1 bun run build --outDir /tmp/telescreen-p31-web-mock-int1 --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-p31-build-mock-int1.log`).
- `grep -rlq 'telescreen-mock-fixtures-v1' /tmp/telescreen-p31-web-mock-int1` -> exit 0, marker present (log: `/tmp/telescreen-p31-mock-marker-int1.log`).
- `uv run --frozen python -c "import pathlib,sys; from telescreen.masking.redact import Redactor; r=Redactor.default(); bad=[str(p) for p in pathlib.Path('src/web/src/api/mock').rglob('*.ts') if r.has_hits(p.read_text())]; print(bad); sys.exit(1 if bad else 0)"` -> exit 0, no hits (log: `/tmp/telescreen-p31-redactor-int1.log`).
- `test "$(grep -c '^- \[ \]' impl-plans/active/p31-ui-mock-api.md)" -eq 0` -> exit 0 (log: `/tmp/telescreen-p31-plan-check-int1.log`).
- API/page TypeScript 400-line limit check -> exit 0, no violations (log: `/tmp/telescreen-p31-line-limit-int1.log`).
- `git diff --check -- impl-plans/active/p31-ui-mock-api.md src/web/src/api src/web/src/pages` -> exit 0 (log: `/tmp/telescreen-p31-diff-check-int1.log`).
- `test "$(grep -c '^- \[ \]' impl-plans/active/p31-ui-mock-api.md)" -eq 0` -> exit 0 after the plan update (log: `/tmp/telescreen-p31-plan-check-final-int1.log`).
- Confirm `src/web/src/api/env.d.ts` exists, old path is absent, and its SHA-256 matches the refreshed inventory -> exit 0 (log: `/tmp/telescreen-p31-env-path-int1.log`).
**Notes**: P31-INT-01 is resolved within the authorized write paths. No functional source changes were needed. Independent review decisions are produced by the subsequent workflow review steps.
