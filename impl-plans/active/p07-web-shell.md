# P07 Web Shell, API Client and Shared Components Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (Endpoints, UI), design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p07-web-shell`
- planPath: `impl-plans/active/p07-web-shell.md`
- wave: 2
- dependsOn: `p01-foundation`
- writePaths:
  - `impl-plans/active/p07-web-shell.md`
  - `src/web/index.html`
  - `src/web/tsconfig.json`
  - `src/web/vite.config.ts`
  - `src/web/vitest.setup.ts`
  - `src/web/src/main.tsx`
  - `src/web/src/App.tsx`
  - `src/web/src/routes.tsx`
  - `src/web/src/shell.test.tsx`
  - `src/web/src/api`
  - `src/web/src/components`
  - `src/web/src/styles`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 8 apply; the Python rules do
not).

## Intent and Context

This plan builds the Sentry-like SolidJS shell: layout, sidebar, routing, a typed API
client covering every backend endpoint, and shared components. Three later plans (p12,
p13, p14) add the pages under `src/web/src/pages/<area>/` without touching this plan's
files. `package.json`, `bun.lock` and `node_modules` are owned by p01. Use
`bun install --frozen-lockfile` only.

## Non-goals

- No page content (p12-p14). No backend code. Do not edit `package.json`.

## Deliverables

- `index.html` (mounts `#root`, loads `/src/main.tsx`); `tsconfig.json` (`strict: true`,
  `jsx: "preserve"`, `jsxImportSource: "solid-js"`, `moduleResolution: "bundler"`,
  `types: ["vite/client", "@testing-library/jest-dom"]`, `noEmit: true`);
  `vite.config.ts` (vite-plugin-solid; dev proxy `/api` -> `http://localhost:8000`;
  vitest `environment: "jsdom"`, `setupFiles: ["./vitest.setup.ts"]`, `globals: false`);
  `vitest.setup.ts` (imports `@testing-library/jest-dom/vitest`).
- `src/main.tsx` renders `<App />`. `src/App.tsx` renders `Router` with `Layout` and the
  routes.
