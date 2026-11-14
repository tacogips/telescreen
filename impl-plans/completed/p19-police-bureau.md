# P19 Police Bureau Side (Gateway, Pull and Push Transports, Result Acceptance) Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-police-contract.md (Endpoints, Push signing, Bureau side incl. the Attempts bullet revised in session 340), design-docs/specs/design-map-reduce-sweeps.md (Police contract additions), design-docs/specs/design-masking.md (Re-scan of inbound masked data)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p19-police-bureau`
- planPath: `impl-plans/completed/p19-police-bureau.md` (archived from `impl-plans/active/` after run A2 integration acceptance, session 340; writePaths below record the run-time location)
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
  - `derive_push_secret(master: bytes, worker_id: str) -> str`
    (`base64url(HMAC-SHA256(master, "police-push\0" + worker_id))`, unpadded or padded
    exactly as returned at registration). The HMAC key for `sign`/`verify` is
    `derive_push_secret(...).encode("utf-8")`: the UTF-8 bytes of the string, NEVER the
    base64-decoded digest (design D4; the SDK and n8n hold the string);
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
    idempotency key `police-push:<task_id>:1` (the engine task is enqueued once; later
    attempts are engine retries of the same task, see `delivery.py`).
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
  2. Increment `task.attempt` by exactly 1 (committed before the POST, in its own short
     transaction), sign the request (headers per the design) and POST it to
     `endpoint_url`. Never hold a DB transaction across the POST.
  3. 200 -> accept the body as a result (sync).
  4. 202 -> status `delivered` and `last_delivery_at`.
  5. Anything else, or a network error -> `TaskResult.retry(now + 10 s * 2^(attempt-1),
     error_code)` while `attempt < max_attempts` and the retry time is before the
     deadline; otherwise task `failed` and, for a map task (`partition`/`scan_shard`),
     `map_sink.task_failed(ShardFailure(FETCH_FAILED, None))`. A workflow task is left to
     the engine `police_timeout` (same rule as `maintenance.py`).
  6. A task already terminal or `cancelled` when the handler runs -> no POST, done.
- `maintenance.py`: `PoliceMaintenance(map_sink: MapResultSink, clock)` implements
  `ScheduleProvider`:
  - expired leases -> back to `queued` with `worker_id` cleared and `attempt` UNCHANGED
    while `attempt < max_attempts`, otherwise `failed` (the next claim increments it);
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

## Run A Interface Notes (2026-10-09, session 338)

Not started at s319 or s338; implement from this plan in wave A2-2 of
`impl-plans/active/telescreen-v1-20261009-s340-dispatch.json`. The in-tree contracts below
were checked during s338 planning; use them exactly and do not edit them (Reconciliation
request if one is wrong):

- `src/telescreen/ports/police.py`: `PoliceGateway.dispatch(session, request) -> str` and
  `cancel(session, task_id) -> None` take `session` first. `PoliceTaskRequest.labels` is
  `tuple[str, ...]`; join it into the comma-separated column. Callers already exist in
  `workflow/executors/police_verify.py`, `workflow/mapreduce/` and `collectors/police.py`.
- `src/telescreen/db/models/police.py:PoliceTaskRow.attempt` defaults to 1. `dispatch` must
  insert `attempt=0` so the first claim or delivery is attempt 1 (key
  `police-push:<task_id>:1`, backoff `10 s * 2^0`). There is no next-retry column: push
  retries return `TaskResult.retry(available_at, error_code)` from the delivery handler.
- Replay: `src/telescreen/db/replay.py:ReplayNonceStore.remember(session, scope, nonce,
  expires_at, now) -> bool` (in `db`, not `ports`); pass `expires_at = now + 600 s`.
- `src/telescreen/ports/workflow.py`: `RunSignaler.signal(session, step_run_id,
  StepSignal)`; `StepSignal(kind=POLICE_RESULT, outcome, data)`; `TaskHandler` has
  `kind: EngineTaskKind` and `handle(task: ClaimedTask) -> TaskResult`;
  `ScheduleProvider.tick(session, now) -> int`. `PolicePushDeliveryHandler.kind` is
  `EngineTaskKind.POLICE_PUSH_DELIVERY` (nothing enqueues or handles it yet; this plan
  does both). Registration into the engine worker is assembly (run B, p28); tests drive
  the handler directly with a `ClaimedTask`.
