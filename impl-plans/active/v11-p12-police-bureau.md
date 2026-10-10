# V11-P12 Police Bureau: Accept resource_exposures, Claim Liveness, Police Health

**Status**: Completed
**Design Reference**: design-docs/specs/design-police-contract.md ("Contract 1.1"); design-docs/specs/design-v1-1-dspm-adoption.md sections 3 (Police row) and 8 (Police health)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p12-police-bureau`
- planPath: `impl-plans/active/v11-p12-police-bureau.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`, `v11-p03-police-contract`
- writePaths:
  - `impl-plans/active/v11-p12-police-bureau.md`
  - `src/telescreen/police/results.py`
  - `src/telescreen/police/claims.py`
  - `src/telescreen/police/health.py` (new)
  - `tests/police/test_v11_police.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

- The Bureau accepts the optional contract-1.1 `resource_exposures` on `detect` and
  `scan_shard` results and passes them to `IssueRecorder.record_external` as
  `ExternalScan.exposures`. The record path (v11-p05) applies them.
- Every claim, including an empty long poll, now counts as a heartbeat.
- A `PoliceHealthProvider` raises `police_silent` alerts.

Current code:

- `police/results.py:ResultService.accept` and `_external_findings`;
- `police/claims.py:ClaimService.claim` (updates `last_heartbeat_at` only when tasks are
  returned, at about line 64);
- `police/maintenance.py:PoliceMaintenance` (ScheduleProvider example).

## Non-goals

- No contract or schema change (v11-p03), no router change (v11-p18), no wiring.
- No push-worker liveness alerting (push has no heartbeat; design section 8).

## Invariants

- The `Redactor` scan of every string field (including exposure locators) still runs
  before anything is recorded. Any hit -> 422 `raw_value_detected`, as in v1.
- 1.0 results without the member behave exactly as before.
- New dataclass fields on `ResultService` / `ClaimService` are appended with defaults.

## Deliverables

1. **`results.py`**:
   - `_resource_exposures(value) -> tuple[LocatorExposure, ...]` parses the member into
     `Exposure` (enums validated by the schema; a missing optional member -> default).
   - For task types `detect` and `scan_shard` only, keep entries whose `locator` is a
     finding locator or in `complete_locators`. Drop the others and add
     `("exposures_dropped", json_int(n))` to the audit details of the accept entry.
   - Pass the result as `ExternalScan(..., exposures=...)`.
   - When `stats.resources_failed > 0` clears `complete_locators` (v1), exposures still
     apply to finding locators.
2. **`claims.py`**: `ClaimService.claim` sets `last_heartbeat_at = now` on every
   authenticated claim, including one that returns no tasks.
3. **`health.py`**:
   - `worker_health(worker_row, queued_matching_since: datetime | None, now) ->
     WorkerHealth(status: HealthStatus, conditions: tuple[HealthCondition, ...])`:
     - disabled -> `unknown`;
     - pull worker with `last_heartbeat_at` older than 5 min (or null) AND a matching
       task queued for more than 5 min -> `failing` + `police_silent`;
     - pull worker with a stale heartbeat but nothing queued -> `warning`;
     - push worker -> `ok` if `last_delivery_at` is set, else `unknown`;
     - otherwise `ok`.
   - `list_worker_health(session, now)` returns `(worker_id, WorkerHealth)` pairs for
     the router. Matching = task type in the worker's task types and task labels a
     subset of the worker's labels; reuse the matching helper from `claims.py` if one
     exists, otherwise implement it identically.
   - `PoliceHealthProvider` (ScheduleProvider; fields `notifier: NotificationSender |
     None = None`, `recipients: tuple[str, ...] = ()`, `clock`):
     - `tick` runs at most once per 60 s per process.
     - When `police_silent` opens (`health_alerted_at` is null), it sends one
       notification per channel (subject `"Telescreen health: police <name>
       police_silent"`), sets `health_alerted_at` and audits `health.alert_opened`.
     - When the condition clears, it resets `health_alerted_at` to null silently.

## Pitfalls

- Do not drop exposures for locators that only appear in `complete_locators`. Those are
  valid (clean resources).
- `queued` matching tasks must be older than 5 minutes (`created_at`). A task queued a
  moment ago is not evidence of silence.
- Never include task input or locators in the notification.