- `src/routes.tsx`:
  - `AREAS`: a readonly array of `{ key, label, path }` in this order. Issues `/issues`,
    Search `/search`, Workflows `/workflows`, Runs `/runs`, Police `/police`,
    Collectors `/collectors`, Escalation Policies `/escalation`, Audit `/audit`,
    Assistant `/chat`, Settings `/settings`. (The design maps the old "spies" and "Big
    Brother" pages to Collectors and Assistant.)
  - Pages are discovered with
    `import.meta.glob<PageModule>("./pages/*/index.tsx")`. The page module contract is
    `export default function Page(): JSX.Element`. Each area gets routes `<path>` and
    `<path>/:id`, both rendering the area's default export, which reads
    `useParams().id` to choose list or detail view. A missing module renders
    `<Unavailable label=...>` ("This page is not available yet"), so the shell
    typechecks and runs before pages exist.
  - `/` redirects to `/issues`.
- `src/api/client.ts`:
  - `ApiError` class with fields `status`, `code`, `detail`, `errorId?`.
  - `getToken()` and `setToken()` use `sessionStorage` key `telescreen.token`.
  - `apiFetch<T>(path, init?)` adds `Authorization: Bearer`, JSON-encodes bodies, decodes
    `{error:{code,detail,error_id}}` into an `ApiError`, and returns parsed JSON.
  - A typed function per endpoint, grouped in exported objects: `issuesApi` (`list`, `get`,
    `events`, `history`, `stats`, `update`, `comment`), `workflowsApi` (`list`, `get`,
    `version`, `schema`, `create`, `validate`, `activate`, `startRun`), `runsApi` (`list`,
    `get`, `cancel`), `humanTasksApi` (`list`, `complete`), `evidenceApi` (`upload`
    (raw body, query `issue_id`, `step_run_id`, `filename`), `list`, `get`),
    `escalationApi`, `directoryApi`, `collectorsApi` (`list`, `create`, `update`, `run`,
    `sweep`), `mapJobsApi` (`list`, `get`, `cancel`, `retryFailed`), `policeApi`
    (`workers`, `disable`), `auditApi` (`search`, `verify`), `searchApi` (`search`),
    `chatApi` (`send`).
- `src/api/types.ts`: hand-written TypeScript types mirroring the API.
  - Field names equal the snake_case column names in `design-data-model.md` (for
    example `Issue { id, number, short_id, display_title, status, source, rule_class,
    severity, display_locator, times_seen, first_seen, last_seen,
    assignee_member_id, assignee_team_id, regression_count, resolved_at, resolution }`).
    Assignment is sent as `PATCH { assignee_ref: "member:<handle>" | "team:<slug>" }`.
  - Timestamps are ISO strings. Lists use `Page<T> { items: T[]; next_cursor: string | null }`.
  - Free-form JSON members are strings named `*_json` (`selector_json`, `input_json`,
    `output_json`, `context_json`).
  - Include union types for every status enum.
  - Include `MapJob` (`id`, `origin`, `status`, `collector_config_id`, `counts {ready,
    dispatched, succeeded, failed, cancelled}`, `partitions_done`, `partitions_total`,
    `findings_so_far`, `paused_until`, `failed_shards: {shard_key, error_code}[]`,
    `result_json`).
  - Pinned response shapes that the backend plans also use:
    - `SearchHit { index, id, title, snippet, status: string | null, at: string | null }`
      in `{ items: SearchHit[], total }` (p15);
    - `AuditRecord { seq, id, at, actor_kind, actor_ref, action, target_kind,
      target_id, summary }` and `AuditVerify { ok, first_broken_seq: number | null,
      checked }` (p15);
    - `ChatResponse { answer }` for the request `{ messages: {role: "user" |
      "assistant", content}[] }` (p27);
    - `ValidateResponse { valid, errors: {path, code}[] }` (p24);
    - `HumanTask { id, run_id, step_run_id, kind, title, assignee_ref, tier_index,
      due_at, status, outcome, outcomes: string[] }` (p24);
    - `PoliceWorker { id, name, version, mode, contract_version, task_types: string[],
      labels: string[], concurrency, status, last_heartbeat_at }` (p19);
    - `Evidence { id, issue_id, run_id, step_run_id, display_filename, content_type,
      size_bytes, verdict, rejection_rule_classes: string[], uploaded_by, created_at }`
      (p18);
    - `CollectorConfig { id, name, kind, trigger_mode, cron, sweep_cron,
      sweep_max_concurrency, selector_json, credential_ref, enabled }` (p20).
- `src/components/`, each with a `.test.tsx` next to it:
  - `Layout` (sidebar plus content slot);
  - `Sidebar` (renders `AREAS` with active state);
  - `StatusBadge(status)` (issue, run, step and police statuses);
  - `Sparkline(values: number[])` (inline SVG `<polyline>`; empty data renders a flat
    line);
  - `ErrorBanner(error: ApiError | null)` (shows `code` and `detail`, never request
    bodies);
  - `LoadMore(onMore, hasMore)`;
  - `JsonTextArea(value, onInput, errors: {path, code}[])`;
  - `RelativeTime(iso)`;
  - `Unavailable(label)`.
- `src/styles/app.css`: plain CSS with a Sentry-like two-column layout. No CSS framework.
- `src/shell.test.tsx`: the shell renders all ten sidebar labels in order; an injected
  page map without an area shows "not available", and an injected map with a page renders
  it; `apiFetch` with a mocked `fetch` returning a 422 error body throws an `ApiError`
  whose code is `validation_error`; `setToken` then `apiFetch` sends the bearer header.

## Key Pitfalls

- `import.meta.glob` keys are relative (`./pages/issues/index.tsx`). Derive the area key
  with a regex. Use `lazy()` from solid-js for each module loader.
- Do not import any `./pages/...` module statically. Exercise the unavailable fallback
  with an injected map so the test remains valid after downstream plans add every page.
- Never render or log raw request bodies; chat text and evidence filenames come back
  already masked from the API.
- Keep `fetch` injectable for tests (`vi.stubGlobal("fetch", ...)`).

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)   # p01 installed it; reinstall only if missing; bun.lock unchanged
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p07-web-shell-typecheck.log   # exit 0
cd src/web && bun run test 2>&1 | tee /tmp/telescreen-p07-web-shell-test.log             # exit 0
cd src/web && bun run build --outDir /tmp/telescreen-p07-web-shell-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p07-web-shell-build.log   # exit 0
git status --porcelain src/web                               # only your paths (no dist/, no lockfile change)
```

## Completion Criteria

- [x] Shell, routes (glob-based), API client covering every endpoint, types and components delivered
- [x] typecheck, vitest and build pass (logs recorded)
- [x] P07 created no page modules under `src/web/src/pages`; page modules in the
  integrated tree belong to downstream plans p12-p14.
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 20:05 JST
**Tasks Completed**: Implemented the SolidJS shell and ordered sidebar; lazy glob routes for all ten areas plus list/detail paths; typed API wire models and client helpers for health, ingest, issues, workflows, runs, human tasks, evidence, escalation, directory, collectors/webhooks/sweeps, map jobs, Police contract/workers, audit, search and chat; all nine shared components with colocated tests; app styling and shell/API tests.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: All new p07 source files had `before=absent`; after hashes are recorded in `tmp/telescreen-v1-20261008-s319/p07-web-shell/final-source-hashes.sha256`. Plan before hash was `44d711fa9671ef74ee86113aeb6e9f88d0d207d0c6c1ac893ea18223bd6ed2ae`.
**Verification**:
- `test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)` -> exit 0 (dependencies already present; no install run).
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-p07-web-shell-typecheck.log`).
- `cd src/web && bun run test` -> exit 0; 10 files, 13 tests passed (log: `/tmp/telescreen-p07-web-shell-test.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-p07-web-shell-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-p07-web-shell-build.log`).
- At that time (before p12-p14 integration), `test ! -e src/web/src/pages` -> exit 0;
  p07 created no page files.
