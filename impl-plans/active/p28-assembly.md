# P28 Assembly (Wiring, App, CLI, End-to-End and Leak Tests) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/command.md, design-docs/specs/architecture.md sections 3, 4, 9, design-docs/specs/design-masking.md (Leak test matrix), design-docs/specs/design-api-and-ui.md (Serving)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p28-assembly`
- planPath: `impl-plans/active/p28-assembly.md`
- wave: 5
- dependsOn: `p07-web-shell`, `p11-police-sdk`, `p12-ui-issues`, `p13-ui-workflows`, `p14-ui-ops`, `p15-audit-search`, `p16-issues-ingest`, `p17-notifications`, `p18-evidence`, `p19-police-bureau`, `p20-collectors-core`, `p21-collectors-slack`, `p22-collectors-google`, `p23-collectors-github`, `p24-workflow-engine`, `p25-workflow-nodes`, `p26-workflow-mapreduce`, `p27-assistant`, `p29-examples-compose`
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

Read `impl-plans/conventions.md` first. This is the only wave-5 plan, so when it runs
every other plan is finished. Full-repository checks are REQUIRED to pass here, not
informational.

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
  - when `web_dist` is set, serve static files at `/` with SPA fallback to `index.html`
    for non-`/api` paths;
  - a lifespan that calls `ensure_indexes()`.
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
cd src/web && bun install --frozen-lockfile && bun run typecheck && bun run test && bun run build --outDir /tmp/telescreen-p28-assembly-web --emptyOutDir
find src tests -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"'   # prints nothing
```

## Completion Criteria

- [ ] Container, create_app and the CLI deliver every command in command.md with the documented exit codes
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
