# Police Contract v1

Versioned, language-agnostic HTTP+JSON contract between the Reporting Bureau and Police
workers (custom detectors and verifiers), with pull and push transports, a standalone
Python SDK and an n8n example.

## Overview

Police and Bureau share only the published contract: an OpenAPI document plus JSON Schemas
in `src/telescreen/contracts/police/v1/`, served at `GET /api/police/v1/openapi.json`. The
Bureau never imports police code; the SDK never imports `telescreen.*`. Each police
registration selects one mode:

- **pull** (Kestra-worker style): the police connects outbound, registers, long-poll claims
  tasks by type and labels, heartbeats to extend leases, and submits results.
- **push** (webhook): the Bureau POSTs a signed task envelope to the police endpoint; the
  police answers synchronously (200 with a result) or later (202, then a signed callback with
  a one-time result token). Implementable with stock n8n nodes.

Inside the Bureau a `PoliceGateway` port hides transports from the workflow engine.

## Details

### Contract files (`src/telescreen/contracts/police/v1/`)

| File | Content |
|------|---------|
| `openapi.json` | OpenAPI 3.1 document for every endpoint below, `$ref`-ing the schemas |
| `manifest.schema.json` | Registration manifest |
| `registration.schema.json` | Registration response |
| `claim-request.schema.json`, `claim-response.schema.json` | Pull claim |
| `heartbeat-request.schema.json`, `heartbeat-response.schema.json` | Pull heartbeat |
| `task-envelope.schema.json` | Task delivered by claim or push |
| `result-envelope.schema.json` | Result submitted by pull, sync push response, or callback |
| `masked-finding.schema.json` | Finding shape inside results |
| `error.schema.json` | `{error: {code, detail}}` |
| `examples/*.json` | One valid example per schema (used by contract tests and the n8n README) |

`telescreen.contracts` provides `load_schema(name)` and `validate(name, raw: bytes) ->
list[SchemaError]` (jsonschema Draft 2020-12; errors carry JSON path and keyword only,
never instance values).

Versioning: URL major version (`/api/police/v1`), `contract_version` field `"1.<minor>"`
in every envelope. Minor versions only add optional fields; breaking changes require `v2`
paths served side by side. The Bureau rejects manifests whose major version it does not
serve (`unsupported_contract_version`).

### Envelopes

- Manifest: `name`, `version`, `contract_version`, `mode` (`pull`|`push`), `task_types`
  (`detect`, `verify_remediation`, `partition`, `scan_shard`, `custom:<name>`), `labels` (list of `key=value`),
  `concurrency` (1-64), `endpoint_url` (push only, https unless
  `TELESCREEN_POLICE_ALLOW_HTTP=true` for dev).
- Registration response: `worker_id`, `worker_token` (pull) or `signing_secret` (push),
  `lease_seconds` (60), `heartbeat_interval_seconds` (20), `max_wait_seconds` (30).
- Task envelope: `contract_version`, `task_id`, `task_type`, `attempt`, `issued_at`,
  `deadline`, `labels`, `input`, `callback` (push only: `url`, `result_token`).
- Inputs by task type:
  - `verify_remediation`: `issue` (`id`, `short_id`, `source`, `display_locator`,
    `rule_class`, `severity`, `first_seen`, `last_seen`), `instructions` (masked text).
  - `detect`: `selector` (the `police` collector config selector document), `collector_config_id`.
  - `partition`, `scan_shard`: map-reduce sweep tasks; inputs, results and the
    `partition-result.schema.json` schema are specified in `design-map-reduce-sweeps.md`.
  - `custom:<name>`: `payload` (free JSON object, built from masked workflow context).
- Result envelope: `contract_version`, `task_id`, `status` (`completed`|`failed`),
  `outcome` (`passed`|`failed`, required for `verify_remediation`), `summary` (max 2000
  chars), `findings` (masked findings: `locator`, `rule_id`, `rule_class`,
  `masked_snippet`, `location`, `confidence`, `external_fingerprint`), `payload` (custom
  only), `error` (`code`, `detail`) when failed, `result_token` (callback only).

### Endpoints (`/api/police/v1`)

| Endpoint | Auth | Behavior |
|----------|------|----------|
| `GET /openapi.json` | none | Contract document |
| `POST /workers/register` | enrollment token (`TELESCREEN_POLICE_ENROLLMENT_TOKEN` secret ref) | Validates manifest, creates `police_workers` row, returns credentials once; audited |
| `POST /workers/{id}/claim` | worker token | Body `{task_types?, max_tasks (1-concurrency), wait_seconds (0-30)}`; long-poll: re-checks the queue every second until tasks exist or wait ends; returns `{tasks: [...]}` (possibly empty). Matching: task type in manifest and task labels subset of worker labels |
| `POST /workers/{id}/heartbeat` | worker token | Body `{task_ids}`; extends leases of tasks leased to this worker; returns `{cancelled_task_ids}`; updates `last_heartbeat_at` |
| `POST /tasks/{task_id}/result` | worker token | Pull result; task must be leased to the worker |
| `POST /callbacks/{task_id}` | push signature + one-time `result_token` | Push asynchronous result |

All inbound bodies are read raw, size-limited (1 MiB), schema-validated with
`telescreen.contracts.validate`, then parsed into pydantic dataclasses. Free-form members
(`input`, `payload`) stay native JSON objects on the wire; their encoding and decoding follow
"Free-form JSON on the wire" in `design-api-and-ui.md`.

