# P20 Collectors Core (Configs, Webhooks, Scheduling, Scans, Rescans, Sweeper) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Collector adapters, Resource identity, Per-kind behavior, Full sweeps), design-docs/specs/design-map-reduce-sweeps.md (Map: collector shards, Ports)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p20-collectors-core`
- planPath: `impl-plans/active/p20-collectors-core.md`
- wave: 4
- dependsOn: `p08-masking`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p20-collectors-core.md`
  - `src/telescreen/collectors/__init__.py`
  - `src/telescreen/collectors/service.py`
  - `src/telescreen/collectors/repository.py`
  - `src/telescreen/collectors/codec.py`
  - `src/telescreen/collectors/registry.py`
  - `src/telescreen/collectors/scheduler.py`
  - `src/telescreen/collectors/rescanner.py`
  - `src/telescreen/collectors/police.py`
  - `src/telescreen/collectors/tasks.py`
  - `src/telescreen/collectors/sweep.py`
  - `src/telescreen/api/routers/collectors.py`
  - `tests/collectors/core`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Collectors are configured centrally in the Bureau. This package is source-agnostic: it
depends only on the `CollectorAdapter` and `SweepCapable` Protocols. The Slack, Google
and GitHub adapters (p21-p23) are registered by assembly (p28), and tests here use a fake
adapter. The package covers:

- config CRUD;
- the webhook entry point;
- event, schedule and manual scans via the `collector_scan` task;
- watch renewal;
- rescans for workflow `detect` steps;
- `police`-kind detect dispatch;
- the `CollectorSweeper` used by map-reduce.

It owns the `collector_configs` and `scan_jobs` tables and implements
`CollectorConfigReader` and `ScanJobStore`.

## Non-goals

- No source-specific HTTP code. Do not import `telescreen.collectors.slack`, `.google`
  or `.github`.
- No map-job tables (use `SweepLauncher`).

## Deliverables

- `codec.py`: `selector_to_json(selector: CollectorSelector) -> JsonValue`;
  `selector_from_json(kind: CollectorKind, value: JsonValue) -> CollectorSelector`
  (validation_error on a bad shape); `CursorState(cursor: str | None, watch: WatchState | None, last_sweep_fire: datetime | None)`
  with to/from JSON. This is the format of the `collector_configs.cursor_json` column.
- `repository.py`: CRUD for `CollectorConfigRow` and `ScanJobRow`;
  `to_config(row) -> CollectorConfig`.
- `registry.py`: `CollectorRegistry` with `register(adapter: CollectorAdapter, sweeper: SweepCapable | None) -> None`,
  `adapter(kind) -> CollectorAdapter` (`not_found` when unregistered) and
  `sweeper(kind) -> SweepCapable | None`. The webhook path kind `gdrive` dispatches to
  configs of kinds gdrive, gdocs and gsheets.
- `service.py`: `CollectorService(registry, queue: TaskQueue, scan_jobs: ScanJobStoreImpl, fingerprinter: Fingerprinter, redactor: Redactor, audit: AuditLog, clock)`.
  - Implements `CollectorConfigReader`.
  - `create`, `update` and `list`. Validation:
    - the name is a slug;
    - `trigger_mode` schedule or both requires a valid 5-field `cron` (croniter);
    - `sweep_cron` must be valid if set;
    - `credential_ref` must match `^(env|file):`;
    - the selector must decode for its kind;
    - `redactor.has_hits(selector_json)` -> `raw_value_detected`.
    Audit `collector.created` and `collector.updated`.
  - `enqueue_run(session, config_id, trigger)` enqueues a changes-mode `collector_scan`
    (key `collector:<config>:manual:<now-iso>`).
  - `handle_webhook(session, path_kind, event: InboundWebhook, secrets: CollectorSecrets) -> WebhookOutcome(status: int, body: str | None)`:
    1. `adapter.verify_event`: invalid -> 401 (audit nothing; log the code only);
       challenge -> 200 with the body.
    2. Candidate configs: enabled, event or both, and kind in the path's kinds. When
       `config_hint` is given, only the config whose watch `channel_id` equals it.
    3. For each config, `resources_from_event`. Enqueue `collector_scan` per changed ref
       (mode `resource`) and per removed ref (mode `removed`), with key
       `collector:<config>:<resource_key>:<event-id>`, where event-id is the sha256 hex
       of the body. When `poll_changes` is true, also enqueue one changes-mode scan
       (key `collector:<config>:changes:<event-id>`).
    4. Return 200 quickly (no scanning inline).
  - `start_watch(session, config_id)` calls `renew_watch(config, None)` and stores the
    result in the cursor state.
- `ScanJobStoreImpl` (in `service.py` or `repository.py`) implements `ScanJobStore`.
- Task payloads (JSON, no raw data): `{config_id, mode: "resource"|"removed"|"changes", ref_json?}`.
  A ref whose `ref_json` has a Redactor hit is never enqueued (log the code
  `raw_value_detected`, count it as skipped).
- `tasks.py`:
  - `CollectorScanHandler(sessions, registry, scanner: ResourceScanner, issues: IssueRecorder, reader: IssueReader, scan_jobs, fingerprinter, redactor, clock, max_pages: int = 20)`
    implements `TaskHandler` (`collector_scan`). It creates a scan job (trigger from the
    payload) and handles the modes:
    - `resource`: `fetch` then `scanner.scan_resource`. `ResourceGoneError` -> a
      complete empty scan via `issues.record_scan(ResourceScan(resource_key, findings=(), complete=True, sweep_covered=<adapter's value; default True>))`.
    - `removed`: the same complete empty scan.
    - `changes`: loop `list_changed` from the stored cursor for up to `max_pages` pages.
      Changed refs are fetched and scanned; removed refs become complete empty scans.
      Save `next_cursor` after each page (own transaction). `has_more` after
      `max_pages` -> enqueue a continuation (key includes the cursor hash).
    - Full listings: once a page has `full_listing=True`, consume pages to the end in
      the same task (no `max_pages` cap). Accumulate the listed resource keys in memory
      (HMAC hex only). After the last page, reconcile:
      `reader.open_resource_refs(config)`, and every open ref with
      `sweep_covered=True` whose resource_key was not listed gets a complete empty scan.
      Refs with `sweep_covered=False` (for example GitHub PR refs) are never reconciled.
    `RateLimited` -> `TaskResult.retry(now + retry_after)`. Other errors -> retry with
    backoff, and the scan job becomes `failed` or `partial`. Finish the scan job with the
    counts.
  - `WatchRenewHandler` (`collector_watch_renew`) calls `renew_watch(config, current)`
    and saves the result.
- `scheduler.py`: `CollectorScheduleProvider(queue, launcher: SweepLauncher, police: PoliceCollector, clock)`
  implements `ScheduleProvider.tick(session, now)`:
  1. For enabled configs with `cron` and trigger mode schedule or both: for each missed
     fire time after `last_scheduled_at` (at most 1 catch-up), enqueue a changes scan
     with key `collector:<config>:<fire-iso>` and update `last_scheduled_at`. For
     `police` kind, dispatch a police `detect` task instead.
  2. `sweep_cron`: if the previous fire time is later than `cursor_state.last_sweep_fire`,
     call `launcher.start_collector_sweep(config_id, ScanTrigger.SCHEDULE)`. Treat
     `conflict` as already running. Store `last_sweep_fire`.
  3. A watch expiring within 1 day -> enqueue `collector_watch_renew` (key
     `watch:<config>:<expires-iso>`).
- `rescanner.py`: `CollectorRescanner(sessions, registry, reader: IssueReader, configs, scanner, issues, scan_jobs, clock)`
  implements `ResourceRescanner.rescan(issue_id, detectors)`, passing `detectors` through
  to `scan_resource`:
  - a snapshot without `ref_json` -> `error` with `not_rescannable`;
  - otherwise `adapter.ref_from_json`, `fetch`, `scan_resource` with a new scan job
    (trigger `workflow`);
  - `found` if the issue id is in `outcome.issue_ids`, else `clean`;
  - `ResourceGoneError` -> complete empty scan, `clean`;
  - another error -> `error` with `fetch_failed`.
- `police.py`: `PoliceCollector(gateway: PoliceGateway, clock)` with
  `dispatch_detect(session, config) -> str`. Input
  `{selector: PoliceSelector.input, collector_config_id}`, labels from the selector,
  deadline now + 1 h, `collector_config_id` set.
- `sweep.py`: `CollectorSweeperImpl(sessions, registry, configs, scanner: ResourceScanner, issues: IssueRecorder, scan_jobs, fingerprinter, clock)`
  implements `CollectorSweeper`:
  - `partition` requires `registry.sweeper(kind)`, else `sweep_unsupported`.
  - `scan_shard` iterates `shard_resources`:
    - `fetched` -> `scan_resource(content, scan_job_id)`; add `bytes_scanned`; a
      `skipped` outcome counts as `resources_skipped`, otherwise as `resources_scanned`;
    - `removed` -> complete empty scan;
    - `failed` -> INCOMPLETE empty scan (updates `last_checked_at`) and
      `resources_failed += 1`.
    `RateLimited` propagates. Finish the scan job with the totals and return the
    `ShardSummary`.

### Router `routers/collectors.py`
`CollectorsRouterDeps(ctx: ApiContext, service: CollectorService, launcher: SweepLauncher, secrets: WebhookSecrets)`,
where `WebhookSecrets` is a frozen dataclass of `CollectorSecrets` for slack, github and
gdrive built by assembly.

- `GET /api/collectors` (read).
- `POST /api/collectors` and `PATCH /api/collectors/{id}` (admin; `selector_json` is a
  string field).
- `POST /api/collectors/{id}/run` (admin).
- `POST /api/collectors/{id}/sweep` (admin) -> `{map_job_id}`; 409 when a sweep is
  active.
- `POST /api/collectors/{kind}/webhook`: no bearer auth; the raw body is read (1 MiB
  limit), then `handle_webhook`.

## Key Pitfalls

- Never put `raw_locator`, titles or content into task payloads, logs, audit details or
  errors. Payloads carry `ref_json` only.
- Removal empty scans need `resource_key`. Compute it with
  `fingerprinter.resource_key(source, ref.raw_locator)`; source is the config kind's
  source string (equal to the kind value, for example `gdrive`).
- The webhook handler must not wait on network I/O after verification.
- Keep each file under 1000 lines (the scan handler is the largest; split helpers if
  needed).

## Tests (`tests/collectors/core/`)

Use a `FakeAdapter` implementing `CollectorAdapter` and `SweepCapable`, in-memory refs,
and fakes for `ResourceScanner`, `IssueRecorder`, `IssueReader`, `TaskQueue`,
`SweepLauncher`, `PoliceGateway` and `AuditLog`, plus the SQLite harness and `FakeClock`.

- Create a config with an invalid cron -> 422; a credential that is not a ref -> 422; a
  selector containing a sample -> `raw_value_detected`.
- Webhook: invalid signature -> 401; challenge -> body echoed (the challenge string
  only); a valid event -> one `collector_scan` per ref with idempotency keys; the same
  event again -> no duplicate tasks; `config_hint` routing picks the right config.
- The scan handler in changes mode advances the cursor across 2 pages; a two-page
  `full_listing` missing an open covered ref -> exactly one complete empty scan for that
  resource_key after the last page; an open ref with `sweep_covered=False` -> untouched;
  a `poll_changes` webhook -> one changes-mode task; `ResourceGoneError`
  -> an empty scan; `RateLimited(5)` -> retry at now + 5.
- Scheduler: a cron due -> one task, no duplicate on the next tick; `sweep_cron` due ->
  the launcher is called once and `last_sweep_fire` is stored; a launcher conflict is
  tolerated; police kind -> a detect dispatch; a watch near expiry -> a renew task.
- Rescanner: found, clean, gone and no-ref cases.
- `scan_shard` with entries fetched, too-large (fake scanner returns `skipped`), removed
  and failed -> summary counts 1 scanned, 1 skipped, 1 failed, with an incomplete scan
  recorded for the failed one.
- No payload or audit row contains a raw locator from the fake adapter (use a sample
  email in the raw locator and run `find_leaks` on every engine task and audit row text).

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/collectors/__init__.py src/telescreen/collectors/service.py src/telescreen/collectors/repository.py src/telescreen/collectors/codec.py src/telescreen/collectors/registry.py src/telescreen/collectors/scheduler.py src/telescreen/collectors/rescanner.py src/telescreen/collectors/police.py src/telescreen/collectors/tasks.py src/telescreen/collectors/sweep.py src/telescreen/api/routers/collectors.py tests/collectors/core"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/collectors/core 2>&1 | tee /tmp/telescreen-p20-collectors-core-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p20-collectors-core-typelint.log 2>&1; grep -E '^(src/telescreen/collectors/(__init__|service|repository|codec|registry|scheduler|rescanner|police|tasks|sweep)\.py|src/telescreen/api/routers/collectors.py|tests/collectors/core)' /tmp/telescreen-p20-collectors-core-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] All modules above delivered with the exact names; the ports are implemented
- [ ] Webhook, scan modes, scheduling, rescans, police dispatch and sweeper tested
- [ ] The no-raw-locator test passes
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
