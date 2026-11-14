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
| Police sweep | A `map` step with `via: police`, or a collector sweep of a `police` kind config (always `via: police`) | Partition and shard scans run on Police workers (pull or push, including n8n). Task `selector` is the config's `PoliceSelector.input`; labels default to `PoliceSelector.labels` |

### DSL step type `map` (workflow-dsl)

```yaml
- id: sweep_drive
  type: map
  source:
    partition:
      via: collector            # collector | police
      collector: gdrive-domain  # collector config name (required for both via values)
      # via: police -> task_type: partition, labels: [net=private] (optional override)
  map:
    via: collector              # collector | police (must equal source.partition.via)
    # police: {task_type: scan_shard, labels: [...]}
  max_concurrency: 64           # in-flight shards for this job (1-1024, default 16)
  shard_timeout: PT15M          # per shard attempt
  shard_max_attempts: 3
  failure: {max_failed_ratio: 0.01, max_failed_items: 100}   # either bound fails the job
  timeout: P1D                  # whole sweep
  outcomes: {succeeded: notify_clean, partial: review_errors, failed: alert_ops}
```

Validation: `source.partition` is required; `collector` names a collector config (existence
checked by the API, not by the offline CLI; `via: police` requires kind `police`, and
`via: collector` requires a `SweepCapable` built-in kind); `map.via` equals
`source.partition.via`; `via: police` requires `task_type` on both sides (`partition` and
`scan_shard`). Static `items` fan-out and `map.via: sub_workflow` are not part of v1 (see
`notes.md`, "Map step scope in v1"). Output (`steps.<id>.output`):
`map_job_id`, `shards_total`, `shards_succeeded`, `shards_failed`, `resources_scanned`,
`findings_total`, `issues_opened`, `issues_auto_resolved`, `findings_by_rule_class`
(list of `{rule_class, count}`), `failed_error_codes` (list of `{code, count}`). All are
counts or enums; no locators, no snippets.

### Data model (db-schema)

| Table | Columns | Notes |
|-------|---------|-------|
| `map_jobs` | id, origin (`workflow`, `collector`), step_run_id nullable, collector_config_id (not null: every v1 sweep partitions a collector config), spec_json (compiled source/map/limits), status (`partitioning`, `running`, `reducing`, `succeeded`, `partial`, `failed`, `cancelled`), partition_done (bool), max_concurrency, paused_until nullable, deadline, result_json nullable, created_at, finished_at nullable | `paused_until` set on upstream 429/quota responses; `created_at` is the reconciliation watermark |
| `map_partitions` | id, map_job_id, corpus_key, cursor nullable, status (`ready`, `leased`, `done`, `failed`), attempt, police_task_id nullable, UNIQUE(map_job_id, corpus_key) | One row per corpus (Drive user, shared drive, GitHub org page...); partition tasks run in parallel per corpus |
| `map_items` | id, map_job_id, shard_key, descriptor_json, weight, status (`ready`, `dispatched`, `succeeded`, `failed`, `cancelled`), attempt, dispatched_at nullable, finished_at nullable, summary_json nullable, error_code nullable, police_task_id nullable, scan_job_id nullable, UNIQUE(map_job_id, shard_key), INDEX(map_job_id, status) | Idempotent shard creation: re-partitioning after a crash cannot duplicate shards |

Progress is computed with `SELECT status, count(*) ... GROUP BY status` on the
`(map_job_id, status)` index; there is no per-shard update of a counter row on `map_jobs`
(avoids a hot row under high concurrency).

### Phases

Engine task kinds added by workflow-mapreduce (registered as `TaskHandler`s through the
`TaskQueue` port): `map_partition`, `map_dispatch`, `map_shard`, `map_reduce`.

