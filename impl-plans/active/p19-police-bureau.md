# P19 Police Bureau Side (Gateway, Pull and Push Transports, Result Acceptance) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-police-contract.md (Endpoints, Push signing, Bureau side), design-docs/specs/design-map-reduce-sweeps.md (Police contract additions), design-docs/specs/design-masking.md (Re-scan of inbound masked data)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p19-police-bureau`
- planPath: `impl-plans/active/p19-police-bureau.md`
- wave: 4
- dependsOn: `p05-police-contract`, `p08-masking`, `p10-api-common`, `p11-police-sdk`
- writePaths:
  - `impl-plans/active/p19-police-bureau.md`
  - `src/telescreen/police`
  - `src/telescreen/api/routers/police.py`
  - `tests/police`
  - `tests/contract/test_bureau_contract.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This is the Bureau side of the versioned police contract (Kestra-2.0-style pull workers
and signed push webhooks, for example n8n). The `PoliceGateway` port hides the transports
from the workflow engine and the map-reduce machinery. Every result is schema-validated
and re-scanned; raw values are rejected.

## Non-goals

- The Bureau never imports `telescreen_police_sdk`, except in
  `tests/contract/test_bureau_contract.py`.
- No changes to the schemas (p05). No message-queue transport.

## Deliverables (`src/telescreen/police/`)

- `repository.py`: queries and updates for `PoliceWorkerRow` and `PoliceTaskRow`.
- `signing.py`: the same algorithm as the SDK, implemented independently:
  - `derive_push_secret(master: bytes, worker_id: str) -> bytes`
    (`base64url(HMAC-SHA256(master, "police-push\0" + worker_id))` as bytes);
  - `sign(secret, body, timestamp, nonce) -> str` (`v1=<hex>`);
  - `verify_signature(headers, body, secret, now, nonce_store_result) -> bool`
    (300 s window, constant-time comparison).
- `registration.py`: `RegistrationService(enrollment_token: SecretValue, signing_key: SecretValue | None, allow_http: bool, audit, clock)`.
  `register(session, raw_manifest: bytes) -> bytes` (response JSON):
  - schema `manifest.schema.json`;
  - a major version other than 1 -> `unsupported_contract_version` (422);
  - push requires https unless `allow_http`, and requires a signing key (else
    `config_error`);
  - create the worker row;
  - pull: a random worker token (hash stored) returned once; push: the derived
    `signing_secret` returned;
  - `lease_seconds=60`, `heartbeat_interval_seconds=20`, `max_wait_seconds=30`;
  - audit `police.registered`.
- `gateway.py`:
  - `class PoliceTransport(Protocol)` with `async def deliver(self, session, task: PoliceTaskRow) -> None`.
  - `PullQueueTransport` (no-op: the task stays `queued`).
  - `WebhookTransport(queue: TaskQueue, clock)` enqueues `police_push_delivery` with
    idempotency key `police-push:<task_id>:<attempt>`.
  - `RoutingPoliceGateway(pull: PullQueueTransport, webhook: WebhookTransport, clock)`
    implements `PoliceGateway`. `dispatch` inserts the task row (`queued`,
    `input_json = dump_json(request.input)`, labels, deadline, step_run_id,
    collector_config_id, max_attempts). If an active push worker matches (task type in
    the manifest, task labels a subset of the worker labels; choose the oldest
    `last_delivery_at`), it assigns `worker_id` and uses the webhook transport;
    otherwise it uses pull. `cancel` sets `cancelled` unless the task is terminal.
- `claims.py`: `ClaimService(clock, sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep)`.
  - `claim(sessions, worker, request) -> list[PoliceTaskRow]`: long-poll. Re-check every
    1 s until tasks exist or `wait_seconds` passes. Each check is its own transaction
    using `with_for_update(skip_locked=True)`. Matching: status `queued`, no `worker_id`
    OR `worker_id` = this pull worker, type allowed, labels a subset; limit `max_tasks`
    (at most the worker's concurrency). Claiming sets `leased`, `worker_id`,
    `attempt += 1` and `lease_expires_at = now + 60 s`.
  - `heartbeat(session, worker, task_ids) -> list[str]` extends the leases of tasks
    leased to this worker, returns the cancelled ids among `task_ids`, and updates
    `last_heartbeat_at`.
- `results.py`: `ResultService(issues: IssueRecorder, configs: CollectorConfigReader, signaler: RunSignaler, map_sink: MapResultSink, redactor: Redactor, audit, clock)`.
  `accept(session, task: PoliceTaskRow, raw_result: bytes) -> None`:
  1. Validate the schema with `result-envelope.schema.json`.
  2. Check that `task_id` matches.
  3. `verify_remediation` requires `outcome`.
  4. Run `redactor.has_hits` over EVERY string in the result (summary, all finding
     fields, `complete_locators`, error detail, payload strings recursively). Any hit ->
     audit `police.result_rejected` (ids only) and raise `raw_value_detected` (422); the
     task stays open.
  5. Route the result:
     - `detect` and `scan_shard` -> `issues.record_external(ExternalScan(source="police:" + config.name, collector_config_id, detector=police, sweep_covered=True, complete_locators, findings))`;
     - `scan_shard` then -> `map_sink.shard_finished` with a `ShardSummary` built from
       `stats` plus the `RecordOutcome` (`resources_skipped = 0`);
     - `partition` -> validate `payload` against `partition-result.schema.json`, then
       `map_sink.partition_page`;
     - `status: failed` -> for `partition` and `scan_shard`,
       `map_sink.task_failed(ShardFailure(code, retry_after_seconds))` (code
       `rate_limited` maps to `RATE_LIMITED`, an unknown code to `FETCH_FAILED`); for
       workflow tasks, a signal with outcome `error`;
     - a task with `step_run_id` -> `signaler.signal(step_run_id, StepSignal(police_result, outcome=passed|failed|error, data={summary (redacted), findings_count}))`.
  6. Mark the task `completed` (or `failed`) with `result_json` (canonical masked),
     `outcome` and `completed_at`; audit `police.result_accepted`.
- `delivery.py`: `PolicePushDeliveryHandler(sessions, http: httpx.AsyncClient, signing_key: SecretValue, public_base_url: str, results: ResultService, clock)`
  implements `TaskHandler` (`police_push_delivery`):
  1. Build the task envelope with
     `callback {url: <public_base_url>/api/police/v1/callbacks/<task_id>, result_token}`,
     where the token is 32 random bytes in base64url and only its hash is stored.
  2. Sign the request (headers per the design) and POST it to `endpoint_url`.
  3. 200 -> accept the body as a result (sync).
  4. 202 -> status `delivered` and `last_delivery_at`.
  5. Anything else, or a network error -> retry with backoff `10 s * 2^(attempt-1)`
     until `max_attempts` or the deadline -> task `failed`.
- `maintenance.py`: `PoliceMaintenance(map_sink: MapResultSink, clock)` implements
  `ScheduleProvider`:
  - expired leases -> back to `queued` with `worker_id` cleared (pull) until
    `max_attempts`, then `failed`;
  - tasks past their deadline and not terminal -> `expired`;
  - map tasks that become `failed` or `expired` -> `map_sink.task_failed(...)`. Workflow
    timeouts are handled by the engine timer.

### Router `routers/police.py`
`PoliceRouterDeps(ctx: ApiContext, registration, claims, results, replay: ReplayNonceStore, signing_key: SecretValue | None)`.

- Contract endpoints under `/api/police/v1`:
  - `GET /openapi.json` (`contracts.validator.openapi_document()`);
  - `POST /workers/register` (Bearer enrollment token, constant-time comparison);
  - `POST /workers/{id}/claim`, `POST /workers/{id}/heartbeat` and
    `POST /tasks/{task_id}/result` (Bearer worker token, hash lookup, and the worker
    must be active and own the path id);
  - `POST /callbacks/{task_id}`: signature with the task's worker's derived secret, nonce
    via `ReplayNonceStore.remember(scope="police-callback:<worker_id>")`, and
    `result_token` hash equal, unused and before the deadline.
    `callback_token_used_at` is set in the same transaction as the acceptance.
- All bodies: raw (`read_limited_body` 1 MiB), then schema validation, then parsing.
  Responses are written with `dump_json` as `application/json`.
- Admin endpoints: `GET /api/police/workers` (read) and
  `POST /api/police/workers/{id}/disable` (admin).

## Key Pitfalls

- A result for a cancelled or terminal task -> 409 `conflict` (late results for
  timed-out map items are dropped this way).
- Never log the inputs, results, tokens or secrets of tasks.
- `police_tasks.labels` and `police_workers.labels` are comma-separated `key=value`
  strings. Compare them as sets.
- Long-poll must not hold a DB transaction while sleeping.

## Tests

- `tests/police/`, with SQLite harness, fakes for `IssueRecorder`, `RunSignaler`,
  `MapResultSink` and `AuditLog`, `FakeClock` and a fake sleeper:
  - register pull -> token returned once; register push over http with
    `allow_http=false` -> 422; a wrong enrollment token -> 401; contract `2.0` -> 422.
  - dispatch with no push worker -> queued; a pull claim with matching labels -> leased;
    non-matching labels -> empty after `wait_seconds` (the fake sleeper counts calls).
  - heartbeat extends the lease and returns cancelled ids.
  - lease expiry through maintenance -> queued again; after max attempts -> failed;
    a map task -> `task_failed` called.
  - result with a raw sample in `summary` -> 422, the task still leased, the audit
    details hold no leak.
  - `verify_remediation` passed -> signal with outcome passed.
  - `scan_shard` result with stats -> `record_external` called with complete locators,
    then `shard_finished` with `resources_failed` from the stats.
  - `partition` result with 1001 shards -> 422.
  - push: dispatch with a matching push worker -> a delivery task; the delivery handler
    with MockTransport answering 202 -> delivered; the callback with a valid signature
    and token -> accepted; a replayed nonce -> 401; reusing the token -> 409; a 200 sync
    answer -> accepted without a callback.
- `tests/contract/test_bureau_contract.py`: mount the police router in a FastAPI app
  (SQLite) and drive it with the SDK `BureauClient` and `PullWorker` through
  `httpx.ASGITransport`. The full cycle is register, claim, heartbeat and result. Every
  Bureau response body validates against its schema.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/police tests/contract/test_bureau_contract.py 2>&1 | tee /tmp/telescreen-p19-police-bureau-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p19-police-bureau-typelint.log 2>&1; grep -E '^(src/telescreen/police|src/telescreen/api/routers/police.py|tests/police|tests/contract/test_bureau_contract.py)' /tmp/telescreen-p19-police-bureau-typelint.log   # prints nothing
grep -rn "telescreen_police_sdk" src/telescreen   # prints nothing
```

## Completion Criteria

- [ ] Registration, gateway and transports, claims, results, push delivery, maintenance and router delivered with the exact names
- [ ] Pull and push flows, replay, token one-time use and raw-value rejection tested
- [ ] The SDK-driven contract test passes
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
