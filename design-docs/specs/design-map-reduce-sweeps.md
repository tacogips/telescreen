# Map-Reduce Sweeps

Durable, concurrent map-reduce for scans that cover a whole estate (for example every file
in a Google Workspace domain's Drive, every repository of a GitHub organization, or every
table of a private database inspected by a Police). Sweeps never run sequentially: work is
partitioned into shards that many workers process concurrently, and results are reduced into
one sweep result.

Owner requirement (2026-10-08): "Police must be able to inspect, for example, all of Google
Drive; this must run concurrently as map-reduce, not sequentially." Airflow was considered
and rejected as the core engine; see `notes.md` (Map-reduce: self-built vs Airflow).

## Overview

A sweep has three phases, all persisted in PostgreSQL and driven by the existing engine
queue (`engine_tasks`, `FOR UPDATE SKIP LOCKED`) and the Police task protocol:

```text
partition (streaming, itself parallel per corpus)
   -> map_items rows (one per shard, bulk inserted per partition page)
   -> map: shards dispatched concurrently, capped by max_concurrency
        built-in collector shard -> engine task `map_shard` on any `telescreen worker`
        police shard            -> police task `scan_shard` on any matching Police worker
   -> each shard records masked findings immediately (issues appear while the sweep runs)
   -> reduce: aggregate item summaries, removal reconciliation, signal the workflow step
```

Scale-out is horizontal: add `telescreen worker` processes or Police workers; all of them
claim from the same database queue. No component holds the shard list in memory.

## Details

### Where sweeps come from

| Origin | How | Notes |
|--------|-----|-------|
| Workflow `map` step | DSL step type (below) | General form; output usable in `condition` expressions |
| Collector sweep | `collector_configs.sweep_cron` or `telescreen collector sweep <name>` / `POST /api/collectors/{id}/sweep` | Creates a map job with `origin=collector` directly (no workflow run) |
| Police sweep | A `map` step or collector config of kind `police` with `source.via: police` | Partition and shard scans run on Police workers (pull or push, including n8n) |

### DSL step type `map` (workflow-dsl)

```yaml
- id: sweep_drive
  type: map
  source:                       # exactly one of partition | items
    partition:
      via: collector            # collector | police
      collector: gdrive-domain  # collector config name (via: collector)
      # via: police -> task_type: partition, labels: [net=private], input: {...templates}
    # items: steps.list_repos.output.repos   # static fan-out over a list from a prior step
  map:
    via: collector              # collector | police | sub_workflow
    # police: {task_type: scan_shard, labels: [...]}
    # sub_workflow: {workflow: per-repo-check}   (one child run per item)
  max_concurrency: 64           # in-flight shards for this job (1-1024, default 16)
  shard_timeout: PT15M          # per shard attempt
  shard_max_attempts: 3
  failure: {max_failed_ratio: 0.01, max_failed_items: 100}   # either bound fails the job
  timeout: P1D                  # whole sweep
  outcomes: {succeeded: notify_clean, partial: review_errors, failed: alert_ops}
```

Validation: exactly one source form; `via: police` requires `task_type`; `sub_workflow`
must reference an existing definition (API check). Output (`steps.<id>.output`):
`map_job_id`, `shards_total`, `shards_succeeded`, `shards_failed`, `resources_scanned`,
`findings_total`, `issues_opened`, `issues_auto_resolved`, `findings_by_rule_class`
(list of `{rule_class, count}`), `failed_error_codes` (list of `{code, count}`). All are
counts or enums; no locators, no snippets.

### Data model (db-schema)

