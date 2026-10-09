# P28 Assembly (Wiring, App, CLI, End-to-End and Leak Tests) Implementation Plan

**Status**: Ready
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
- [ ] `telescreen serve` serves the built UI per design-ui-sentry.md 8.6 (static tests pass; missing index.html exits 3)
- [ ] UI fixture leak test passes
- [ ] E2E GitHub remediation, sweep, ingestion and chat tests pass
- [ ] The leak matrix passes over every sink
- [ ] Full-repository verification passes (logs recorded)
- [ ] Reconciliation requests (if any) listed for serial repair
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