- `src/telescreen/ports/findings.py:ExternalScan` also needs `scan_job_id` (use `None`)
  and `observed_at` (`clock.now()`). `source` is `"police:" + config.name` when the task has
  `collector_config_id`, otherwise `"police:" + worker.name`. SDK `MaskedFinding` fields map
  one-to-one onto `ExternalFinding`.
- `src/telescreen/ports/sweeps.py`: `ShardSummary` needs every field: `resources_scanned`
  and `resources_failed` and `bytes_scanned` from the result `stats`; `resources_skipped=0`;
  `findings_inserted`, `issues_opened`, `issues_auto_resolved` from `RecordOutcome`;
  `findings_by_rule_class` counted from the result findings. `PartitionPage.sub_corpora`
  is `tuple[SubCorpus, ...]`: wrap each wire string. `ShardFailure(error_code,
  retry_after_seconds)`.
- `src/telescreen/masking/redact.py:Redactor.has_hits(text)` checks one string; walk the
  parsed `JsonValue` and check every string leaf (summary, finding fields,
  `complete_locators`, error detail, payload).
- `src/telescreen/contracts/validator.py:validate(name, raw: bytes) -> list[SchemaError]`
  with names like `"result-envelope.schema.json"`; `openapi_document() -> str`.
- `src/telescreen/api/common`: `ApiContext(sessions, clock, audit)`;
  `read_limited_body(request, max_bytes)`; `require_scope` handles API tokens only, so
  enrollment and worker bearer checks are written in this plan (reuse `hash_token`,
  compare with `hmac.compare_digest`). `SecretValue.value` is `str`: `.encode()` before
  HMAC.
- Existing pattern to imitate for routers: `src/telescreen/api/routers/evidence.py`
  (`<Area>RouterDeps`, raw body, dedicated error response). For services:
  `src/telescreen/notifications/sender.py:NotificationService`.
- Put tests in several files under `tests/police/` (for example `test_registration.py`,
  `test_claims.py`, `test_results.py`, `test_push.py`) so none reaches 1000 lines.

## Key Pitfalls

- A result for a cancelled or terminal task -> 409 `conflict` (late results for
  timed-out map items are dropped this way).
- Never log the inputs, results, tokens or secrets of tasks.
- `police_tasks.labels` and `police_workers.labels` are comma-separated `key=value`
  strings. Compare them as sets.
- Long-poll must not hold a DB transaction while sleeping.
- `attempt` changes only in `ClaimService.claim` and `PolicePushDeliveryHandler` (+1 each);
  re-queue never touches it. `max_attempts` is the value stored at dispatch from
  `PoliceTaskRequest.max_attempts` (default 3); ignore the DB column default 5.
- A disabled worker, an unknown token, or a token for a different `{id}` -> 401
  `unauthorized` with a static detail. Compare token hashes with `hmac.compare_digest`.
- Raw-value rejection must not mutate the task (status, lease, attempt, result_json stay
  as they were) and the audit entry carries only `task_id` and `worker_id`.
- `POST /callbacks/{task_id}` for a pull task or a task without `callback_token_hash`
  -> 401; after acceptance the same token -> 409 `conflict`.
- Do not import `telescreen_police_sdk` anywhere under `src/telescreen` (only the
  contract test). Do not edit `src/telescreen/contracts`, `ports`, `db`, `api/common`
  or any other plan's paths; write a Reconciliation request instead.

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
  - attempts: dispatch -> row has `attempt == 0` and `max_attempts == 3`; claim ->
    `attempt == 1`; lease expiry through maintenance -> `queued`, `attempt == 1`;
    second claim -> `attempt == 2`; with `max_attempts=2`, the next expiry -> `failed`.
  - push retries: MockTransport answering 500 -> `TaskResult` kind retry with
    `available_at == now + 10 s`, `attempt == 1`; second run -> `now + 20 s`; at
    `max_attempts` -> task `failed` and, for a `scan_shard` task, `task_failed` called once.
  - signing interop: the `signing_secret` string returned by push registration, used as
    `signing_secret.encode("utf-8")`, verifies a Bureau-signed delivery with the Bureau's
    own `verify_signature`; a key built from the base64-decoded digest does NOT verify.
  - disabled worker (admin disable) -> claim 401; another worker's token on `{id}` -> 401.
  - leak: after a rejected raw-sample result, no `police_tasks` column, audit row or
    captured log record (caplog) contains the sample (`tests.support.samples`).