| Table | Columns | Notes |
|-------|---------|-------|
| `map_jobs` | id, origin (`workflow`, `collector`), step_run_id nullable, collector_config_id nullable, spec_json (compiled source/map/limits), status (`partitioning`, `running`, `reducing`, `succeeded`, `partial`, `failed`, `cancelled`), partition_done (bool), max_concurrency, paused_until nullable, deadline, result_json nullable, created_at, finished_at nullable | `paused_until` set on upstream 429/quota responses |
| `map_partitions` | id, map_job_id, corpus_key, cursor nullable, status (`ready`, `leased`, `done`, `failed`), attempt | One row per corpus (Drive user, shared drive, GitHub org page...); partition tasks run in parallel per corpus |
| `map_items` | id, map_job_id, shard_key, descriptor_json, weight, status (`ready`, `dispatched`, `succeeded`, `failed`, `cancelled`), attempt, dispatched_at nullable, finished_at nullable, summary_json nullable, error_code nullable, police_task_id nullable, child_run_id nullable, UNIQUE(map_job_id, shard_key), INDEX(map_job_id, status) | Idempotent shard creation: re-partitioning after a crash cannot duplicate shards |

Progress is computed with `SELECT status, count(*) ... GROUP BY status` on the
`(map_job_id, status)` index; there is no per-shard update of a counter row on `map_jobs`
(avoids a hot row under high concurrency).

### Phases

- **Partition.** `map_partition` engine tasks (one per `map_partitions` row) call the
  partitioner and receive a page `{shards: [ShardSpec], next_cursor, sub_corpora}`. Shards
  are bulk inserted (`ON CONFLICT (map_job_id, shard_key) DO NOTHING`); `next_cursor`
  re-enqueues the same partition; `sub_corpora` add new `map_partitions` rows (for example
  the Drive partitioner first lists users and shared drives, then pages files per corpus).
  Mapping starts as soon as the first shards exist. `partition_done` becomes true when every
  partition row is `done` or `failed` (a failed partition makes the sweep incomplete).
- **Dispatch.** The `map_dispatch` task for a job (re-enqueued every
  `TELESCREEN_MAP_DISPATCH_SECONDS`, default 2, while the job is active) counts in-flight
  items and dispatches `ready` items up to `max_concurrency - in_flight`, oldest first,
  skipping when `paused_until > now`:
  - `via: collector` -> enqueue engine task `map_shard` (idempotency key
    `map:<job>:<shard_key>:<attempt>`).
  - `via: police` -> `PoliceGateway.dispatch(task_type=scan_shard, input={sweep_id,
    shard_key, descriptor}, labels, deadline=now+shard_timeout)`.
  - `via: sub_workflow` -> start a child run with the item as input.
- **Map.** A shard scan fetches content transiently, runs the detector pipeline, and records
  masked findings through `IssueRecorder.record_scan` (collector) or `record_external`
  (police result acceptance) immediately. The item `summary_json` holds only counts:
  `resources_scanned`, `resources_failed`, `bytes_scanned`, `findings_by_rule_class`,
  `issue_ids` (internal ids, not locators). Retry: failed or timed-out shards go back to
  `ready` with `attempt + 1` until `shard_max_attempts`, then `failed`.
- **Backpressure and quotas.** Concurrency is enforced globally by the dispatcher (database
  state, not per process). Adapters map upstream `429`/quota errors to
  `RateLimited(retry_after)`; the shard returns to `ready` without consuming an attempt and
  the job gets `paused_until = now + retry_after` (exponential when no `Retry-After`).
- **Reduce.** When `partition_done` and no item is `ready` or `dispatched`, the `map_reduce`
  task aggregates item summaries with SQL, decides the status (`failed` if a failure bound
  is exceeded or a partition failed and `failure` forbids it; `partial` if any shard or
  partition failed within bounds; else `succeeded`), and, only for `succeeded` collector
  sweeps, runs removal reconciliation: open issues of the collector config whose resources
  were enumerated by the sweep and had no findings are auto-resolved through the existing
  issue lifecycle (`complete` scan semantics). It stores `result_json`, writes an audit
  record, and for workflow origins calls `RunSignaler.signal(step_run_id, map_finished)`.