- **Partition.** One `map_partition` engine task per `map_partitions` row
  (idempotency key `mappart:<partition_id>:<attempt>:<cursor-hash>`).
  - `via: collector`: the handler calls `CollectorSweeper.partition` (port, implemented by
    collectors-core over the adapter's `SweepCapable`) and receives a `PartitionPage`
    `{shards: [ShardSpec], next_cursor, sub_corpora}`.
  - `via: police`: the handler dispatches a police `partition` task, stores its id in
    `map_partitions.police_task_id` and leaves the row `leased`. police-bureau delivers the
    accepted page through `MapResultSink.partition_page` (port, implemented here).
  - Applying a page is one transaction: shards are bulk inserted
    (`ON CONFLICT (map_job_id, shard_key) DO NOTHING`), `sub_corpora` are inserted
    (`ON CONFLICT (map_job_id, corpus_key) DO NOTHING`), and the cursor is advanced.
    `next_cursor` re-enqueues the same partition; otherwise the row becomes `done`.
  - The Drive partitioner first lists users and shared drives, then pages files per corpus.
  - Mapping starts as soon as the first shards exist. `partition_done` becomes true when
    every partition row is `done` or `failed`. A failed partition makes the sweep
    incomplete.
- **Dispatch.** `map_dispatch` tasks use the idempotency key `mapdisp:<job>:<tick>`, where
  tick is the task's `available_at` truncated to `TELESCREEN_MAP_DISPATCH_SECONDS`
  (default 2). The task is re-enqueued for the next tick while the job is active. A shard
  completion also enqueues the current tick, which is deduplicated by the key. Extra
  wake-ups are harmless because the row lock below serializes them.
  - The handler first locks the job row (`SELECT ... FOR UPDATE`, not `SKIP LOCKED`). Two
    dispatchers on different workers therefore serialize, and the in-flight count cannot be
    exceeded by a race. SQLite is single-writer.
  - It then times out `dispatched` items whose `dispatched_at + shard_timeout < now`:
    police tasks are cancelled through `PoliceGateway.cancel`, and the item goes back to
    `ready` with `attempt + 1`, or to `failed` after `shard_max_attempts`.
  - Unless `paused_until > now`, it dispatches `ready` items up to
    `max_concurrency - in_flight`, oldest first. The item is set to `dispatched` in the same
    transaction:
    - `via: collector`: a `scan_jobs` row is created once per item (kept in
      `map_items.scan_job_id` across attempts), and an engine task `map_shard` is enqueued
      (idempotency key `map:<job>:<shard_key>:<attempt>`).
    - `via: police`: `PoliceGateway.dispatch(task_type=scan_shard, input={sweep_id,
      shard_key, descriptor, selector}, labels, deadline=now+shard_timeout)`; the task id
      goes in `map_items.police_task_id`.
- **Map.**
  - Collector shards: the `map_shard` handler calls `CollectorSweeper.scan_shard(config_id,
    shard, scan_job_id)`. collectors-core walks the adapter's `shard_resources` entries and
    commits per resource, so issues appear while the sweep runs:
    - `fetched`: the transient content runs through the scan pipeline (`ResourceScanner`),
      which also covers size-skipped resources.
    - `removed`: the upstream reports the resource gone (for example a 404, or a file now
      trashed). It is recorded as a complete scan with no segments, so its open issues
      auto-resolve.
    - `failed`: any other fetch, export or permission error. It is recorded as an
      incomplete scan with no segments, which updates `last_checked_at` and changes no
      status, and it is counted in `resources_failed`.
  - A shard re-executed after a crash reuses the item's `scan_job_id`, so
    `UNIQUE(issue_id, scan_job_id, fingerprint)` prevents duplicate findings and
    `times_seen` is not inflated. When the handler finishes, it writes the summary only if
    the item is still `dispatched` with the same `attempt`. A stale attempt that completes
    after a timeout is dropped; its findings are already deduplicated by the shared
    `scan_job_id`.
  - Police shards: police-bureau accepts the `scan_shard` result. It records findings via
    `IssueRecorder.record_external` (each locator in `complete_locators` becomes a complete
    resource scan) and then calls `MapResultSink.shard_finished`.
  - Results for an item that is no longer `dispatched` with that `police_task_id` are
    ignored; police-bureau already rejects results for cancelled tasks.
  - The item `summary_json` (`ShardSummary`) holds only counts: `resources_scanned`,
    `resources_skipped`, `resources_failed`, `bytes_scanned`, `findings_inserted`,
    `issues_opened`, `issues_auto_resolved`, `findings_by_rule_class`, summed from the
    `RecordOutcome` that `IssueRecorder` returns per resource. There are no ids, locators or
    snippets. The step output `issues_auto_resolved` is the sum of the shard summaries plus
    the count from reconciliation.
  - Retry: a failed shard (`ShardFailure(error_code)`) goes back to `ready` with
    `attempt + 1` until `shard_max_attempts`, then becomes `failed`.
- **Backpressure and quotas.** Concurrency is enforced globally per job by the locked
  dispatcher, using database state rather than per-process state. Adapters map upstream
  `429` and quota errors to `RateLimited(retry_after)`, raised out of `partition` or
  `scan_shard`; police report error code `rate_limited` with an optional `retry_after_seconds`.
  The shard or partition goes back to `ready` without consuming an attempt. The job gets
  `paused_until = now + retry_after`; without `Retry-After` the pause is exponential from
  5 s, capped at 300 s.
- **Reduce.** The `map_reduce` task runs when `partition_done` is true and no item is
  `ready` or `dispatched`.
  - It aggregates item summaries with SQL.
  - It decides the status:
    - `failed` if a failure bound is exceeded or a partition failed;
    - `partial` if any shard failed within bounds, or if `sum(resources_failed) > 0`
      across all items (the result carries `resources_failed`);
    - otherwise `succeeded`.

    Therefore `succeeded` always means that every enumerated resource was scanned or
    reported removed, and that reconciliation ran.
  - Only for `succeeded` jobs (which implies `sum(resources_failed) = 0`), it runs removal
    reconciliation. A `partial` or `failed` job never calls `resolve_unchecked`. Every scan of a resource,
    whether complete, incomplete or skipped (for example `too_large`), sets
    `issues.last_checked_at` on that resource's issues (see the data model). Removal
    reconciliation calls `IssueRecorder.resolve_unchecked(collector_config_id,
    checked_before=map_jobs.created_at)`. That call auto-resolves (`resolution=auto`,
    history `auto_resolved`) every open issue of the config with `sweep_covered = true` that
    no shard of this sweep touched, which means its resource no longer exists in the swept
    estate. Issues on resources a sweep never enumerates, such as GitHub PR refs, are left
    alone. It returns the
    count, which becomes `issues_auto_resolved`. Clean resources that still exist were
    already auto-resolved by their complete scans during the map phase.
  - It stores `result_json`, writes an audit record, and for workflow origins calls
    `RunSignaler.signal(step_run_id, map_finished)`.
- **Timeout and cancel.** Past `deadline` the job is reduced with remaining items
  `cancelled` and status `failed` (`sweep_timeout`). Run cancel or
  `POST /api/map-jobs/{id}/cancel` cancels ready items and cancels dispatched police tasks
  (they appear in the next heartbeat's `cancelled_task_ids`).
- **Retry failed shards.** `POST /api/map-jobs/{id}/retry-failed` (admin) resets failed
  items to `ready` and reopens the job (`running`); the step is re-signalled on completion.

### Partitioner and shard executor ports (ports, `telescreen/ports/sweeps.py`)

```python
class SweepCapable(Protocol):            # optional CollectorAdapter capability
    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage: ...
    def shard_resources(self, config: CollectorConfig, shard: ShardSpec) -> AsyncIterator[ShardEntry]: ...

class CollectorSweeper(Protocol):        # collectors-core; consumed by workflow-mapreduce
    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage: ...
    async def scan_shard(self, collector_config_id: str, shard: ShardSpec, scan_job_id: str) -> ShardSummary: ...

class SweepLauncher(Protocol):           # workflow-mapreduce; consumed by collectors-core
    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str: ...

class MapResultSink(Protocol):           # workflow-mapreduce; consumed by police-bureau
    async def partition_page(self, police_task_id: str, page: PartitionPage) -> None: ...
    async def shard_finished(self, police_task_id: str, summary: ShardSummary) -> None: ...
    async def task_failed(self, police_task_id: str, failure: ShardFailure) -> None: ...
```

DTOs (frozen dataclasses in the same module): `PartitionRequest(corpus_key, cursor)`,
`PartitionPage(shards, next_cursor, sub_corpora)`, `ShardSpec(shard_key: str, descriptor:
JsonValue, weight: int)`, `ShardSummary` (count fields above), `ShardFailure(error_code,
retry_after_seconds: int | None)`, `ShardEntry(kind: fetched|removed|failed, raw_locator,
content: ResourceContent | None, error_code: str | None)`, and the exception
`RateLimited(retry_after_seconds)`. `RateLimited` aborts the whole shard, which goes back
to `ready`. `SweepCapable.shard_resources` yields one `ShardEntry` per resource the shard
enumerates, and only `fetched` entries carry content. collectors-core scans each resource
and releases it before pulling the next one, so a shard never holds more than one
resource's content in memory. `shard_key` is a deterministic function of the corpus key
and the shard's identifiers. For Drive it is the hex sha256 of `corpus_key` and the
sorted file ids of the chunk. Re-applying the same partition page after a crash therefore
yields the same keys. Descriptors are opaque
identifiers only (file ids, page tokens, repository ids, table ids). They pass the
`Redactor` before insertion and never contain file names, titles or content. A descriptor
with a hit fails its partition page with `raw_value_detected`. All three port
implementations run inside the caller's task transaction rules: no external side effect
other than read-only fetches.

Built-in sweep implementations:

| Kind | Corpora (partition level 1) | Shards (level 2) |
|------|-----------------------------|------------------|
| `gdrive` / `gdocs` / `gsheets` | With `TELESCREEN_GOOGLE_DELEGATED_ADMIN` set: domain users (Admin SDK `users.list`, domain-wide delegation impersonating that admin) and shared drives (`drives.list`). Without it: only the selector's `drive_ids`/`folder_ids` visible to the service account | `files.list` pages (`pageSize` up to 1000, mime filter per kind) split into shards of `TELESCREEN_SWEEP_SHARD_SIZE` file ids (default 100). Single enumeration: user corpora list only files that user owns in My Drive (`q` includes `'me' in owners`, `corpora=user`). Shared-drive corpora use `corpora=drive`, `driveId=<id>`, `includeItemsFromAllDrives=true`, `supportsAllDrives=true` (shared-drive files have no user owner). Each file is therefore enumerated by exactly one corpus per sweep, and files merely shared with a user are never listed again. Non-delegated mode applies the same rule: a file reachable from several selected folders or drives is assigned to the first matching entry in selector order (drives before folders), and the other corpora skip it |
| `github` | Organization repository pages (or the selector's `repos`) | One shard per repository and selected branch (`GitHubSelector.branches`, default branch when empty), full tree listing |
| `slack` | Conversation list pages | One shard per conversation (history pages scanned inside the shard) |

### Police contract additions (police-contract, police-bureau, police-sdk)

- Manifest `task_types` gains `partition` and `scan_shard`.
- `partition` input: `{sweep_id, corpus_key, cursor, selector}`; result `payload` must
  validate against `partition-result.schema.json`: `{shards: [{shard_key, descriptor,
  weight}], next_cursor, sub_corpora: [{corpus_key}]}` (max 1000 shards per result).
- `scan_shard` input: `{sweep_id, shard_key, descriptor, selector}`; result: masked
  `findings` (existing schema) plus `stats` (`resources_scanned`, `resources_failed`,
  `bytes_scanned`) and `complete_locators` for removal reconciliation.
  - Every resource the Police enumerated in the shard must appear in exactly one place:
    in `complete_locators` (fully scanned, with or without findings) or in
    `stats.resources_failed` (enumerated but not fully scanned, for any reason).
  - A resource absent from both `complete_locators` and `findings` while
    `resources_failed = 0` is treated as removed.
  - Any `resources_failed > 0` makes the sweep `partial`, so it does not reconcile.
- Both are re-scanned by the `Redactor` on acceptance like every other police result.
- police-bureau routes accepted `partition`/`scan_shard` results, failed results and
  expired tasks to `MapResultSink` (it never writes map tables). A failed result with code
  `rate_limited` carries `retry_after_seconds` in `error` (optional contract field).
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

- Engine (workflow-mapreduce, SQLite repositories, `FakeClock`, fake `CollectorSweeper`
  and `MapResultSink` callers):
  - A 10,000-shard fake partitioner with `max_concurrency=32` never exceeds 32 in-flight
    items. The fake sweeper records the peak, and two dispatch tasks are run for one tick
    to cover the lock.
  - Crash-and-resume (re-applying partition pages, re-executing a `map_shard` task)
    creates no duplicate shards and no duplicate findings.
  - Failure ratio boundaries.
  - 429 pauses dispatch without consuming attempts.
  - Shard timeout, cancel, and reduce aggregates.
  - `resolve_unchecked` is called only for `succeeded` jobs, with `map_jobs.created_at`.
  - Collector-path regression: one `failed` entry in an otherwise successful shard makes
    the job `partial` with `resources_failed = 1`. `resolve_unchecked` is not called, and the
    open issue on that resource stays open with `last_checked_at` updated.
  - Police-path regression: a `scan_shard` result with `stats.resources_failed = 1` makes
    the job `partial`, and `resolve_unchecked` is not called.
  - Stale attempt: a `map_shard` attempt that completes after its timeout does not
    overwrite the newer attempt's summary.
- Collectors (collectors-google, `httpx.MockTransport`):
  - With delegation, a file owned by user A, shared with users B and C, yields exactly one
    shard entry per sweep, and `times_seen` grows by 1.
  - A file in a shared drive is listed only by that drive's corpus.
  - A 404 entry is reported `removed`; a 500 or 403 entry is reported `failed`.
- Issues (issues-ingest): `resolve_unchecked` resolves only open, `sweep_covered` issues of
  the config with `last_checked_at` older than the watermark; skipped resources and GitHub
  PR-ref issues keep their issues open.
- Police: SDK pull worker processes `partition`/`scan_shard` concurrently against the
  Bureau contract test app; descriptors containing raw values are rejected.
- Leak: descriptors, summaries, results and progress responses never contain raw sample
  secrets.