- `git status --porcelain src/web` -> exit 0; p07 changes and p01-owned web package files are within the assigned web tree; no dist output or lockfile change.
**Notes**: Production build output was isolated under `/tmp`. Current tree hash evidence and immutable per-edit intentions are under `tmp/telescreen-v1-20261008-s319/p07-web-shell/`. Initial typecheck/test attempts found missing JSX type/router test context; corrected and verified on final source. Downstream page plans p12-p14 own page content. Formal review and workflow finalization remain downstream.

### Session: 2026-10-09 review repair
**Tasks Completed**: Addressed `WEB-SHELL-UNAVAILABLE-TEST-CONFLICT` by exporting `AreaPage` with an optional injected page map that defaults to the glob-derived production map; fallback and present-module tests now run independently of page-directory contents. Addressed `EVIDENCE-EMPTY-FILTER-LOGS` by recording owner attribution and accepted-path filter commands/results in the two requested logs.
**Tasks In Progress**: None for p07 implementation. Independent test-integrity/adversarial re-review and workflow finalization remain downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/web/src/routes.tsx` before=`bd7d349b2369b0d70450e71ebe135f0fce7b8052376d0235f833f4750ef1d234`; after=`ea6a07598a055d99c6b0dc6dcad60a0c16854bacef4b0242c1818b3db93f2755`; `src/web/src/shell.test.tsx` before=`e3f9e83bb206d5cabd64452c475956f95c565a1f1305e7e93ecadc29f6bf0f85`; after=`937e11450b58e0168b386e33d6d8e8242c3d4a55fcff049cba0258de4937903f`; plan before=`427c356586b5d2edf8c955a28f2c04a0de400fdbbf53ca8ac533108990b14385`.
**Verification**:
- Pre-fix `cd src/web && bun run test` reproduced the finding: 21 files, 34 passed / 1 failed (log: `/tmp/telescreen-p07-web-shell-review-repro.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p07-web-shell-typecheck.log` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run test 2>&1 | tee /tmp/telescreen-p07-web-shell-test.log` -> exit 0; 21 files, 36 tests passed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p07-web-shell-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p07-web-shell-build.log` -> exit 0.
- `find src/web/src/pages -type f | sort | wc -l` -> 25 downstream-owned page files present; p07 changed no page paths.
- Typelint attribution logs now report 69 diagnostics classified as p10=39, p22=30, unattributed=0; both filter logs are non-empty under `tmp/telescreen-v1-20261008-s319/serial-reconcile-wave3/`.
**Notes**: Per-edit intentions and pre-edit hashes are recorded in `tmp/telescreen-v1-20261008-s319/p07-web-shell/review-repair-20261009/`. Production route defaults and real page routing are unchanged. Review decisions are pending independent downstream re-review; no approval is claimed.
