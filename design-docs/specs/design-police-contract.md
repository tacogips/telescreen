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
    A `scan_shard` result must account for every resource it enumerated: each one is
    either in `complete_locators` (fully scanned) or counted in `stats.resources_failed`
    (enumerated but not fully scanned). A resource in neither place, while
    `resources_failed = 0`, is treated as removed. Any `resources_failed > 0` blocks
    removal reconciliation for the whole sweep. The SDK `SweepHandler` counts a resource
    as failed whenever its handler raises.
  - `custom:<name>`: `payload` (free JSON object, built from masked workflow context).
- Result envelope: `contract_version`, `task_id`, `status` (`completed`|`failed`),
  `outcome` (`passed`|`failed`, required for `verify_remediation`), `summary` (max 2000
  chars), `findings` (masked findings: `locator`, `rule_id`, `rule_class`,
  `masked_snippet`, `location`, `confidence`, `external_fingerprint`), `payload` (custom
  only), `error` (`code`, `detail`, optional `retry_after_seconds` used with code
  `rate_limited`) when failed, `result_token` (callback only).

### Contract 1.1 (v1.1, exposure)

Minor version `1.1` adds one optional member to `result-envelope.schema.json`, through a
new `$ref`-ed schema file `resource-exposure.schema.json`:

```json
"resource_exposures": {
  "type": "array", "maxItems": 1000,
  "items": {"$ref": "resource-exposure.schema.json"}
}
```

`resource-exposure.schema.json` is a closed object (`additionalProperties: false`):

| Member | Type | Required |
|--------|------|----------|
| `locator` | string, max 1024 | yes |
| `visibility` | enum `public`, `anyone_with_link`, `external_shared`, `domain`, `internal`, `private`, `unknown` | yes |
| `external_principal_count` | integer >= 0 | no |
| `shared_drive`, `is_fork` | boolean | no |
| `channel_kind` | enum `public`, `private`, `dm`, `connect` | no |
| `repo_visibility` | enum `public`, `internal`, `private` | no |

Rules:

- `contract_version` keeps the pattern `^1\.[0-9]+$`. The Bureau accepts 1.0 and 1.1
  envelopes against the same schema files. A 1.0 worker never sends the member, so existing
  workers keep working unchanged.
- The member is accepted on `detect` and `scan_shard` results and ignored on the other task
  types.
- Each `locator` must also appear as a finding `locator` or in `complete_locators`.
  Entries for other locators are dropped, and the drop is counted in the audit details.
- Every `locator` is scanned by the `Redactor` like the other string fields. Any hit
  rejects the result with 422 `raw_value_detected`.
- Accepted entries become `ExternalScan.exposures` and are applied per resource as in
  `design-v1-1-dspm-adoption.md` section 3. Locators without an entry get visibility
  `unknown`, which never overwrites a known value.
- `openapi.json` adds the schema reference, and `examples/` gains
  `result-envelope-1.1.json` with exposures.
- SDK: `models.py` gains a frozen `ResourceExposure` dataclass and
  `ResultEnvelope.resource_exposures: tuple[ResourceExposure, ...] = ()`, serialized only
  when non-empty. `CONTRACT_VERSION` becomes `"1.1"`. `SweepHandler` and `PullWorker`
  handlers may return exposures. The SDK still imports only stdlib and httpx.
- Contract tests validate SDK-serialized 1.0 and 1.1 envelopes and the Bureau's acceptance
  of both against the same schema files.
- Heartbeat semantics (v1.1): a claim also updates `last_heartbeat_at`, even when it
  returns no tasks. A long-poll claim is a liveness signal, and the `police_silent` health
  condition relies on it (`design-v1-1-dspm-adoption.md` section 8).

### Contract 1.2 (v1.4, data inventory)

Status: designed 2026-10-13 (`design-v1-4-data-inventory.md` section 6.4). Minor version
`1.2` adds one optional member to `result-envelope.schema.json`, `resource_inventories`
(at most 1000 entries). Its items are `$ref`s to a new closed schema,
`resource-inventory.schema.json`:

