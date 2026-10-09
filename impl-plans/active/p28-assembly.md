# P28 Assembly (Wiring, App, CLI, End-to-End and Leak Tests) Implementation Plan

**Status**: In Progress
**Design Reference**: design-docs/specs/command.md, design-docs/specs/architecture.md sections 3, 4, 9, design-docs/specs/design-masking.md (Leak test matrix), design-docs/specs/design-api-and-ui.md (Serving), design-docs/specs/design-ui-sentry.md sections 8.3 (fixture leak check) and 8.6 (serving the built UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09 (run B, session 341)

## Plan Metadata

- planId: `p28-assembly`
- planPath: `impl-plans/active/p28-assembly.md`
- wave: B2 (parallel with p12, p13, p14, which touch only `src/web/src/pages`)
- dependsOn: `p31-ui-mock-api` (fixtures scanned by `tests/leak`), plus the accepted
  backend plans at 28ce81b: `p01-foundation`, `p02-db-schema`, `p03-detection-rules`,
  `p04-ports`, `p05-police-contract`, `p06-workflow-dsl`, `p08-masking`,
  `p09-detection-llm-ml`, `p10-api-common`, `p11-police-sdk`, `p15-audit-search`,
  `p16-issues-ingest`, `p17-notifications`, `p18-evidence`, `p19-police-bureau`,
  `p20-collectors-core`, `p21-collectors-slack`, `p22-collectors-google`,
  `p23-collectors-github`, `p24-workflow-engine`, `p25-workflow-nodes`,
  `p26-workflow-mapreduce`, `p27-assistant`. `examples/workflows/*.yaml` used by the
  tests already exist on main (accepted run-A p29 output; read-only here).
- writePaths:
  - `impl-plans/active/p28-assembly.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_builders.py`
  - `src/telescreen/api/app.py`
  - `src/telescreen/cli.py`
  - `src/telescreen/__main__.py`
  - `tests/e2e`
  - `tests/leak`
  - `tests/test_package.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. Every Python-owning plan is finished when this
runs (the parallel B2 plans edit only `src/web/src/pages`), so full-repository PYTHON
checks are REQUIRED to pass here, not informational. Web checks are not part of this
plan (p29 runs them after the UI plans join).

## Intent and Context

This plan binds every port to its implementation (the only place that imports across L3
packages), builds the FastAPI app and the `telescreen` CLI, and proves the system end to
end. That includes the GitHub credential-leak remediation workflow and the
raw-value-never-leaks matrix required by the issue.

## Non-goals

- No feature logic. When a feature package has a bug, record a Reconciliation request
  naming the owning plan and file. Fix it only in the serial reconciliation step, not
  here.
- Do not edit `src/telescreen/__init__.py` (it keeps `greet`).

## Deliverables

- `wiring.py`:
  - `Container` (frozen dataclass) with every service the app, worker, CLI and MCP need.
  - `build_container(settings: TelescreenSettings, env: EnvSource, http: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession] | None = None, clock: Clock | None = None) -> Container`.
  - `async def close_container(container) -> None`.
  - Build order and bindings:
    1. Fingerprinter, Redactor, the detector (`build_detector(settings.detectors)`) and
       the DLP detector (`build_detector(settings.dlp_detectors)`).
    2. Masker, `AuditLogService`, the search backend (`MeiliSearchBackend` when
       `meili_url` is set, else `InMemorySearchBackend` with a warning) and
       `DocumentIndexer`.
    3. Directory, tokens and resolver.
    4. `SqlTaskQueue`, `NotificationService` and the delivery handler.
    5. `RoutingPoliceGateway`.
    6. `IssueService`, wired with a `DeferredIssueEventSink(target: IssueEventSink | None)`
       that is set to `WorkflowTriggerService` after the engine exists. This breaks the
       issues -> triggers -> engine -> issues cycle.
    7. `ResourceScannerImpl`, `IngestService` and the collectors (registry with Slack,
       three Google adapters and GitHub, plus their sweeps; `CollectorService`;
       `CollectorRescanner`; `CollectorSweeperImpl`).
    8. Map-reduce (`MapJobService`, launcher, handlers, sink, reducer).
    9. Police (registration, claims, `ResultService` with the map sink, push delivery,
       maintenance).
    10. Evidence (`LlmImageEvidenceScanner` when `build_managed_llm_client` returns a
        client, else None) and the `EvidenceQuery` implementation.
    11. Executors (flow, human, detect, ai_agent, notify, police_verify, map) in an
        `ExecutorRegistry`; `RunEngine`; `WorkflowService`; escalation; triggers.
    12. Assistant tools and chat.
    13. The task handler registry (all task kinds except `police_timeout`); the
        `Scheduler` with providers collectors, police maintenance and audit index; the
        `EngineWorker`.
  - `ResourceScannerImpl.detector_factory` is bound to
    `functools.partial(build_detector, settings, env, http)`, so workflow `detect` steps
    can request an explicit detector list.
  - Secrets resolved through `resolve_secret` at build time. A missing required setting
    -> `ConfigError`.
- `api/app.py`: `create_app(container: Container) -> FastAPI`:
  - `install_error_handlers`;
  - include every `build_*_router(...)` (health, directory, issues, ingest, workflows,
    runs, human_tasks, escalation, evidence, collectors, map_jobs, police, audit, search,
    chat);
  - when `settings.web_dist` is set, serve the built UI (design-ui-sentry.md 8.6),
    registered AFTER every API router:
    - files under the directory are served at `/` (Starlette `StaticFiles` or an
      equivalent route; no new dependency, do not edit `pyproject.toml`);
    - any `GET`/`HEAD` path that is not under `/api/` and matches no file returns
      `index.html` (SPA fallback) with `Cache-Control: no-cache`;
    - paths under `/api/` never fall back: an unknown `/api/...` path returns 404 with
      the common error body `{"error": {"code": "not_found", ...}}`;
    - path traversal (`/../`) never escapes the directory;
    - the directory is resolved relative to the working directory when relative;
  - when `web_dist` is unset, only the API is served;
  - a lifespan that calls `ensure_indexes()`.
- Startup check (in `cli.py` `serve`, before `uvicorn.run`, and in `create_app`): if
  `web_dist` is set but `<web_dist>/index.html` is not a file -> `ConfigError` -> exit 3
  with a masked static message (never print the path contents).
- `cli.py`: argparse subcommands exactly per `command.md`: `serve`, `worker [--once]`,
  `migrate [--revision]`, `workflow validate FILE...`, `collector run NAME [--watch]`,
  `collector sweep NAME`, `police list`, `token create --label --scopes`,
  `search reindex`, `mcp`.
  - `main(argv: Sequence[str] | None = None) -> int`. Every command first calls
    `configure_logging(settings.log_level)` and `install_excepthook`.
  - Exit codes: 0 ok; 1 runtime error (masked message); 2 usage or validation failure;
    3 `ConfigError`.
  - A bare `telescreen` prints help and exits 2.
  - `workflow validate` needs no database. It prints `path code` lines and runs
    `Redactor` on the source.
  - `token create` prints the token once to stdout.
  - `serve` uses `uvicorn.run(app, host, port, log_config=None)` so logging stays masked.
- `__main__.py`: unchanged behavior (`raise SystemExit(main())`).

## Tests

- `tests/test_package.py`:
  - keep the existing `greet` test;
  - `main([])` -> 2;
  - `main(["workflow", "validate", "examples/workflows/github-credential-leak.yaml"])`
    -> 0;
  - an invalid file -> 2 with paths printed;
  - `serve` with `TELESCREEN_MANAGED_LLM_PROVIDER=codex` and no model -> 3.
- `tests/e2e/` (SQLite harness; `httpx.MockTransport` routing Slack, GitHub, Drive,
  OpenAI and the police push endpoints; fake LLM clients; `FakeClock`;
  `EngineWorker.run_until_idle`; the app through `ASGITransport`):
  - GitHub credential leak, end to end, using `examples/workflows/github-credential-leak.yaml`:
    1. A signed push webhook adds a file with a sample AWS key.
    2. -> `collector_scan` -> issue `TELE-1` -> the trigger starts the run.
    3. -> detect found -> ai_agent (fake) -> triage human task: SLA breach escalates to
       tier 2 and a Slack notification is delivered masked.
    4. -> completion -> dev evidence upload: rejected text with a secret, then accepted
       text -> completion.
    5. -> admin human task.
    6. -> police_verify: an SDK `PullWorker` driven in-process returns failed; send-back
       to dev; next time passed.
    7. -> end succeeded. The issue history and audit chain verify ok.
  - Sweep: a GitHub collector sweep with 3 repos -> map job succeeded; an open issue on
    a deleted file is auto-resolved; with one blob 500 -> partial and no reconciliation.
  - Push ingestion: custom source scan -> issue; findings push with a raw value -> 422.
  - Chat: a fake LLM tool loop returns a count.
- `tests/e2e/test_serve_static.py` (temporary dist with `index.html` and
  `assets/app.js`, app via `ASGITransport`):
  - `GET /` and `GET /issues/iss-01` -> 200 `index.html` body, `Cache-Control: no-cache`;
  - `GET /assets/app.js` -> 200 file body;
  - `GET /api/does-not-exist` -> 404 JSON with `error.code == "not_found"`;
  - `GET /api/health` -> 200 (routers win over static);
  - `GET /../pyproject.toml`-style traversal -> not the file content;
  - `web_dist` unset -> `GET /` is 404;
  - `main(["serve"])` with `TELESCREEN_WEB_DIST` pointing to a directory without
    `index.html` -> 3 (monkeypatch `uvicorn.run` so nothing starts).
- `tests/leak/test_ui_fixtures.py`: for every file under `src/web/src/api/mock`
  (recursive, `*.ts`): `Redactor.default().has_hits(text)` is False and
  `find_leaks(text) == ()`; the directory must exist and contain at least one file
  (guards against a silent pass).
- `tests/leak/`: the full matrix from design-masking.md. The flows are push ingestion,
  collector scan, issue listing, search, a workflow notify delivery, text and image
  evidence (rejected, accepted with metadata, scanner unavailable, free-text scanner
  output), police results (clean and raw), chat, and a forced exception. After all
  flows, assert `find_leaks(...) == ()` over:
  - every text column of every table (iterate `Base.metadata.sorted_tables`);
  - captured log output (a `StringIO` handler installed through `configure_logging`);
  - all `InMemorySearchBackend` documents;
  - every captured outbound HTTP request body (Slack, police push, LLM);
  - stored evidence files;
  - every API response body;
  - the rendered traceback text of the forced exception.

## Key Pitfalls

- `wiring.py` must stay under 1000 lines. Put per-area builder functions
  (`build_collectors(...)`, `build_police(...)`, `build_workflow(...)` and so on) in
  `wiring_builders.py`, and keep `Container` and `build_container` in `wiring.py`.
- The cycle between issues and triggers is broken only through the deferred sink. Do not
  import across L3 packages anywhere except `wiring.py` and `api/app.py`.
- Never print secrets: `token create` prints the token once, and nothing else prints
  secret values.

## Verification (full repository; all must pass)

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache . 2>&1 | tee /tmp/telescreen-p28-assembly-format.log
uv run --frozen ruff check --no-cache . 2>&1 | tee /tmp/telescreen-p28-assembly-ruff.log
uv run --frozen python tools/typelint.py 2>&1 | tee /tmp/telescreen-p28-assembly-typelint.log
uv run --frozen ty check 2>&1 | tee /tmp/telescreen-p28-assembly-ty.log
uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p28-assembly-pytest.log
uv build --out-dir /tmp/telescreen-p28-assembly-dist 2>&1 | tee /tmp/telescreen-p28-assembly-build.log
find src tests -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"'   # prints nothing
git diff --quiet -- pyproject.toml uv.lock   # no dependency change
```

