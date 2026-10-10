# V11-P08 Collectors Core: Coverage Recording, Collector Health, Inconclusive Rescans

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 5 (`detect` / rescanner, `removed`), 6 (F4 coverage), 8 (F6 collector health)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p08-collectors-core`
- planPath: `impl-plans/active/v11-p08-collectors-core.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`
- writePaths:
  - `impl-plans/active/v11-p08-collectors-core.md`
  - `src/telescreen/collectors/tasks.py`
  - `src/telescreen/collectors/sweep.py`
  - `src/telescreen/collectors/service.py`
  - `src/telescreen/collectors/rescanner.py`
  - `src/telescreen/collectors/coverage.py` (new)
  - `src/telescreen/collectors/health.py` (new)
  - `tests/collectors/core/test_v11_coverage_health.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Collectors core runs every scan job (`collectors/tasks.py:CollectorScanHandler`, sweeps in
`collectors/sweep.py:CollectorSweeperImpl.scan_shard`) and the rescanner used by the
`detect` node and by `verify_rescan` (`collectors/rescanner.py:CollectorRescanner`).

This plan:

- records per-job coverage counts and the per-resource unscannable list;
- keeps the collector health record and raises health alerts;
- makes the rescanner report `inconclusive` for incomplete content and `removed` for gone
  resources.

Adapters (slack, google, github) set `ResourceContent.coverage`, `exposure` and the other
fields in their own plans (v11-p09..p11). This plan only consumes `ResourceContent.coverage`
and `exposure`, and must work when they are None (v1 adapters).

## Non-goals

- No adapter changes (`collectors/slack|google|github/**`), no router, no wiring.
- No `scan_coverage` for Police shards (design section 6).
- `collectors/scheduler.py` and `collectors/repository.py` are unchanged. Put new
  queries in `coverage.py` and `health.py`.

## Invariants

- Coverage rows hold only `resource_key` (keyed), source, state, reason, counts,
  visibility and timestamps. Never a locator, title or file name.
- A coverage state other than `scanned` always means the scan was recorded with
  `complete=False` (the issues plan enforces this in `scan.py`; do not pass
  `complete=True` yourself).
- v1 behavior of scan jobs (statuses, counts, cursor handling, reconciliation) is
  unchanged.
- New dataclass fields on `CollectorScanHandler`, `CollectorSweeperImpl`,
  `CollectorService` and `CollectorRescanner` are appended with defaults (for example
  `coverage: CoverageRecorder | None = None`, `health: CollectorHealthRecorder | None =
  None`), so `wiring.py` / `wiring_collectors.py` compile unchanged until v11-p19.

## Deliverables

1. **`coverage.py`**:
   - `coverage_state(content: ResourceContent, outcome: RecordOutcome) -> Coverage`:
     - `outcome.skipped` -> `too_large`;
     - else `content.coverage` if set;
     - else `scanned` when `content.complete`;
     - else `partial`.
   - `CoverageRecorder` (no stored session; methods take `session`):
     - `record(session, config_id, scan_job_id, source, resource_key, coverage,
       visibility, now)`: upserts the `scan_coverage` count `(scan_job_id, state, reason
       or "")`. For a non-`scanned` state it upserts the `resource_coverage` row
       (`first_seen_at` kept, `last_attempt_at` = now); for `scanned` it deletes the row.
     - `remove(session, config_id, resource_key)` for removed resources.
     - `summary(session, config_id, since) -> CoverageSummary(counts: tuple[(state,
       reason, count)], unscannable_total)`.
     - `resources(session, config_id, state, limit, cursor) -> (tuple[CoverageResourceView,
       ...], next_cursor)`: keyset on `(last_attempt_at desc, id)`; rows with visibility
       `public` or `anyone_with_link` sort first.
     - `public_unscannable_count(session, config_id) -> int`.
2. **Integration in `tasks.py` and `sweep.py`**:
   - After each `scanner.scan_resource(content, job_id)`, call
     `coverage.record(...)` with the `resource_key` already computed there
     (`fingerprinter.resource_key`) and `content.exposure.visibility` (or `unknown`).
   - Gone or removed refs (`_record_empty` callers with `ResourceGoneError` or
     `page.removed`, and the full-listing reconciliation) set `removed=True` on the
     `ResourceScan` they build and call `coverage.remove`.
   - Sweep FAILED entries (`ShardEntryKind.FAILED`) record `fetch_failed`, or
     `permission_denied` when `error_code is ErrorCode.FORBIDDEN`.
   - In `CollectorSweeperImpl.scan_shard` the coverage scan job id is the shard's scan
     job if one exists; read the method. If no scan job id exists, skip `scan_coverage`
     but still upsert `resource_coverage`.
