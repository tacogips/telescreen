# P26 Map-Reduce Sweeps (Map Step, Partition, Dispatch, Shards, Reduce) Implementation Plan

**Status**: In Progress (run A resume, session 338)
**Design Reference**: design-docs/specs/design-map-reduce-sweeps.md (all sections), design-docs/specs/notes.md (Map step scope in v1; Sweep removal reconciliation by watermark)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p26-workflow-mapreduce`
- planPath: `impl-plans/active/p26-workflow-mapreduce.md`
- wave: 4
- dependsOn: `p06-workflow-dsl`, `p08-masking`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p26-workflow-mapreduce.md`
  - `src/telescreen/workflow/mapreduce`
  - `src/telescreen/workflow/executors/map.py`
  - `src/telescreen/api/routers/map_jobs.py`
  - `tests/workflow/mapreduce`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. Do not create
`src/telescreen/workflow/executors/__init__.py` (owned by p24).

## Intent and Context

Acceptance: "a `map` workflow step and collector/police sweeps partition work into
shards processed concurrently (global max_concurrency, never sequential), survive crashes
without duplicate shards, apply 429 backpressure, reduce into a count-only result with
removal reconciliation; tests include a 10,000-shard concurrency-cap test". This plan
owns the `map_jobs`, `map_partitions` and `map_items` tables and implements the
`SweepLauncher` and `MapResultSink` ports. It reaches collectors only through
`CollectorSweeper`, `CollectorConfigReader` and `ScanJobStore`, police only through
`PoliceGateway`, issues only through `IssueRecorder`, and the engine only through
`TaskQueue` and `RunSignaler`.

## Non-goals

- No `source.items` and no `map.via: sub_workflow` (v1 scope decision).
- No per-shard counter row on `map_jobs`: progress comes from `GROUP BY status`.

## Deliverables (`src/telescreen/workflow/mapreduce/`)

- `repository.py`: CRUD and aggregate queries for the three map tables.
  `status_counts(session, job_id) -> MapCounts`.
  `aggregate_summaries(session, job_id) -> ShardSummary` sums `summary_json`.
- `spec.py`: `MapJobSpec(partition_via, map_via, partition_task_type, map_task_type, partition_labels, map_labels, max_concurrency, shard_timeout, shard_max_attempts, max_failed_ratio, max_failed_items, timeout)`,
  stored as `spec_json`. `spec_for_collector_sweep(config) -> MapJobSpec` gives the
  defaults for collector sweeps:
  - via collector for built-in kinds, via police (`partition` and `scan_shard`, labels
    from `PoliceSelector`) for police kinds;
  - `max_concurrency = config.sweep_max_concurrency`, `shard_timeout` 15 min,
    3 attempts, failure ratio 0.01 or 100 items, timeout 1 day.
- `service.py`: `MapJobService(queue: TaskQueue, configs: CollectorConfigReader, scan_jobs: ScanJobStore, clock)`
  with `create_job(session, origin, collector_config_id, step_run_id: str | None, spec) -> str`.
  It inserts the job (`partitioning`, deadline = now + timeout), the root partition
  (`corpus_key=""`) and the first `map_partition` and `map_dispatch` tasks.
- `launcher.py`: `MapSweepLauncher(service, sessions, configs)` implements
  `SweepLauncher.start_collector_sweep`. It refuses with `TelescreenError(conflict)` when
  a non-terminal job with `origin=collector` exists for the config. It also checks that
  the kind is sweep-capable (`sweep_unsupported`); any other refusal is surfaced by the
  partitioner.
- `partition.py`: `MapPartitionHandler(sessions, sweeper: CollectorSweeper, gateway: PoliceGateway, configs, redactor, clock)`
  (`map_partition`):
  - Via collector: `sweeper.partition(config_id, PartitionRequest(corpus_key, cursor, selector))`,
    then `apply_page`.
  - Via police: dispatch a `partition` police task with input
    `{sweep_id, corpus_key, cursor, selector}` and deadline now + shard_timeout; store
    `police_task_id`; the row becomes `leased`.
  - `apply_page(session, partition_row, page)`, in one transaction:
    1. Every descriptor goes through `redactor.has_hits(dump_json(descriptor))`. Any hit
       fails the partition with `raw_value_detected`.
    2. Bulk insert items, ignoring existing `(map_job_id, shard_key)` (dialect
       `on_conflict_do_nothing`, or a savepoint per row on fallback).
    3. Insert `sub_corpora` partitions, ignoring existing `(map_job_id, corpus_key)`, and
       enqueue their `map_partition` tasks.
    4. `next_cursor` -> update the cursor and re-enqueue (key
       `mappart:<partition_id>:<attempt>:<sha256(cursor)[:16]>`); otherwise the row
       becomes `done`.
    5. When every partition is `done` or `failed`, set `partition_done` and move the job
       to `running`.
  - `RateLimited` -> back to `ready` with the attempt unchanged, and set the job's
    `paused_until`. Other errors -> attempt + 1, or `failed` after 3.