## Tests (`tests/police/test_v11_police.py`)

Imitate `tests/police/test_services.py` and `tests/police/fakes.py`.

- a detect result with 2 findings and an exposure for one locator -> the fake recorder
  receives `ExternalScan.exposures` with that one entry;
- an exposure for an unknown locator -> dropped, and the audit details have
  `exposures_dropped=1`;
- an exposure locator containing a sample secret
  (`tests.support.samples.aws_access_key_id().value` inside the locator) -> rejected with
  `RAW_VALUE_DETECTED`, nothing recorded;
- a 1.0 result -> unchanged behavior;
- an empty claim updates `last_heartbeat_at`;
- health:
  - stale heartbeat + matching task queued 6 min ago -> failing / `police_silent`, one
    notification across 2 ticks;
  - a task with labels the worker lacks -> not matching -> warning;
  - a fresh heartbeat -> ok;
  - disabled -> unknown;
  - the condition clears -> `health_alerted_at` reset.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/police` and `<tests>` =
`tests/police`. Also `uv run --frozen pytest -p no:cacheprovider tests/contract > /tmp/telescreen-v11-p12-police-bureau-contract.log 2>&1; echo "exit=$?"`
-> `exit=0`.

## Completion Criteria

- [x] Exposures parsed, filtered and forwarded; claim liveness; health provider and view.
- [x] Tests pass; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:13 JST
**Tasks Completed**: Parsed Contract 1.1 resource exposures for detect and scan_shard, filtered them against finding/complete locators, forwarded safe DTOs, counted dropped entries in audit details, retained finding exposures on partial shard failures, updated every active authenticated pull claim as a heartbeat, and added worker health view/provider with transition alerts.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: src/telescreen/police/results.py before=0fa8b43f42addf08ce09b101315f0c5cbf4b12b122844e034453b1ca5d4030d2 after=800afa13b4e815007249acfd5538247ba418d40dd7c3571dc15a8ac7afab3b26; src/telescreen/police/claims.py before=729f2a421cb2f992ebc85d2fb838a139350475fde878ee1285dae1ddc30e204c after=ff5c99dec10cccb67b251c94b9b0423b289f4c3a7d43ad2feb1fba2cc30d893a; src/telescreen/police/health.py before=absent after=d7ca41a2d85f52fccc166810e4b4f40b39d474ef174f8fb43adbbb5b870d7a3e; tests/police/test_v11_police.py before=absent after=b2f42231b3628fd49469dd0cad83376cb9f3a16a2c78e785130c69d04e51e07c; this plan before=c6fb44e91c6087da949e0d71d6c1db518a4c0979631905c955de7e0895b5c2cd.
**Verification**:
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/police tests/police -> exit 0 (14 files formatted).
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/police tests/police -> exit 0 (all checks passed).
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/police tests/police -> exit 0 (all checks passed).
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/police 2>&1 | tee /tmp/telescreen-v11-p12-police-bureau-pytest.log -> exit 0, 25 passed (log: /tmp/telescreen-v11-p12-police-bureau-pytest.log).
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/contract > /tmp/telescreen-v11-p12-police-bureau-contract.log 2>&1 -> exit 0, 18 passed (log: /tmp/telescreen-v11-p12-police-bureau-contract.log).
- set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p12-police-bureau-typelint.log 2>&1; ! grep -E '^(src/telescreen/police/|tests/police/)' /tmp/telescreen-v11-p12-police-bureau-typelint.log -> exit 0 (no matching diagnostics).
- test "$(grep -c '^- \\[ \\]' impl-plans/active/v11-p12-police-bureau.md)" -eq 0 -> exit 0 (all Completion Criteria checked).
**Notes**: Superseded initial Police test run was 1 failed/23 passed because SQLite read the UTC heartbeat back without tzinfo; the assertion now normalizes via ensure_utc, and final current-source Police tests pass 25/25. Captured failure output is preserved at tmp/telescreen-v11-20261010-c1/v11-p12-police-bureau/step6-implement/prior-attempt-1-police-pytest.txt. Initial formatter and lint issues were corrected before final passing gates. Contract and health alerts contain no task inputs or locators. Final plan hash is recorded in tmp/telescreen-v11-20261010-c1/v11-p12-police-bureau/step6-implement/final-hashes.txt.