3. **`health.py`**:
   - `CollectorHealthRecorder` with `job_finished(session, config_id, status, now)`:
     `succeeded` sets `last_success_at` and `consecutive_failures=0`; `failed` sets
     `last_failure_at` and +1; `partial` changes nothing. Also `event_received(session,
     config_id, now)` and `watch_started(session, config_id, expires_at, now)`.
   - `health_view(row, config, public_unscannable, now) -> CollectorHealthView(status,
     conditions, last_success_at, last_failure_at, consecutive_failures, last_event_at,
     watch_expires_at, event_only)`. It evaluates the conditions of design section 8:
     - `stale_success`, only for configs with `cron`; sweep-only configs are never
       stale. The interval comes from croniter (next fire minus previous fire). Skip
       configs younger than 2 intervals, using the `collector_configs.created_at` row
       value.
     - `event_only` = trigger_mode `event` and no `cron` and no `sweep_cron`.
   - `list_health(session, configs, now)` for the router.
   - `CollectorHealthProvider` (ScheduleProvider; fields include
     `notifier: NotificationSender | None = None`, `recipients: tuple[str, ...] = ()`,
     `failure_threshold: int = 3`, `alert_unscannable_public: bool = True`):
     - `tick` runs at most once per 60 s per process and evaluates every enabled config.
     - For each newly opened condition (not in `open_conditions`), it sends one
       notification per channel to the recipients when both are set. Subject:
       `"Telescreen health: <config name> <condition>"`. Body: kind, condition,
       timestamps, counts.
     - It sets `open_conditions` and `alerted_at` and audits `health.alert_opened`.
     - Cleared conditions are removed silently.
4. **Calls into the health recorder**:
   - `tasks.py._finish` (incremental and run-now scan jobs) calls `job_finished`. Sweep
     results are not recorded: the reducer lives in `workflow/mapreduce` and is out of
     scope.
   - `service.py.handle_webhook` on accepted events -> `event_received`.
   - `service.py.start_watch` -> `watch_started` with `WatchState.expires_at`.
5. **`rescanner.py`**:
   - `ResourceGoneError` -> content with `removed=True` and `complete=True`.
   - After `scan_resource`:
     - if the issue is in `outcome.issue_ids` -> `FOUND`;
     - else if `outcome.skipped` or the coverage state is not `scanned` ->
       `RescanResult(INCONCLUSIVE, ErrorCode.SCAN_INCOMPLETE, coverage=state)`;
     - else `CLEAN`.
   - Record coverage for the rescan job as well.

## Pitfalls

- Do not count resources twice: coverage is recorded once per fetched resource per job.
- `resource_coverage` UNIQUE is `(collector_config_id, resource_key)`. Upsert by select
  then update, or insert inside a nested transaction; imitate
  `issues/service.py` `begin_nested` + `IntegrityError`.
- Health alerts must not fire repeatedly. Only conditions absent from `open_conditions`
  notify.
- Never put a locator into a health notification. Collector names are operator-entered
  and allowed.

## Tests (`tests/collectors/core/test_v11_coverage_health.py`)

Use fake adapters and the SQLite harness; imitate `tests/collectors/core/test_runtime.py`.

- adapter content with `coverage=Coverage(UNSUPPORTED, IMAGE)` -> one `scan_coverage`
  row `(unsupported, image, 1)` and one `resource_coverage` row with no locator columns;
- the same resource later scanned -> the `resource_coverage` row is deleted;
- oversized content -> `too_large`;
- a removed ref -> a capturing fake recorder receives a `ResourceScan` with
  `removed=True`, and the `resource_coverage` row is deleted (assert only these two);
- handler resource task with unsupported/image coverage -> one job count and one
  per-resource row; handler removed task -> removed/complete scan and row deletion;
- sweeper REMOVED -> removed scan and row deletion; FAILED+FORBIDDEN ->
  `permission_denied` per-resource coverage and job count;
- rescanner:
  - incomplete content and no finding -> `INCONCLUSIVE` with coverage `partial`;
  - `ResourceGoneError` -> `RescanResult` CLEAN, and a capturing fake `ResourceScanner` /
    issue recorder receives a `ResourceScan` with `removed=True` and `complete=True`;
  - a finding -> FOUND, including when the content is incomplete;