Record `echo "exit=$?"` after each command. Every command exits 0.

## Completion Criteria

- [ ] Container, create_app and the CLI deliver every command in command.md with the documented exit codes
- [x] `telescreen serve` serves the built UI per design-ui-sentry.md 8.6 (static tests pass; missing index.html exits 3)
- [x] UI fixture leak test passes
- [ ] E2E GitHub remediation, sweep, ingestion and chat tests pass
- [ ] The leak matrix passes over every sink
- [x] Full-repository verification passes (logs recorded)
- [x] Reconciliation requests (if any) listed for serial repair
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 18:10
**Tasks Completed**: Added shared runtime primitives and API composition for health, directory, audit, search and assistant chat; added optional built-UI static serving with safe SPA fallback, API 404 behavior, path containment and missing-index validation; implemented CLI parsing, offline workflow validation, migrate, serve, MCP, token creation, police listing and search reindex; added static serving, CLI, and UI fixture leak tests.
**Tasks In Progress**: Full composition of issue ingestion, collectors, workflow, evidence and Police services; worker and collector CLI commands; remediation/sweep/ingestion/chat E2E and full sink leak matrix.
**Blockers**: None external. Implementation remains incomplete because assigned service wiring and E2E/leak criteria are not yet delivered.
**Reconciliation requests**: None. The remaining work is within p28 writePaths.
**Hashes**: `src/telescreen/cli.py` before=48199c7f4e506f05a0f92bade49596086f5a7cc498f44492402792db385eeb05 after=a48aeae1e9dc689de745744e13493daf02a0cff50320de5fad1b6f21c1353b4d; `src/telescreen/wiring.py` before=absent after=d52b0d853617b8a8b98108ff3503560c3c52f175d62aeae114a261c7e3aa03bb; `src/telescreen/wiring_builders.py` before=absent after=1ebcffb4e8de8b03a868b4389f67fde9e101830b915df70b3241b2b466d57096; `src/telescreen/api/app.py` before=absent after=f19472e4582a51b6c7683d38be2bbef0f320ffc99c1f0179d195c809c5e71407; `tests/test_package.py` before=23c60338dccf5285b920361cc619db517c0975bcfbe0bea206816da97cbb467f after=20b7b020da7b5f79d62f43292211dc97fd26719b62859f794ede510ab64e415e; `tests/e2e/test_serve_static.py` before=absent after=3edf4db626baa2baf781834757e8f2f25945f5dee7e0b4d64f955a84b2dc5620; `tests/leak/test_ui_fixtures.py` before=absent after=2bd5e05a79396651a8129873acd8378f6c3cdfd9fd442191a43e17f77ce7f5e5; plan before=ccc7201649f418877919f3a33519c1aab13fc6e17e923508b4304558d1adb8d3 after=recorded in `tmp/telescreen-v1-20261009-s341/p28-assembly/final-source-sha256.json`.
**Verification**: Final-source full repository format -> exit 0 (log `/tmp/telescreen-p28-assembly-format-final2.log`); ruff -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-final2.log`); typelint -> exit 0 (`/tmp/telescreen-p28-assembly-typelint-final2.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-ty-final2.log`); pytest -> exit 0, 363 passed/4 offline skips (`/tmp/telescreen-p28-assembly-pytest-final2.log`); wheel/sdist build -> exit 0 (`/tmp/telescreen-p28-assembly-build-final2.log`); 1000-line gate -> exit 0, no matches (`/tmp/telescreen-p28-assembly-line-limit-final2.log`); dependency diff -> exit 0 (`/tmp/telescreen-p28-assembly-dependencies-final2.log`); `git diff --check` -> exit 0.
**Notes**: The whole-repository Python gates pass on this shared source tree. The complete planned Container/router/worker assembly, all-command CLI behavior, remediation/sweep E2E and raw-value sink matrix remain outstanding; therefore Status stays In Progress and their completion criteria stay unchecked. The separate focused static/CLI/fixture run passed 8 tests before the final shutdown-lifecycle adjustment; final-source counts are covered by the full pytest run.

### Session: 2026-10-09 continuation 2
**Tasks Completed**: Moved `workflow validate` dispatch before runtime settings loading so the documented offline validator does not require unrelated service configuration. Extended the CLI test to prove validation succeeds with an invalid Codex-provider configuration and no database settings.
**Tasks In Progress**: Complete service/router composition and command execution for worker and collector commands; GitHub remediation, sweep, ingestion and chat E2E; full raw-value sink leak matrix.
**Blockers**: None external. The remaining work is assigned to p28 and stays within its writePaths.
**Verification**: CLI behavior `uv run --frozen pytest -p no:cacheprovider tests/test_package.py` -> exit 0, 5 passed (`/tmp/telescreen-p28-assembly-cli-attempt2.log`); full `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 363 passed/4 expected offline skips (`/tmp/telescreen-p28-assembly-pytest-attempt2.log`); format, ruff, typelint, ty and `uv build --out-dir /tmp/telescreen-p28-assembly-dist-attempt2` -> exit 0 (`/tmp/telescreen-p28-assembly-{format,ruff,typelint,ty,build}-attempt2.log`); 1000-line, dependency and diff checks -> exit 0. Prior continuation-1 full-gate results are not reused as final-source verification after this edit.
**Hashes**: Continuation-2 `src/telescreen/cli.py` SHA-256 `657b4778179518163f4bb9c1368235fdf58b34637a09a6a68aa109501372c94e`; `tests/test_package.py` SHA-256 `8a01540627d6bf93e0137bc458f56e5d67f5e106d380e3e942b950a23a949ab2`; source and plan hash inventory: `tmp/telescreen-v1-20261009-s341/p28-assembly/final-source-sha256.json`.
**Review Decisions**: None for p28; test-integrity, adversarial and integration reviews remain downstream.