- `dispatch.py`: `MapDispatchHandler(sessions, queue, gateway, scan_jobs, clock, tick_seconds: int)`
  (`map_dispatch`), one transaction:
  1. Lock the job with `with_for_update()` (NOT `skip_locked`).
  2. Exit if the job is terminal.
  3. Time out dispatched items with `dispatched_at + shard_timeout < now`: cancel police
     tasks; the item goes back to `ready` with attempt + 1, or to `failed` after
     `shard_max_attempts` with `sweep_timeout`.
  4. Unless `paused_until > now`, dispatch up to `max_concurrency - in_flight` ready
     items, oldest first:
     - via collector: create a scan job once per item through `ScanJobStore` (trigger
       `sweep`) and enqueue `map_shard` with key `map:<job>:<shard_key>:<attempt>`;
     - via police: `gateway.dispatch(scan_shard, input {sweep_id, shard_key, descriptor, selector}, labels, deadline now + shard_timeout, collector_config_id)`
       and store `police_task_id`.
     The item becomes `dispatched` with `dispatched_at`.
  5. Past the deadline -> cancel the remaining items and reduce with `sweep_timeout`.
  6. If `partition_done` and no item is ready or dispatched -> enqueue `map_reduce` (key
     `mapreduce:<job>`). Otherwise re-enqueue the next tick with key
     `mapdisp:<job>:<tick>`, where tick = `available_at` truncated to `tick_seconds`.
- `shard.py`: `MapShardHandler(sessions, sweeper, queue, clock)` (`map_shard`):
  1. `sweeper.scan_shard(config_id, shard, scan_job_id)` (outside the job lock).
  2. In a transaction, write the result only if the item is still `dispatched` with the
     same `attempt`: `succeeded` with `summary_json`. A stale attempt is ignored.
  3. Enqueue the current dispatch tick.
  - `RateLimited` -> the item goes back to `ready` without consuming an attempt, and the
    job's `paused_until = now + retry_after` (exponential from 5 s, capped at 300 s,
    when it is None).
  - `TelescreenError` and other errors -> attempt + 1 or `failed` with the error code.
- `sink.py`: `MapResultSinkImpl(queue, clock, redactor)` implements `MapResultSink`
  (methods take `session` first). It locates the item or partition by `police_task_id`,
  ignores it unless it is still `dispatched` or `leased` with that id, applies the page,
  summary or failure with the same rules as above, and enqueues a dispatch tick.
- `reduce.py`: `MapReduceHandler(sessions, queue: TaskQueue, gateway: PoliceGateway, issues: IssueRecorder, signaler: RunSignaler, audit: AuditLog, clock)`
  (`map_reduce`), one transaction, idempotent (skip if terminal):
  - aggregate the summaries;
  - status: `failed` if a partition failed, `failed_items > max_failed_items`, or
    `failed_items / total > max_failed_ratio`; `partial` if any item failed, or
    `sum(resources_failed) > 0`; else `succeeded`;
  - only `succeeded` -> `issues.resolve_unchecked(session, collector_config_id, checked_before=job.created_at)`;
  - `result_json` holds the output fields from the design: `map_job_id`,
    `shards_total`, `shards_succeeded`, `shards_failed`, `resources_scanned`,
    `findings_total`, `issues_opened`, `issues_auto_resolved` (sum plus reconciliation),
    `findings_by_rule_class`, `failed_error_codes`, plus `resources_failed`;
  - audit `map_job.finished`; for the workflow origin,
    `signaler.signal(step_run_id, StepSignal(map_finished, outcome=status, data=result))`;
  - `cancel(session, job_id)` and `retry_failed(session, job_id)` per the design.
- `src/telescreen/workflow/executors/map.py`: `MapExecutor(service: MapJobService, configs: CollectorConfigReader)`:
  - `execute`: resolve the collector by name (missing -> `failed(not_found)`), create the
    job (origin workflow, `step_run_id`), return `waiting(MAP)`;
  - `resume(map_finished)` -> route by outcome, with output = the result object.