These tests must not assert any issue `resolution` or `status` value. The end-to-end
`resource_deleted` resolution is owned and asserted by v11-p05 (its removed=True
complete-scan test) and by v11-p21. p05 is not a p08 dependency and may not be merged
when p08 runs.
- health:
  - 3 failed jobs -> `consecutive_failures` 3 and the condition opens once, with one
    notification for 2 ticks, including a second evaluation past the 60-second throttle;
  - a success resets it;
  - stale_success with cron `0 * * * *` and last success 3 h ago -> open;
  - watch expiring in 10 h -> open, in 20 h -> not;
  - a public unscannable row -> `unscannable_public`, and with the flag false -> not;
  - event-only config -> `event_only` true and status `warning`.
- `health_view` status mapping: no job yet -> `unknown`.

## Verification

Conventions section 7 with the scoped paths limited to the files this plan owns. The
adapter directories `src/telescreen/collectors/{slack,google,github}` belong to the
concurrent wave-2 plans v11-p09, v11-p10 and v11-p11, so never use
`src/telescreen/collectors` as a required scope:

- `<src>` = `src/telescreen/collectors/tasks.py src/telescreen/collectors/sweep.py src/telescreen/collectors/service.py src/telescreen/collectors/rescanner.py src/telescreen/collectors/coverage.py src/telescreen/collectors/health.py`
- `<tests>` = `tests/collectors/core`
- typelint filter (must print nothing):
  `grep -E '^(src/telescreen/collectors/(tasks|sweep|service|rescanner|coverage|health)\.py|tests/collectors/core/)' /tmp/telescreen-v11-p08-collectors-core-typelint.log`

All existing `tests/collectors/core` tests must still pass.

## Completion Criteria

- [x] Coverage and health modules plus integrations implemented; the rescanner returns
      INCONCLUSIVE and removed as specified.