- `tests/contract/test_bureau_contract.py`: mount the police router in a FastAPI app
  (SQLite) and drive it with the SDK `BureauClient` and `PullWorker` through
  `httpx.ASGITransport`. The full cycle is register, claim, heartbeat and result. Every
  Bureau response body validates against its schema. Add a push case: register a push
  worker, deliver a task through `PolicePushDeliveryHandler` whose `httpx.AsyncClient`
  uses an `httpx.MockTransport` that forwards the request to the SDK
  `WebhookHandler(handlers, secret=registration.signing_secret.encode("utf-8"),
  mode=WebhookMode.SYNC)` via `WebhookRequest`/`WebhookReply`. Expected: the SDK accepts
  the Bureau signature (reply 200, not 401) and the Bureau accepts the sync result (task
  `completed`).

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
test -z "$(find src/telescreen/police tests/police -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-p19-police-bureau-pytest-all.log 2>&1; echo "exit=$?"   # informational
```

Evidence each command must establish: the four scoped gates exit 0; the pytest log
shows every `tests/police` and contract test passed with 0 failed and 0 errors; the
typelint grep prints nothing; the SDK import grep prints nothing; no file reaches 1000
lines.

## Completion Criteria

- [x] Registration, gateway and transports, claims, results, push delivery, maintenance and router delivered with the exact names
- [x] Pull and push flows, replay, token one-time use and raw-value rejection tested
- [x] Attempt counting, push retry/exhaustion and signing-interop tests pass
- [x] The SDK-driven contract test (pull cycle and push case) passes
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 06:00
**Tasks Completed**: Implemented Police registration, token hashing, independent push signing, repository and transport routing, attempt-zero dispatch, long-poll claims/heartbeats, result schema and DLP validation, external scan and sweep/workflow routing, signed webhook delivery with callback-token hashing, lease/deadline maintenance, Police contract/admin router, service tests, and SDK-driven pull/push/callback contract tests.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**:
- `src/telescreen/police/__init__.py` before=absent after=49f5067f4359e6723b733d74aac3314189db4a4c3e29e8202687efb64d1028b2
- `src/telescreen/police/claims.py` before=absent after=729f2a421cb2f992ebc85d2fb838a139350475fde878ee1285dae1ddc30e204c
- `src/telescreen/police/delivery.py` before=absent after=6f25778487441b1401185f32cc77d541ca82cfde02814557fb42aa626317bbb4
- `src/telescreen/police/gateway.py` before=absent after=c605d97f782b1687efea1d089247d026a3331b2d3571c624c770129e13acf3a3
- `src/telescreen/police/maintenance.py` before=absent after=218d01d8733f9cf3ba061bbe6e7bb918a41d93326afa3c6b423733159731ea55
- `src/telescreen/police/registration.py` before=absent after=dfc37b685ac74d79fac347a93213ccdd89746b67e480a6bc568a44fab09bb060
- `src/telescreen/police/repository.py` before=absent after=6e587984fa6f9957a2eb3198e4bce7ae25070add65112ba665edb31363af221a
- `src/telescreen/police/results.py` before=absent after=0fa8b43f42addf08ce09b101315f0c5cbf4b12b122844e034453b1ca5d4030d2
- `src/telescreen/police/signing.py` before=absent after=a3d7c4e7356b28b0c4c178c30e98f72c107db7b39a8e794220e344c3a4f6d5b4
- `src/telescreen/api/routers/police.py` before=absent after=98c01b4cb7f599247068304081bb604176acd0116115c1038682833a21eeae5b
- `tests/police/fakes.py` before=absent after=639b6bdc8c366afdbd22373af4c8fca942f506fd537a19423c6be19056d8072c
- `tests/police/test_services.py` before=absent after=5955cc8da82d0b9a16ad1333b6cd90104fa8c61b02f81ce467655b2aafae685b
- `tests/contract/test_bureau_contract.py` before=absent after=dceacd8fc0b7150bf330bb6b50ce3e1e229c09dbb8fb3bba08534b47ff4e7f5d
- `impl-plans/active/p19-police-bureau.md` before=e3a19791169c2490f4d228510b4337473a5890c111661cf09c1f1fb2b4e7a239 after=recorded in `/tmp/telescreen-p19-police-bureau-plan.sha256`
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (log: `/tmp/telescreen-p19-final-format-gate.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (log: `/tmp/telescreen-p19-final-ruff-gate.log`)
- `uv run --frozen ty check src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (log: `/tmp/telescreen-p19-final-ty-gate.log`)
- `uv run --frozen pytest -p no:cacheprovider tests/police tests/contract/test_bureau_contract.py` -> exit 0, 9 passed (log: `/tmp/telescreen-p19-final-pytest-gate.log`)
- `uv run --frozen python tools/typelint.py` -> exit 0, 268 files clean (log: `/tmp/telescreen-p19-final-typelint-gate.log`); scoped diagnostic scan `! grep -E '^(src/telescreen/police|src/telescreen/api/routers/police.py|tests/police|tests/contract/test_bureau_contract.py)' /tmp/telescreen-p19-final-typelint-gate.log` -> exit 0 (log: `/tmp/telescreen-p19-final-scope-gate.log`)
- `! grep -rn 'telescreen_police_sdk' src/telescreen` -> exit 0 (log: `/tmp/telescreen-p19-final-imports-gate.log`)
- `test -z "$(find src/telescreen/police tests/police -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"` -> exit 0 (log: `/tmp/telescreen-p19-final-size-gate.log`)
**Notes**: First verification iterations exposed type annotations and response-shape issues; corrections were applied before the passing final-source gates. The formal independent review and serial integration/finalization gates remain downstream workflow steps.

### Session: 2026-10-09 p19 audit-commit correction
**Tasks Completed**: Changed pull-result and signed-callback routes to catch only raw-value rejection inside the transaction, allowing the id-only rejection audit to commit while returning 422 and leaving task results and one-time callback state unchanged. Added a SQLite-backed contract-route test using the real `AuditLogService`; it confirms the task stays leased, `result_json` remains empty, and the stored audit row excludes the raw sample.
**Tasks In Progress**: None for p19 implementation. Independent test-integrity/adversarial reviews and serial integration/finalization remain downstream.
**Blockers**: None
**Hashes**:
- `src/telescreen/api/routers/police.py` after=`f7a18dac805629c4f2ed2e204cb5063b75e7fee155b5ec942534d60a31e036c2`
- `tests/contract/test_bureau_contract.py` after=`1fd0cd7471f19d99a5161010759f73bcbdd62d4a05e15f2e20ef289a77538fb1`
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-audit-final2-format.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-audit-final2-ruff.log`)
- `uv run --frozen ty check src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-audit-final2-ty.log`)
- `uv run --frozen pytest -p no:cacheprovider tests/police tests/contract/test_bureau_contract.py -q` -> exit 0, 10 passed, 0 failed (`/tmp/telescreen-p19-audit-final2-pytest.log`)
- `uv run --frozen python tools/typelint.py` -> exit 0, 268 files clean; scoped diagnostic scan empty (`/tmp/telescreen-p19-audit-final2-typelint.log`, `/tmp/telescreen-p19-audit-final2-typelint-filter.log`)
- `! grep -rn 'telescreen_police_sdk' src/telescreen` -> exit 0, no matches (`/tmp/telescreen-p19-audit-final2-imports.log`)
- Python file-size check under `src/telescreen/police` and `tests/police` -> exit 0, no files at 1000+ lines (`/tmp/telescreen-p19-audit-final2-size.log`)
- `grep -c '^- \[ \]' impl-plans/active/p19-police-bureau.md` -> 0 unchecked criteria (`/tmp/telescreen-p19-audit-final2-plan.log`)
**Notes**: The SQLite test specifically verifies persistence across the request transaction boundary. Earlier intermediate Ruff/format and malformed size-check invocations were corrected; the final-source gates above all pass.