### Router `routers/map_jobs.py`
`MapJobsRouterDeps(ctx, service, reducer)`:

- `GET /api/map-jobs?origin&status&collector_config_id` (read);
- `GET /api/map-jobs/{id}` (read) -> counts by status, partitions done and total,
  `findings_so_far`, `paused_until`, `failed_shards` (shard_key and error code, at most
  100), `result_json`;
- `POST /api/map-jobs/{id}/cancel` and `POST /api/map-jobs/{id}/retry-failed` (admin).

## Key Pitfalls

- The cap must hold across concurrent dispatchers: count in-flight items inside the
  locked transaction. Never cache counts in memory.
- Shard work (network) never runs while holding the job lock.
- Never run concurrent sessions on the `tests.support.db` harness. It is an in-memory
  `StaticPool` database with ONE shared connection, so `asyncio.gather` over two
  sessions interleaves transactions. Run handlers sequentially in offline tests. Real
  lock contention is tested only in the PostgreSQL integration test.
- Descriptors and summaries never contain locators, names or snippets.
- Use `FakeClock` and the p24 `run_until_idle` style loop. In these tests drive the
  handlers directly with a minimal in-test loop over `EngineTaskRow`s, because p24's
  worker is a sibling plan in the same wave. Do not import p24 modules.

## Tests (`tests/workflow/mapreduce/`)

Use the SQLite harness, a fake `CollectorSweeper` (records the concurrent in-flight peak
using shared counters incremented on `scan_shard` entry and decremented on exit, with
`asyncio.Event`s to keep shards open), a fake `PoliceGateway`, a fake `IssueRecorder`
(records `resolve_unchecked` calls), a fake `RunSignaler`, and a simple in-test task
runner that claims `engine_tasks` rows by kind.

- 10,000-shard concurrency cap: a fake partitioner returns 10 pages of 1000 shards with
  `max_concurrency=32`. Assert all of the following:
  - Lower bound (proves concurrent, never sequential): after the first `map_dispatch`
    tick that runs once the first partition page has produced 1000 ready items, exactly
    32 items are `dispatched` at the same time (in-flight == min(32, ready)). A
    dispatcher that sends one item per tick must fail this assertion.
  - Two dispatchers for one tick: run two `map_dispatch` handler invocations for the
    same tick back to back (sequentially, NOT `asyncio.gather`) on the SQLite harness.
    The second one dispatches 0 new items, and its tick idempotency key
    (`mapdisp:<job>:<tick>`) enqueues no duplicate dispatch task (count `engine_tasks`
    rows of kind `map_dispatch` for that key == 1).
  - Upper bound: after every later dispatch tick, in-flight is at most 32.
  - Completion: processing all tasks makes all 10,000 items succeed and the job succeeds.
  - Runtime: under 60 s; insert in bulk.
- `@pytest.mark.integration` (only with `TELESCREEN_TEST_DATABASE_URL`): run two
  dispatchers with `asyncio.gather` on PostgreSQL sessions -> in-flight never exceeds 32
  (this exercises the real row lock).
- Crash and resume: apply the same partition page twice and re-run a `map_shard` task
  for an already-succeeded item -> item count unchanged; the stale attempt is ignored.
- Failure bounds: 1 of 200 failed with ratio 0.01 -> partial; 3 of 200 -> failed.
- 429: the fake raises `RateLimited(10)` -> the item is ready with the same attempt;
  `paused_until = now + 10`; dispatch is paused until the clock advances.
- `resources_failed > 0` within an otherwise successful run -> partial, and
  `resolve_unchecked` is not called (accepted design fix DR-1); a clean success ->
  called once with `created_at`.
- Police path: the sink receives a partition page, then shard results with
  `resources_failed = 1` -> partial; a late result for a timed-out item -> ignored.
- Shard timeout -> retried, then failed with `sweep_timeout`.
- Cancel -> ready items cancelled and police tasks cancelled.
- `retry-failed` reopens the job.
- Map step: `execute` creates a job waiting; the reduce signal carries the result; the
  outcome routes.