- [x] Tests pass; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:17 JST
**Tasks Completed**: Added per-job and per-resource coverage recording/querying, collector health recording and open-condition alerts, coverage/removal hooks in collector task and sweep paths, accepted-event/watch health updates, and inconclusive/gone-resource rescan handling. Added SQLite tests for coverage classification, public-first cursor paging, removals, health thresholds/notifications, event/watch timestamps, stale/watch/public conditions, and rescan outcomes.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/v11-p08-collectors-core.md` before=0c619c0177cb3e256a87f8a7967405b05730fb190f27e3fedc060d46fc2cd326 after=recorded in `/tmp/telescreen-v11-p08-plan-sha256.log`; `src/telescreen/collectors/tasks.py` before=b18facebd38e2aad308b70c8865b3eaa71ebebb4299eb58b82e41f82798da9d6 after=e4691f156159a6d635cdbddc5f669fe4d7126dd72cb438e63418df2ef52fae6c; `src/telescreen/collectors/sweep.py` before=b11d49b0502bd36803f8364099b38453d1573d1a79e0eeaf2ab30a7127ed47c9 after=fa520e7eb7ef682045e219c6344057e6082cde1f9a3c1e2074ef57f0d2c39932; `src/telescreen/collectors/service.py` before=edad1023fa2e194f1517f61a3c44d5c7671be8fdc82f84c818cee655cbe58cab after=e7f0a6de8f42dd3612468c949daf9596da11f6e6b9bdf410f50a49021425baf4; `src/telescreen/collectors/rescanner.py` before=36a9ceff932520ccc13d67588e158b9e0e75ebb7927ad6a591d5104dcd38f304 after=c5417a44cf1922353dbde46122d08e6365c580fb0b986321e51858c7a30ae62d; `src/telescreen/collectors/coverage.py` before=absent after=7f241dbffb1e3d82816d44b303e45f5312d1bd12e92a83c3f97c38279454595c; `src/telescreen/collectors/health.py` before=absent after=985e111ec544c6015e75fdb6c001d10a3aea98fd74f60f531262c58b998cd31e; `tests/collectors/core/test_v11_coverage_health.py` before=absent after=ea1cbc28efd6e18adbd47fa2ebfa2fb6abc0e37546d31b9dcaba404bca59f00b.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/tasks.py src/telescreen/collectors/sweep.py src/telescreen/collectors/service.py src/telescreen/collectors/rescanner.py src/telescreen/collectors/coverage.py src/telescreen/collectors/health.py tests/collectors/core` -> exit 0 (log: `/tmp/telescreen-v11-p08-collectors-core-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/tasks.py src/telescreen/collectors/sweep.py src/telescreen/collectors/service.py src/telescreen/collectors/rescanner.py src/telescreen/collectors/coverage.py src/telescreen/collectors/health.py tests/collectors/core` -> exit 0 (log: `/tmp/telescreen-v11-p08-collectors-core-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/tasks.py src/telescreen/collectors/sweep.py src/telescreen/collectors/service.py src/telescreen/collectors/rescanner.py src/telescreen/collectors/coverage.py src/telescreen/collectors/health.py tests/collectors/core` -> exit 0 (log: `/tmp/telescreen-v11-p08-collectors-core-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/core 2>&1 | tee /tmp/telescreen-v11-p08-collectors-core-pytest.log` -> exit 0, 19 passed, 0 failed (log: `/tmp/telescreen-v11-p08-collectors-core-pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p08-collectors-core-typelint.log 2>&1; ! grep -E '^(src/telescreen/collectors/(tasks|sweep|service|rescanner|coverage|health)\.py|tests/collectors/core/)' /tmp/telescreen-v11-p08-collectors-core-typelint.log` -> exit 0; no owned-path diagnostics.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; test "$(grep -c '^- \[ \]' impl-plans/active/v11-p08-collectors-core.md)" -eq 0 && test "$(wc -l < impl-plans/active/v11-p08-collectors-core.md)" -lt 400 > /tmp/telescreen-v11-p08-plan-gate.log 2>&1; gate_result=$?; echo "exit=$gate_result" >> /tmp/telescreen-v11-p08-plan-gate.log; exit "$gate_result"` -> exit 0; no incomplete criteria and 400-line plan limit respected (log: `/tmp/telescreen-v11-p08-plan-gate.log`).
**Notes**: Earlier in-progress gates exposed corrected cron-interval boundary math and SQLite UTC/order test assumptions; a first plan-gate shell wrapper also used zsh's readonly `status` variable. These were corrected. The per-resource unique insert now uses a nested transaction and reloads the winning row on conflict. Final scoped gates pass on the current tree. Formal review and integration acceptance remain downstream workflow steps.

### Review revision: test-integrity findings TI-P08-001..003
**Tasks Completed**: Added local SQLite integration tests for `CollectorScanHandler` and `CollectorSweeperImpl` coverage wiring, including removed-row deletion and FAILED+FORBIDDEN permission-denied counts. Extended health dedup coverage with a NOW+2min tick beyond the 60-second throttle. Made the rescan scanner outcome configurable and asserted FOUND takes precedence over incomplete content. No production files changed; no issue status or resolution assertions were added.
**Verification**: The first revision pytest attempt exposed that the fake scanner did not capture the handler-created job id (1 failed, 20 passed; preserved captured log: `/tmp/telescreen-v11-p08-collectors-core-pytest-initial-failure.log`). The fake was corrected. Final scoped pytest: 21 passed, 0 failed, exit 0 (`/tmp/telescreen-v11-p08-collectors-core-pytest-final.log`). Final Ruff format check, Ruff lint, ty, and owned-path typelint grep all exit 0; logs are `/tmp/telescreen-v11-p08-collectors-core-format-final.log`, `/tmp/telescreen-v11-p08-collectors-core-ruff-final.log`, `/tmp/telescreen-v11-p08-collectors-core-ty-final.log`, and `/tmp/telescreen-v11-p08-collectors-core-typelint-final.log`.
Plan gate: all Completion Criteria remain checked; the final checkbox and under-400-line command exited 0 (`/tmp/telescreen-v11-p08-plan-gate-review-revision.log`).
**Hashes**: `src/telescreen/collectors/tasks.py` e4691f156159a6d635cdbddc5f669fe4d7126dd72cb438e63418df2ef52fae6c; `src/telescreen/collectors/sweep.py` fa520e7eb7ef682045e219c6344057e6082cde1f9a3c1e2074ef57f0d2c39932; `src/telescreen/collectors/service.py` e7f0a6de8f42dd3612468c949daf9596da11f6e6b9bdf410f50a49021425baf4; `src/telescreen/collectors/rescanner.py` c5417a44cf1922353dbde46122d08e6365c580fb0b986321e51858c7a30ae62d; `src/telescreen/collectors/coverage.py` 7f241dbffb1e3d82816d44b303e45f5312d1bd12e92a83c3f97c38279454595c; `src/telescreen/collectors/health.py` 985e111ec544c6015e75fdb6c001d10a3aea98fd74f60f531262c58b998cd31e; `tests/collectors/core/test_v11_coverage_health.py` 8c6a92c986fd035ebc4930b28ed54e64f40b448484ea109f1cc03937faca3c48.