### Session: 2026-10-09 integration repair attempt 3
**Tasks Completed**: Expanded `Container.routers` to include all 15 required routers; assembled issue ingestion, workflow/run services, evidence, escalation, police, map-reduce, worker/scheduler handlers, and connected deferred issue events. Added `tests/e2e/test_api_routes.py`, which authenticates as admin and verifies 200 responses from issues, workflows, runs, police workers, collectors, escalation policies and map jobs. Replaced the unavailable worker and collector CLI paths with worker run-once/continuous, collector run/watch and sweep dispatch. Existing token and police commands use their assembled services.
**Tasks In Progress**: Register the built-in GitHub, Slack and Google adapters/sweepers in `CollectorRegistry`; the runtime collector run/watch and sweep commands depend on those registrations. Implement the planned GitHub remediation, collector sweep, push-ingestion and chat end-to-end flows, plus the design-masking sink matrix across database text columns, logs, search documents, outbound bodies, evidence files, API responses and forced tracebacks.
**Blockers**: None external. The remaining work is assigned to p28 and remains within its writePaths.
**Verification**: Focused static/API/CLI/UI-fixture tests -> exit 0, 9 passed (`/tmp/telescreen-p28-assembly-focused-attempt3.log`); final-source full pytest -> exit 0, 364 passed and 4 expected offline integration skips (`/tmp/telescreen-p28-assembly-pytest-attempt3-final.log`); repository format -> exit 0 (`/tmp/telescreen-p28-assembly-format-attempt3-final.log`); ruff -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-attempt3-final.log`); typelint -> exit 0, 275 files clean (`/tmp/telescreen-p28-assembly-typelint-attempt3-final.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-ty-attempt3-final.log`); package build -> exit 0 (`/tmp/telescreen-p28-assembly-build-attempt3-final.log`); Python file line-limit (`/tmp/telescreen-p28-assembly-line-limit-attempt3-final.log`), dependency (`/tmp/telescreen-p28-assembly-dependencies-attempt3-final.log`), and whitespace (`/tmp/telescreen-p28-assembly-diff-check-attempt3-final.log`) checks -> exit 0.
**Hashes**: Current `src/telescreen/wiring.py` `ed9bcad5c8bc659a874780f773e138b2e87e7a889f2d9598d36314d8829ef9fa`; `src/telescreen/cli.py` `852a9e5bac0f5cf280ef87454710a35dd484ef46e1424a739e8ce685528bfc02`; `tests/e2e/test_api_routes.py` `245283ddb6e325cd861e58dcef0d0b8efef5c197d8cf649dc2c488e642dc90f1`. Additional modified source and test hashes are in the attempt 3 evidence inventory.
**Notes**: Container wiring and the required list routes are now covered. The adapter registry and required behavior-level E2E and leak-matrix tests remain incomplete. Do not check the remaining completion criteria or mark the plan complete.

**Owner note (2026-10-09)**: Remaining scope is real work (register built-in Slack, Google
and GitHub adapters and sweepers in the container; worker and collector commands; GitHub
remediation, sweep, ingestion and chat E2E; full raw-value leak matrix). Continue it. When
reporting, list only genuinely open items in `risks`/`findings`; fixed review findings go
only in `addressedFeedback` (the gate ignores status; conventions 7a).