| Member | Type | Required |
|--------|------|----------|
| `locator` | string, max 1024 | yes |
| `item_fingerprints` | array (max 10000) of `^[0-9a-f]{64}$` | no |
| `item_count` | integer >= 0 | no |
| `kind` | enum `file`, `table`, `list`, `channel`, `object`, `folder`, `other` | no |

Rules:

- `item_fingerprints` and `item_count` are mutually exclusive.
- Accepted on `detect` and `scan_shard`; ignored on other task types.
- Each locator must be a finding locator or in `complete_locators`. Other entries are
  dropped, and the count goes in the audit details.
- The `Redactor` rule applies to every string.
- The Bureau re-keys each fingerprint with its item key, so workers never send raw item
  keys.
- 1.0 and 1.1 workers are unaffected.
- SDK: `ResourceInventory`, `ResultEnvelope.resource_inventories` (serialized only when
  non-empty), `masking.item_fingerprint(item_key, key)` (HMAC-SHA256 hex with the worker's stable
  secret, like `external_fingerprint`) and `CONTRACT_VERSION =
  "1.2"`.
- `examples/result-envelope-1.2.json` is added. Contract tests cover the 1.0, 1.1 and 1.2
  envelopes.

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
  master key rotates all push secrets (police must re-register). Both sides use the UTF-8
  bytes of the `signing_secret` string itself as the HMAC key (it is not base64-decoded),
  so the SDK, the Bureau and an n8n Crypto node holding the string compute the same value.
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
  backoff (`police_push_delivery` engine task) until the task's `max_attempts` or deadline.

### Bureau side (`telescreen.police`)

```python
class PoliceGateway(Protocol):
    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str: ...  # task_id
    async def cancel(self, session: AsyncSession, task_id: str) -> None: ...
```

- `RoutingPoliceGateway` creates the `police_tasks` row and picks a transport: if an active
  push worker matches type and labels, `WebhookTransport` (round-robin by
  `last_delivery_at`) enqueues delivery; otherwise `PullQueueTransport` leaves the task
  `queued` for any matching pull worker. New transports implement the same internal
  `PoliceTransport` protocol without engine changes.
- Attempts: `dispatch` stores `attempt = 0` and `max_attempts` from
  `PoliceTaskRequest.max_attempts` (default 3). `attempt` is incremented exactly once per
  pull claim or push delivery attempt, never on re-queue, and the same `max_attempts`
  bounds both pull leases and push deliveries.
- Leases: claim sets `leased` with `lease_expires_at = now + lease_seconds`; expired leases
  return to `queued` (worker cleared) while `attempt < max_attempts`, then `failed`.
- Deadline: tasks past `deadline` become `expired`; the engine's `police_timeout` resumes
  the step with outcome `error`.
- Result acceptance: schema validation, task state check, `Redactor` scan of every string
  field (any hit rejects with 422 `raw_value_detected`, task stays open until deadline,
  audited), findings of `detect` tasks recorded through `IssueRecorder.record_external`
  (which re-keys external fingerprints; one resource per finding `locator`, `complete=true`
  per locator listed in the result), and `RunSignaler.signal(step_run_id, police_result)`
  for workflow tasks. `partition` and `scan_shard` results, their failures and their expiry
  are handed to the `MapResultSink` port (`design-map-reduce-sweeps.md`). For `scan_shard`,
  findings are recorded first through `record_external` with the task's
  `collector_config_id`.
- Every `police_tasks` row created for a sweep or a `police` collector carries
  `collector_config_id`, so the external issues it records belong to that config and take
  part in removal reconciliation.
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
| `asgi.py` | `create_webhook_app(handler) -> AsgiApp`: a thin pure-ASGI adapter over `WebhookHandler`. ASGI scope, receive and send are typed with structural Protocols local to this file, with no per-file-ignore. An ASGI `TL005` ignore is not pre-approved; it would need serial reconciliation through the foundation owner and a review call-out (`architecture.md`, Strict Typing Boundary Decisions) |
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
