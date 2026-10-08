# P11 Police SDK, Examples (Python and n8n) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-police-contract.md (Push signing, Python SDK, Examples), design-docs/specs/design-map-reduce-sweeps.md (Police contract additions)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p11-police-sdk`
- planPath: `impl-plans/active/p11-police-sdk.md`
- wave: 3
- dependsOn: `p05-police-contract`
- writePaths:
  - `impl-plans/active/p11-police-sdk.md`
  - `src/telescreen_police_sdk/models.py`
  - `src/telescreen_police_sdk/_json.py`
  - `src/telescreen_police_sdk/signing.py`
  - `src/telescreen_police_sdk/client.py`
  - `src/telescreen_police_sdk/worker.py`
  - `src/telescreen_police_sdk/webhook.py`
  - `src/telescreen_police_sdk/asgi.py`
  - `src/telescreen_police_sdk/masking.py`
  - `src/telescreen_police_sdk/sweep.py`
  - `tests/police_sdk`
  - `tests/contract/test_sdk_contract.py`
  - `examples/police`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Police workers must be buildable without Python internals of the Bureau: with this SDK
(pull or push), with n8n, or with any HTTP client. The SDK is a separate top-level
package in the same wheel. It imports ONLY the stdlib and httpx, never `telescreen.*`
(contract tests may import `telescreen.contracts`). `__init__.py` and `py.typed` already
exist (p01); do not edit them.

## Non-goals

- No Bureau endpoints (p19). No change to the schemas (p05). No new dependencies.

## Deliverables

| File | Contract |
|------|----------|
| `_json.py` | A local closed JSON value model `Json` (same technique as `telescreen.core.json`: a self-referencing frozen dataclass; no recursive alias) with `parse(text: str \| bytes) -> Json`, `dump(value: Json) -> str` (canonical), constructors and accessors. If p01 added the pre-approved TL005 per-file-ignore for this path, you may use it. Otherwise use structural Protocols |
| `models.py` | Frozen dataclasses mirroring every schema: `Manifest`, `Registration`, `ClaimRequest`, `TaskEnvelope` (`input: Json`, `callback: Callback \| None`), `Callback(url, result_token: str = field(repr=False))`, `MaskedFinding`, `ResultError(code, detail, retry_after_seconds: float \| None)`, `ShardStats`, `ResultEnvelope` (`payload: Json \| None`, `stats: ShardStats \| None`, `complete_locators: tuple[str, ...]`), `Shard(shard_key, descriptor: Json, weight)`, `PartitionResult(shards, next_cursor, sub_corpora: tuple[str, ...])`. Each has `to_json() -> Json` and `@staticmethod from_json(value: Json) -> X`; optional members are omitted from JSON when None or empty. `CONTRACT_VERSION: str = "1.0"` |
| `signing.py` | `sign(secret: bytes, body: bytes, timestamp: int, nonce: str) -> str` returning `v1=<hex>` over `f"{timestamp}.{nonce}.".encode() + body`; `signature_headers(secret, body, now: int, nonce: str) -> tuple[tuple[str, str], ...]`; `VerifyResult` StrEnum (ok, bad_signature, stale, replay, missing); `verify(headers: Sequence[tuple[str, str]], body: bytes, secret: bytes, now: int, seen: NonceCache) -> VerifyResult` (300 s window, `hmac.compare_digest`); `NonceCache` dataclass (in memory, TTL 600 s) with `seen_or_add(nonce: str, now: int) -> bool` |
| `client.py` | `BureauClient(base_url: str, http: httpx.AsyncClient)` with `register(manifest, enrollment_token) -> Registration`, `claim(worker_id, token, request) -> tuple[TaskEnvelope, ...]`, `heartbeat(worker_id, token, task_ids) -> tuple[str, ...]` (the cancelled ids), `submit_result(token, result)`, `callback(url, result: ResultEnvelope, secret: bytes)` (signed headers). Non-2xx -> `BureauError(status, code)`, using the error schema's code only |
| `worker.py` | `type TaskHandler = Callable[[TaskEnvelope], Awaitable[ResultEnvelope]]` (PEP 695 statement; a plain assignment fails TL003); `PullWorker(client, worker_id, token, handlers: tuple[tuple[str, TaskHandler], ...], concurrency: int, heartbeat_interval: float, wait_seconds: int)` with `async run(stop: asyncio.Event) -> None`. The claim loop only claims free slots. A heartbeat loop sends the in-flight ids and cancels the asyncio tasks named in `cancelled_task_ids`. A handler exception -> a failed result with code `handler_error` and a static detail. Graceful stop: no new claims, then in-flight tasks finish |
| `webhook.py` | `WebhookRequest(headers: tuple[tuple[str, str], ...], body: bytes)`, `WebhookReply(status: int, headers: tuple[tuple[str, str], ...], body: bytes)`, `WebhookMode` (sync, async), `WebhookHandler(handlers, secret: bytes = field(repr=False), mode, client: BureauClient \| None, clock: Callable[[], int])` with `async handle(request) -> WebhookReply`. Invalid signature -> 401; a replayed nonce -> 401; sync mode -> 200 with the result JSON; async mode -> 202, then the handler runs in the background and posts a signed callback that includes `result_token` |
| `asgi.py` | `create_webhook_app(handler: WebhookHandler) -> AsgiApp` (POST only, else 405). ASGI scope, receive and send are typed with local structural Protocols. Outgoing messages are literal dicts passed inline to `send(...)`. If ty cannot accept this, record a Reconciliation request for an ASGI TL005 per-file-ignore. That ignore is a design-listed candidate but NOT pre-approved: it goes through serial reconciliation by the foundation owner and must be called out in review. Never add it yourself |
| `masking.py` | `mask_value(value: str, keep_prefix: int = 0) -> str` (prefix, then `*` for the rest capped at 16, then `[len]`); `external_fingerprint(value: str, key: bytes) -> str` (hex HMAC-SHA256) so police never send raw values |
| `sweep.py` | `ResourceOutcome(locator: str, findings: tuple[MaskedFinding, ...], completed: bool)`; `SweepHandler(partition_fn, scan_fn)` builds `TaskHandler`s for `partition` (returns `payload` = PartitionResult, max 1000 shards) and `scan_shard`. For `scan_shard`, every enumerated resource is accounted for: `completed=True` -> `complete_locators`; `completed=False` or an exception from the per-resource iterator -> counted in `stats.resources_failed` (design-map-reduce-sweeps.md, Police contract additions) |