### Push signing

- Secret: `signing_secret = base64url(HMAC-SHA256(TELESCREEN_POLICE_SIGNING_KEY,
  "police-push\0" + worker_id))`. It is derived on demand and never stored; rotating the
  master key rotates all push secrets (police must re-register).
- Headers on Bureau -> police deliveries and on police -> Bureau callbacks:
  `X-Telescreen-Timestamp` (unix seconds), `X-Telescreen-Nonce` (32 hex chars),
  `X-Telescreen-Signature: v1=<hex HMAC-SHA256(secret, timestamp + "." + nonce + "." +
  raw_body)>`.
- Verification (both sides): timestamp within +-300 s; nonce not seen in the replay window
  (`replay_nonces`, scope `police-callback:<worker_id>`); constant-time comparison.
- Callback token: 32 random bytes base64url per delivery; SHA-256 stored in
  `police_tasks.callback_token_hash`; accepted once (`callback_token_used_at` set in the
  same transaction as the result) and only before `deadline`.
- Synchronous answer: a 200 response to the delivery must be a valid result envelope and is
  accepted without callback (the Bureau initiated the TLS connection to the registered
  endpoint). 202 means "callback later". Other statuses or network errors are retried with
  backoff (`police_push_delivery` engine task) until `max_attempts` (default 5) or deadline.

### Bureau side (`telescreen.police`)

```python
class PoliceGateway(Protocol):
    async def dispatch(self, request: PoliceTaskRequest) -> str: ...   # returns task_id
    async def cancel(self, task_id: str) -> None: ...
```

- `RoutingPoliceGateway` creates the `police_tasks` row and picks a transport: if an active
  push worker matches type and labels, `WebhookTransport` (round-robin by
  `last_delivery_at`) enqueues delivery; otherwise `PullQueueTransport` leaves the task
  `queued` for any matching pull worker. New transports implement the same internal
  `PoliceTransport` protocol without engine changes.
- Leases: claim sets `leased` with `lease_expires_at = now + lease_seconds`; expired leases
  return to `queued` with `attempt + 1` until `max_attempts` (3), then `failed`.
- Deadline: tasks past `deadline` become `expired`; the engine's `police_timeout` resumes
  the step with outcome `error`.
- Result acceptance: schema validation, task state check, `Redactor` scan of every string
  field (any hit rejects with 422 `raw_value_detected`, task stays open until deadline,
  audited), findings of `detect` tasks recorded through `IssueRecorder.record_external`
  (which re-keys external fingerprints; one resource per finding `locator`, `complete=true`
  per locator listed in the result), and `RunSignaler.signal(step_run_id, police_result)`
  for workflow tasks.
- Admin API outside the contract: `GET /api/police/workers` (status, labels, heartbeat age),
  `POST /api/police/workers/{id}/disable`.

### Python SDK (`src/telescreen_police_sdk`)

| Module | Content |
|--------|---------|
| `models.py` | Frozen dataclasses mirroring every schema; `to_json()`/`from_json()` |
| `_json.py` | JSON boundary (same technique and per-file-ignore rule as `telescreen.core.json`) |
| `signing.py` | `sign(secret, body, now, nonce)`, `verify(headers, body, secret, now, nonce_seen)` |
| `client.py` | `BureauClient` (httpx.AsyncClient): register, claim, heartbeat, submit_result, callback |
| `worker.py` | `PullWorker`: handler registry by task type, concurrency semaphore, heartbeat loop, graceful stop |
| `webhook.py` | `WebhookHandler(handlers, secret, mode=sync|async)` with `handle(WebhookRequest(headers: tuple[tuple[str, str], ...], body: bytes)) -> WebhookReply(status, headers, body)`. It is framework-neutral: it verifies signatures, dispatches to handlers, replies 200/202 and posts signed callbacks. Unit tests call it directly |
| `asgi.py` | `create_webhook_app(handler) -> AsgiApp`: a thin pure-ASGI adapter over `WebhookHandler`. ASGI scope, receive and send are typed with structural Protocols local to this file. If ty cannot accept dict-based ASGI messages through those Protocols, this single file is the third pre-approved `TL005` per-file-ignore candidate (the ASGI specification mandates dict scopes and messages), added through foundation reconciliation |
| `masking.py` | `mask_value(value, keep_prefix)` helper so police never send raw values |

Dependencies: stdlib and httpx only. Tests use httpx MockTransport and an in-process ASGI
call. Contract tests (`tests/contract/`) validate SDK-serialized envelopes and Bureau router
responses against the same schema files and the shared examples.

### Examples

- `examples/police/python/private_db_police.py`: pull-mode police that handles
  `verify_remediation` by querying a local SQLite database (path from env) for the table and
  column named in `display_locator` (`sqlite://<table>/<column>`), checks values locally with
  its own regexes, and reports masked findings. README explains enrollment and running.
- `examples/police/n8n/telescreen-push-police.json`: importable workflow: Webhook trigger
  (POST, raw body) -> Crypto (HMAC-SHA256 of `timestamp.nonce.body`) -> IF signature valid
  -> Respond to Webhook 202 -> Code/HTTP nodes performing the check -> Crypto (sign callback)
  -> HTTP Request POST to `callback.url` with `result_token`. Invalid signatures get 401.
  `examples/police/n8n/README.md` documents import, credentials as n8n credentials or
  environment placeholders, registration with curl, and signature verification.

## References

See `design-docs/references/README.md`.