- Launcher: a second collector sweep while one is active -> conflict.
- Descriptor with a sample secret -> partition failed with `raw_value_detected`.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/workflow/mapreduce src/telescreen/workflow/executors/map.py src/telescreen/api/routers/map_jobs.py tests/workflow/mapreduce"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/workflow/mapreduce 2>&1 | tee /tmp/telescreen-p26-workflow-mapreduce-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p26-workflow-mapreduce-typelint.log 2>&1; grep -E '^(src/telescreen/workflow/mapreduce|src/telescreen/workflow/executors/map.py|src/telescreen/api/routers/map_jobs.py|tests/workflow/mapreduce)' /tmp/telescreen-p26-workflow-mapreduce-typelint.log   # prints nothing
```

## Run A Resume (2026-10-09, session 338)

State: interrupted at s319. Modules exist but several Deliverables rules are missing and
only 3 tests exist. Dispatch: `impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`,
wave A2. Resume from the in-tree code: keep `repository.py`, `spec.py`, `utils.py`, the
existing 10,000-shard and DR-1 tests, and the public class names. Put new tests in new
files (for example `tests/workflow/mapreduce/test_failures.py`, `test_police_path.py`) so
no file reaches 1000 lines. Static gap review (s338 planning) found:

- [ ] R0 Baseline: run the Verification block on the untouched tree; log exit codes to
  `/tmp/telescreen-p26-workflow-mapreduce-baseline-*.log` before any edit.
- [ ] R1 Queue contract. `MapPartitionHandler`, `MapDispatchHandler`, `MapShardHandler` and
  `MapReduceHandler` must satisfy `telescreen.ports.workflow:TaskHandler`: a
  `kind: EngineTaskKind` field (`MAP_PARTITION`, `MAP_DISPATCH`, `MAP_SHARD`, `MAP_REDUCE`)
  and `async def handle(self, task: ClaimedTask) -> TaskResult`, reading the row id from
  `task.ref_id`. Keep the current id-based logic as a private helper the tests can still
  call. Add one test per handler that registers it in a `TaskHandlerRegistry`-like list by
  `kind` and drives it with a `ClaimedTask`. Do not import p24 modules.
- [ ] R2 `executors/map.py:MapExecutor` needs the field `step_type: StepType = StepType.MAP`
  exactly as `src/telescreen/workflow/executors/detect.py:DetectExecutor` declares
  `step_type: StepType = StepType.DETECT` (the `StepExecutor` protocol in
  `src/telescreen/workflow/step_api.py` requires it), so `ExecutorRegistry.register`
  accepts it. Test: `execute` creates a job and returns waiting; `resume(map_finished)`
  routes by outcome with the result as output.
- [ ] R3 `partition.py`: `RateLimited` -> partition back to `ready`, attempt unchanged, job
  `paused_until` set; other errors -> attempt + 1, `failed` after 3, with the error code;
  a raw-value descriptor -> `failed` with `raw_value_detected` recorded.
- [ ] R4 `dispatch.py` step 5: past the job deadline, cancel the remaining ready and
  dispatched items (police tasks through `PoliceGateway.cancel`) and reduce with
  `sweep_timeout`.
- [ ] R5 `shard.py`: non-`RateLimited` errors -> attempt + 1 or `failed` with the error
  code (`TelescreenError.code`, else `fetch_failed`). `RateLimited` must not consume an
  attempt (dispatch already incremented it, so restore it), and with no `retry_after` the
  pause is exponential from 5 s capped at 300 s.
- [ ] R6 `sink.py`: `task_failed` for a partition counts attempts and re-enqueues a
  `map_partition` task (or fails after 3); `partition_page` moves the job to `running`
  when all partitions are done (same `apply_page` rules).
- [ ] R7 `reduce.py`: fill `failed_error_codes` (distinct codes of failed items and
  partitions, sorted) and `findings_by_rule_class` from the summed shard summaries.
  `cancel` must cancel `map_shard` tasks by item id (`cancel_by_ref` per dispatched item)
  and must not overwrite a terminal job. `retry_failed` must also reset failed partitions.
- [ ] R8 Missing tests from the Tests section, each `situation -> outcome`:
  crash/resume (same page twice; stale `map_shard` re-run) -> item count unchanged, stale
  result ignored; 1/200 failed at ratio 0.01 -> partial, 3/200 -> failed; 429 ->
  `RateLimited(10)` keeps the attempt, `paused_until = now + 10`, no dispatch until the
  clock passes it; police path through the sink with `resources_failed = 1` -> partial,
  late result for a timed-out item ignored; shard timeout -> retried then `sweep_timeout`;
  cancel; retry-failed; launcher conflict; descriptor sample -> `raw_value_detected`;
  the `@pytest.mark.integration` two-dispatcher PostgreSQL test (skips offline).
- [ ] R9 Strengthen the 10,000-shard test to the plan wording: the fake partitioner yields
  10 pages of 1000 through `MapPartitionHandler`, and the same-tick check counts real
  `engine_tasks` rows of kind `map_dispatch` for key `mapdisp:<job>:<tick>` (== 1). Keep
  the lower bound 32, upper bound 32 and under-60 s checks.
- [ ] R10 Accepted deviations to record (no code change needed): `status_counts` returns a
  tuple-based shape; `MapJobService(queue, clock)` omits `configs`/`scan_jobs` if they are
  unused; `findings_so_far` read from `result_json` (0 while running).
- [ ] R11 Re-run the full Verification block; every command exits 0; log paths recorded.

Not in scope: populating `findings_by_rule_class` in `collectors/sweep.py` (p20 owns it).

## Completion Criteria

- [ ] Job service, launcher, partition, dispatch, shard, sink, reduce, map executor and router delivered with the exact names and the TaskHandler/StepExecutor contracts (R1, R2)
- [ ] The 10,000-shard cap test (plan wording, R9) and the DR-1 reconciliation tests pass
- [ ] Run A Resume items R0-R11 checked with evidence
- [ ] Scoped verification passes on the final tree; logs recorded
- [ ] Progress Log updated (run A session)

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 07:37
**Tasks Completed**: Implemented the durable map job service, launcher, partition/page handling, capped dispatch, shard execution, Police result sink, count-only reduce, map workflow executor, and authenticated map-job router. The 10,000-item dispatch test runs each dispatched item through `MapShardHandler` with a count-only fake sweeper and asserts 32 items on the first tick, zero new items on a sequential same-tick dispatch, one idempotent queue entry, a 32-item upper bound across later batches, completion of all items, successful reduction, and runtime under 60 seconds. Added DR-1 tests for partial scans skipping reconciliation and clean success reconciling once. Normalized SQLite-naive timestamps before timeout comparisons and used savepoint inserts for dialect-independent unique-key handling.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: Before snapshot: p26 source files were absent; plan before=d868ebb2c849b13920d94dca42d144f1ee9c7b3fb9faf2a3f7c5c6fcdb977c64. Source inventory for the final MapShardHandler test: `tmp/telescreen-v1-20261008-s319/p26-workflow-mapreduce/final-source-hashes-actual-shards.sha256`.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/workflow/mapreduce src/telescreen/workflow/executors/map.py src/telescreen/api/routers/map_jobs.py tests/workflow/mapreduce` -> exit 0 (log: `/tmp/telescreen-p26-workflow-mapreduce-format-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/workflow/mapreduce src/telescreen/workflow/executors/map.py src/telescreen/api/routers/map_jobs.py tests/workflow/mapreduce` -> exit 0 (log: `/tmp/telescreen-p26-workflow-mapreduce-ruff-final.log`).
- `uv run --frozen ty check src/telescreen/workflow/mapreduce src/telescreen/workflow/executors/map.py src/telescreen/api/routers/map_jobs.py tests/workflow/mapreduce` -> exit 0 (log: `/tmp/telescreen-p26-workflow-mapreduce-ty-final.log`).
- `uv run --frozen python tools/typelint.py src/telescreen/workflow/mapreduce src/telescreen/workflow/executors/map.py src/telescreen/api/routers/map_jobs.py tests/workflow/mapreduce` -> exit 0, 14 files clean (log: `/tmp/telescreen-p26-workflow-mapreduce-typelint-scoped-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/workflow/mapreduce` -> exit 0, 3 passed; includes actual `MapShardHandler` execution for all 10,000 items (log: `/tmp/telescreen-p26-workflow-mapreduce-pytest-final.log`).
- `uv run --frozen python tools/typelint.py` -> final combined tree exit 0, 248 files clean (log: `/tmp/telescreen-p26-workflow-mapreduce-typelint-full-final.log`). An earlier combined-tree attempt exited 1 with 92 diagnostics in other plan-owned files; its log was overwritten by the final rerun, so only the final source-matched log remains.
**Notes**: The SQLite 10,000-item scenario completes through dispatch, shard execution, and reduction while exercising the exact concurrency/idempotency assertions. The PostgreSQL concurrent-session integration variant remains conditional on `TELESCREEN_TEST_DATABASE_URL` and was not run. Formal test-integrity, adversarial, branch-evidence, and final serial integration reviews remain downstream workflow steps.