Examples (`examples/police/`):

- `python/private_db_police.py`: a pull-mode police for `verify_remediation`.
  - It opens a local SQLite path from env `PRIVATE_DB_PATH`.
  - It reads `display_locator` in the form `sqlite://<table>/<column>` and validates the
    table and column names against `^[A-Za-z_][A-Za-z0-9_]*$` (never interpolate
    unchecked identifiers into SQL).
  - It scans values with small local regexes and reports masked findings
    (`mask_value`, `external_fingerprint`). Outcome `passed` when there are no findings.
  - Config comes from env: `TELESCREEN_URL`, `TELESCREEN_POLICE_ENROLLMENT_TOKEN`.
  - `python/README.md` covers enrollment, running and the env vars.
- `n8n/telescreen-push-police.json`: an importable n8n workflow.
  - Node chain: Webhook (POST, raw body) -> Crypto (HMAC-SHA256 of
    `timestamp.nonce.body` with the secret from an n8n credential or env placeholder) ->
    IF (signature matches) -> Respond to Webhook 202 (false branch: 401) -> Code (the
    check; a `scan_shard` variant documented) -> Crypto (sign the callback) -> HTTP
    Request POST to `{{$json.callback.url}}` with `result_token`.
  - Placeholders only (`https://telescreen.example.invalid`).
- `n8n/README.md`: import steps; registering with curl (manifest `mode: push`); where to
  store the signing secret; how signature verification works; how to answer
  `scan_shard` with stats and `complete_locators`.

## Key Pitfalls

- `grep -rn "import telescreen\b\|from telescreen\." src/telescreen_police_sdk` must
  print nothing (`telescreen_police_sdk` imports are fine).
- Signatures are computed over the exact raw body bytes; never re-serialize before
  verifying.
- Async webhook mode: keep a reference to background tasks (a set) so they are not
  garbage-collected. Callback failures are logged with status codes only.
- Never log task inputs, results or secrets.

## Tests

- `tests/police_sdk/`:
  - sign then verify -> ok; tampered body -> bad_signature; timestamp 301 s old -> stale;
    same nonce twice -> replay.
  - Models round-trip through JSON for every contract example file (read the examples
    via `importlib.resources` from `telescreen.contracts`; reading package data is allowed
    in tests only).
  - `PullWorker` against a `MockTransport` fake Bureau: claims 2 tasks with
    concurrency 2; a third claim waits for a free slot; a heartbeat returning a cancelled
    id cancels that task; results are submitted; a handler exception yields a failed
    result with code `handler_error`.
  - `WebhookHandler`: sync -> 200 and a valid result; async -> 202, then a signed
    callback is posted (captured by MockTransport, verified with `verify`); bad signature
    -> 401.
  - `create_webhook_app` driven through `httpx.ASGITransport` -> the same behavior; GET
    -> 405.
  - `SweepHandler`: 3 resources, one raising -> `complete_locators` has 2 entries and
    `resources_failed == 1`; a partition with 1200 shards -> error (max 1000).
  - n8n JSON: valid JSON; contains node types `n8n-nodes-base.webhook`, `.crypto`, `.if`,
    `.respondToWebhook`, `.code`, `.httpRequest`; every connection references an existing
    node; `find_leaks` on the file text is empty.
  - The private DB example: run it against a temp SQLite file with one table holding a
    sample email and a fake client -> outcome failed with a masked finding; the payload
    has no leak; an invalid table name -> failed result, no SQL executed.
- `tests/contract/test_sdk_contract.py`: every SDK model instance built in the tests
  serializes to JSON that `telescreen.contracts.validator.validate` accepts.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen_police_sdk tests/police_sdk tests/contract/test_sdk_contract.py examples/police/python"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/police_sdk tests/contract/test_sdk_contract.py 2>&1 | tee /tmp/telescreen-p11-police-sdk-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p11-police-sdk-typelint.log 2>&1; grep -E '^(src/telescreen_police_sdk|tests/police_sdk|tests/contract/test_sdk_contract.py)' /tmp/telescreen-p11-police-sdk-typelint.log   # prints nothing
grep -rn "from telescreen\.\|import telescreen$" src/telescreen_police_sdk   # prints nothing
```

## Completion Criteria

- [ ] All SDK modules delivered; no `telescreen.*` imports in the SDK
- [ ] Pull and push modes tested, including cancellation, replay and the async callback
- [ ] Python and n8n examples plus READMEs delivered, with no secrets
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