### Session: 2026-10-09 test-integrity review repairs (`comm-005659`)
**Tasks Completed**: Addressed `P19-TI-PUSH-MAP-EXHAUSTION`: push delivery now reports `FETCH_FAILED` to `MapResultSink.task_failed` for `partition`/`scan_shard` on both retry exhaustion and the missing-worker path. Added `tests/police/test_review_coverage.py` for label-mismatch long polling, heartbeat lease/cancellation, maintenance failure routing, passed workflow signal, scan-shard reconciliation and resource counts, 1001-shard rejection, push dispatch/202 delivery, and retry timing with both workflow and map task types. Added contract-route assertions for wrong enrollment, cross-worker and disabled-worker 401s and HTTP 422 on 1001 shards. Existing tests remain unchanged.
**Tasks In Progress**: Independent test-integrity/adversarial review and serial integration/finalization remain downstream workflow steps.
**Blockers**: None.
**Review findings addressed**: `P19-TI-PUSH-MAP-EXHAUSTION`, `P19-TI-MISSING-PLAN-TESTS`.
**Hashes**:
- `src/telescreen/police/delivery.py` after=`ad6e58a02bd9bc80e098dcda8f097dea3c3ba22d3ffe1b7bd8169128834930b7`
- `tests/police/test_review_coverage.py` after=`db39510fe4b45f5c4a421ef128c610a55276495f4c77c9466593a2e42179faef`
- `tests/contract/test_bureau_contract.py` after=`9171b684f8c9713bd4f55b8265cd2e83cac02f6a277469325f9e1f41be4a453e`
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0, 14 files formatted (`/tmp/telescreen-p19-review-final2-format.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-review-final2-ruff.log`)
- `uv run --frozen ty check src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-review-final2-ty.log`)
- `PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider tests/police tests/contract/test_bureau_contract.py -q` -> exit 0, 23 passed, 0 failed (`/tmp/telescreen-p19-review-final2-pytest.log`)
- `uv run --frozen python tools/typelint.py` plus scoped diagnostic filter -> exit 0, 269 files clean and no scoped violations (`/tmp/telescreen-p19-review-final2-typelint.log`, `/tmp/telescreen-p19-review-final2-typelint-scope.log`)
- SDK runtime import boundary, Python file size check (<1000 lines), and unchecked-plan-box check -> exit 0.
**Notes**: Initial repair verification found assertion mismatches for SQLite's naive timestamps and the `ShardFailure.error_code` field; corrected using UTC normalization and the declared field. A subsequent lint pass found untyped captured results and constant-body style issues; corrected before the passing final-source gates. The prior failed run is recorded at `/tmp/telescreen-p19-review-repair-pytest-initial.log` (23 tests: 18 passed, 5 assertion failures); the final-source rerun passes all 23.