- **Timeout and cancel.** Past `deadline` the job is reduced with remaining items
  `cancelled` and status `failed` (`sweep_timeout`). Run cancel or
  `POST /api/map-jobs/{id}/cancel` cancels ready items, cancels dispatched police tasks (they
  appear in the next heartbeat's `cancelled_task_ids`) and child runs.
- **Retry failed shards.** `POST /api/map-jobs/{id}/retry-failed` (admin) resets failed
  items to `ready` and reopens the job (`running`); the step is re-signalled on completion.

### Partitioner and shard executor ports (ports)

```python
class SweepCapable(Protocol):            # optional CollectorAdapter capability
    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage: ...
    async def scan_shard(self, config: CollectorConfig, shard: ShardSpec) -> ShardScan: ...
```

`ShardSpec(shard_key: str, descriptor: JsonValue, weight: int)`. Descriptors are opaque
identifiers only (file ids, page tokens, repository ids, table ids); they pass the
`Redactor` before insertion and never contain file names, titles or content. `ShardScan`
yields `ResourceScan` batches for `IssueRecorder` plus the summary counts.

Built-in sweep implementations:

| Kind | Corpora (partition level 1) | Shards (level 2) |
|------|-----------------------------|------------------|
| `gdrive` / `gdocs` / `gsheets` | Domain users (Admin SDK `users.list`, domain-wide delegation) and shared drives (`drives.list`) | `files.list` pages (`pageSize` up to 1000, mime filter per kind) split into shards of `TELESCREEN_SWEEP_SHARD_SIZE` file ids (default 100) |
| `github` | Organization repository pages | One shard per repository default branch (full tree listing) |
| `slack` | Conversation list pages | One shard per conversation (history pages scanned inside the shard) |

### Police contract additions (police-contract, police-bureau, police-sdk)

- Manifest `task_types` gains `partition` and `scan_shard`.
- `partition` input: `{sweep_id, corpus_key, cursor, selector}`; result `payload` must
  validate against `partition-result.schema.json`: `{shards: [{shard_key, descriptor,
  weight}], next_cursor, sub_corpora: [{corpus_key}]}` (max 1000 shards per result).
- `scan_shard` input: `{sweep_id, shard_key, descriptor, selector}`; result: masked
  `findings` (existing schema) plus `stats` (`resources_scanned`, `resources_failed`,
  `bytes_scanned`) and `complete_locators` for removal reconciliation.
- Both are re-scanned by the `Redactor` on acceptance like every other police result.
- SDK: `PullWorker` handlers for `partition` and `scan_shard` with a `SweepHandler` helper
  (iterate corpus, yield shard pages); the worker's `concurrency` lets one process scan many
  shards at once. The n8n README documents a push-mode `scan_shard` workflow (Webhook ->
  check -> callback) so an n8n instance can join a sweep as one more worker.

### API and UI

- `GET /api/map-jobs?origin&status`, `GET /api/map-jobs/{id}` (progress counts by status,
  partitions done/total, findings so far, throughput over the last minute, paused_until),
  `POST /api/map-jobs/{id}/cancel`, `POST /api/map-jobs/{id}/retry-failed`,
  `POST /api/collectors/{id}/sweep`.
- UI: Runs page shows a progress bar and counters for `map` steps; Collectors page shows the
  latest sweep per config with progress and a "Sweep now" action; failed shards are listed
  by opaque `shard_key` with error codes.

### Tests

- Engine: a 10,000-shard fake partitioner with `max_concurrency=32` never exceeds 32
  in-flight items; crash-and-resume (re-run partition pages) creates no duplicate shards;
  failure ratio boundaries; 429 pauses dispatch without consuming attempts; cancel; reduce
  aggregates; removal reconciliation only on `succeeded` collector sweeps.
- Police: SDK pull worker processes `partition`/`scan_shard` concurrently against the
  Bureau contract test app; descriptors containing raw values are rejected.
- Leak: descriptors, summaries, results and progress responses never contain raw sample
  secrets.