### Session: 2026-10-09 adversarial review repair (`comm-005664`)
**Tasks Completed**: Addressed `P19-ADV-PULL-LABEL-HOL` in `matching_pull_tasks`: query queued candidate IDs/labels without locks, filter label subsets in created-at/ID order before applying `limit`, then lock/recheck only the selected IDs. Added `test_claim_skips_older_nonmatching_task_for_newer_matching_task`; it verifies west-new is leased with attempt 1 while east-old remains queued at attempt 0. The existing long-poll mismatch test is unchanged.
**Tasks In Progress**: Independent re-review and serial integration/finalization remain downstream.
**Blockers**: None.
**Review finding addressed**: `P19-ADV-PULL-LABEL-HOL`.
**Hashes**:
- `src/telescreen/police/gateway.py` before=`c605d97f782b1687efea1d089247d026a3331b2d3571c624c770129e13acf3a3` after=`8370a9e5f5096a90f63ae47e86fba6d90d09ea45f152963ff780683285dc0e0c`
- `tests/police/test_review_coverage.py` before=`db39510fe4b45f5c4a421ef128c610a55276495f4c77c9466593a2e42179faef` after=`2c39785d85df3d47fc72444231fddf26a49058e189b1938a6383886e2242cf3f`
**Verification**:
- Regression before fix -> exit 1, expected assertion failure (`/tmp/telescreen-p19-hol-regression-before-fix.log`); after fix -> exit 0, 1 passed (`/tmp/telescreen-p19-hol-regression-after-fix.log`).
- `/tmp/p19_adv_hol_repro.py` before fix -> `claimed with max_tasks=1: []`; after fix -> `claimed with max_tasks=1: ['west-new']` (logs `/tmp/telescreen-p19-hol-repair-before.log`, `/tmp/telescreen-p19-hol-final2-repro.log`, both exit 0).
- `uv run --frozen ruff format --check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-hol-final2-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-hol-final2-ruff.log`).
- `uv run --frozen ty check src/telescreen/police src/telescreen/api/routers/police.py tests/police tests/contract/test_bureau_contract.py` -> exit 0 (`/tmp/telescreen-p19-hol-final2-ty.log`).
- `PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider tests/police tests/contract/test_bureau_contract.py -q -rs` -> exit 0, 24 passed, 0 failed (`/tmp/telescreen-p19-police-bureau-hol-fix-final-pytest.log`).
- `uv run --frozen python tools/typelint.py` plus scoped diagnostic filter -> exit 0, 269 files clean and no scoped violations (`/tmp/telescreen-p19-hol-final2-typelint.log`, `/tmp/telescreen-p19-hol-final2-typelint-scope.log`). Import boundary, Python size and plan checklist gates also exit 0.
