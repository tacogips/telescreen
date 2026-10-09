# P11 Police SDK, Examples (Python and n8n) Implementation Plan

**Status**: In Progress (run A2 verification, session 340)
**Design Reference**: design-docs/specs/design-police-contract.md (Push signing, Python SDK, Examples), design-docs/specs/design-map-reduce-sweeps.md (Police contract additions)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

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

## Run A Resume (2026-10-09, session 338)

State: s319 recorded p11 as BLOCKED, but no cause is written in-tree. Dispatch:
`impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A1 (p19 in wave A2
depends on this plan through `tests/contract/test_bureau_contract.py`). Resume from the
in-tree code; keep every public name. Treat any failing gate in R0 as the probable block
cause and fix it first. Static gap review (s338 planning) found:

- [x] R0 Baseline: run the Verification block on the untouched tree; log exit codes to
  `/tmp/telescreen-p11-police-sdk-baseline-*.log` before any edit. Also confirm
  `pyproject.toml` has no `asgi.py` per-file-ignore (design: NOT pre-approved). Baseline
  scoped format, ruff, ty and 15 tests passed; whole typelint passed and no p11 findings
  appeared in its filter; ASGI ignore was absent.
- [x] R1 `webhook.py:WebhookHandler._run_and_callback`: catch `BureauError` and `httpx`
  errors from the callback and log them with the status code (or error class name) only;
  never let a background task end with an unretrieved exception. Test: async mode with a
  MockTransport returning 500 on the callback -> 202 reply, one warning log with no task
  input, no "exception was never retrieved". Covered in `test_async_webhook_logs_callback_failure_without_task_data`.
- [x] R2 `sweep.py:SweepHandler`: the design (`design-police-contract.md`, scan_shard) says
  a resource counts as failed "whenever its handler raises", and the Tests section expects
  "3 resources, one raising -> 2 `complete_locators`, `resources_failed == 1`". Replace
  `scan_fn` with `enumerate_fn: Callable[[Json], AsyncIterator[str]]` (yields locators) plus
  `scan_resource_fn: Callable[[str], Awaitable[ResourceOutcome]]`. A per-resource exception
  adds 1 to `resources_failed` and continues; an exception from `enumerate_fn` adds 1 and
  stops; the status stays `completed`. Use PEP 695 `type` statements for both aliases.
  Update the existing sweep test and add the 3-resource case; the 1200-shard case asserts
  error code `partition_too_large`. The three-resource test confirms both successful
  locators remain complete when the middle resource raises. A second scenario covers a
  `completed=False` resource followed by an enumeration exception and asserts two failures.
  Contract example comparison
  uses parsed JSON equality for equivalent integer/decimal number spellings.
- [x] R3 `tests/contract/test_sdk_contract.py`: round-trip EVERY contract example file
  (read via `importlib.resources` from `telescreen.contracts`): `from_json` then `to_json`
  equals the original after canonical `_json.dump`. Validate with
  `telescreen.contracts.validator.validate` every SDK model the tests build: TaskEnvelope,
  Registration, a failed ResultEnvelope, a scan_shard result with stats and
  `complete_locators`, the async callback body with `result_token`, and the heartbeat body.
- [x] R4 ASGI: add a bad-signature request through `create_webhook_app` -> 401. Replace
  `httpx.__dict__["ASGITransport"]` in `tests/police_sdk/test_sdk.py` with a normal
  import; if ty rejects the normal import, keep the workaround and add a one-line comment
  saying why. The direct `ASGITransport` constructor is rejected by ty's httpx stub; the
  documented dynamic lookup remains with an explanatory comment.
- [x] R5 Private DB example test: drive the example's handler through `PullWorker` with a
  MockTransport fake Bureau (as in the Tests section) and assert the submitted result is
  `failed` with a masked finding and no leak.
- [x] R6 Accepted deviations to record (no code change): `signing.verify` takes
  `tuple | list` headers; `BureauClient.callback` reads `time.time()` directly.
- [x] R7 Re-run the scoped Verification block; all p11 gates passed and log paths are
  recorded. The final full-tree typelint rerun passed with 250 files clean; an earlier
  concurrent-tree attempt had transient diagnostics recorded in the Progress Log.

## Completion Criteria

- [x] All SDK modules delivered; no `telescreen.*` imports in the SDK (`src/telescreen_police_sdk/`; import scan passed)
- [x] Pull and push modes tested, including cancellation, replay and the async callback (after R1-R5)
- [x] Python and n8n examples plus READMEs delivered, with no secrets (`examples/police/`)
- [x] Run A Resume items R0-R7 checked with evidence
- [x] Scoped verification passes on the final tree; logs recorded
- [x] Progress Log updated (run A session)

## Run A2 Verify (2026-10-09, session 340)

Code is complete; this pass verifies it against the current tree. Resume rule: fix only
real gaps or failing causes inside this plan's writePaths; never rewrite working code.

- [ ] V0 Run the Verification block on the current tree before any edit; log every
  command, exit status and `/tmp/telescreen-p11-police-sdk-a2-*.log` path.
- [ ] V1 Re-check Deliverables, Tests and items R1-R7 against the current source; list
  any gap in the Progress Log and fix it.
- [ ] V2 Push key (design D4, `design-police-contract.md` Push signing): the HMAC key is
  the UTF-8 bytes of the registration `signing_secret` string, never its base64 decode.
  State this in the `signing.py` module docstring and in `examples/police/n8n/README.md`
  if absent. Add a test in `tests/police_sdk/`: `models.Registration.signing_secret`
  string -> `.encode("utf-8")` -> `signature_headers` then `verify` -> valid; the same
  headers verified with `base64.urlsafe_b64decode(secret + padding)` as key -> invalid.
- [ ] V3 Confirm (no edit expected) `examples/police/n8n/README.md` still documents the
  push-mode `scan_shard` flow with count-only `stats` and `complete_locators`.
- [ ] V4 Re-run the Verification block on the final tree; every command exits 0. Report
  fixed items only in `addressedFeedback` (conventions 7a).

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09
**Tasks Completed**: Delivered the standalone pull/push Police SDK, schema-aligned models, sweep helpers, Python and n8n examples, and contract/behavior tests. `submit_result(token, result)` matches the plan contract. No `telescreen.*` imports exist in the SDK.
**Tasks In Progress**: Formal test-integrity, adversarial and combined-tree integration review are downstream workflow steps.
**Blockers**: None for p11 implementation.
**Verification**: Format check, ruff, ty, SDK-path typelint filter and no-import scan passed. `pytest tests/police_sdk tests/contract/test_sdk_contract.py`: 14 passed. Full-tree `tools/typelint.py` exited 1 on unrelated shared-tree diagnostics outside p11; complete log: `/tmp/telescreen-p11-police-sdk-typelint-final.log`.
**Evidence**: Final scoped logs `/tmp/telescreen-p11-police-sdk-format-final.log`, `/tmp/telescreen-p11-police-sdk-ruff-final.log`, `/tmp/telescreen-p11-police-sdk-ty-final.log`, `/tmp/telescreen-p11-police-sdk-pytest-final.log`, `/tmp/telescreen-p11-police-sdk-typelint-final.log`, `/tmp/telescreen-p11-police-sdk-imports-final.log`; plan-local edit intentions in `tmp/telescreen-v1-20261008-s319/p11-police-sdk/edits/`.

### Session: 2026-10-09 — P11-TI-WORKER-COVERAGE repair
**Tasks Completed**: Captured `ClaimRequest.max_tasks` in the PullWorker fake Bureau and asserted the initial two-slot request plus the free-slot bound for every observed claim. Added a raising-handler test that confirms a failed `handler_error` result uses static detail and excludes the raw sample from submitted bytes. Existing cancellation and `submitted == ["task-b"]` assertions remain unchanged.
**Tasks In Progress**: Formal test-integrity, adversarial and combined-tree integration review are downstream workflow steps.
**Blockers**: None for this repair.
**Verification**: Scoped format, ruff, ty, typelint (13 files clean), import scan, and combined SDK/contract pytest all exited 0; pytest reported 15 passed. The earlier format check exited 1 for assertion wrapping and was corrected before final gates. Full-tree typelint remains a known unrelated nonzero baseline from shared-tree paths.
**Evidence**: `/tmp/telescreen-p11-police-sdk-worker-coverage-focused.log`; `/tmp/telescreen-p11-police-sdk-format-ti-repair-final.log`; `/tmp/telescreen-p11-police-sdk-ruff-ti-repair-final.log`; `/tmp/telescreen-p11-police-sdk-ty-ti-repair-final.log`; `/tmp/telescreen-p11-police-sdk-typelint-ti-repair-final.log`; `/tmp/telescreen-p11-police-sdk-pytest-ti-repair-final.log`; `/tmp/telescreen-p11-police-sdk-imports-ti-repair-final.log`; intentions in `tmp/telescreen-v1-20261008-s319/p11-police-sdk/edits/`.

### Session: 2026-10-09 — free-slot wait correction
**Tasks Completed**: The strengthened test reproduced that the worker waited for every handler to finish before reclaiming a newly freed slot. Updated `_wait_for_activity` to wake on the first completed handler or shutdown, cancel and await its stop waiter, then let the claim loop recompute free slots. The test now observes a claim of one while `task-a` is still active, and retains the original `submitted == ["task-b"]` and cancellation checks.
**Tasks In Progress**: Formal test-integrity, adversarial and combined-tree integration review are downstream workflow steps.
**Blockers**: None for p11 implementation.
**Verification**: Final source format, ruff, ty, scoped typelint (13 files clean), import scan, and SDK/contract pytest exited 0; pytest reported 15 passed. The pre-fix reproduction failed on the expected free-slot assertion and is resolved by the source change.
**Evidence**: Reproduction `/tmp/telescreen-p11-police-sdk-worker-free-slot-reproduction.log`; focused post-fix test `/tmp/telescreen-p11-police-sdk-worker-coverage-post-fix.log`; final source logs `/tmp/telescreen-p11-police-sdk-format-ti-repair-source-final.log`, `/tmp/telescreen-p11-police-sdk-ruff-ti-repair-source-final.log`, `/tmp/telescreen-p11-police-sdk-ty-ti-repair-source-final.log`, `/tmp/telescreen-p11-police-sdk-typelint-ti-repair-source-final.log`, `/tmp/telescreen-p11-police-sdk-pytest-ti-repair-source-final.log`, `/tmp/telescreen-p11-police-sdk-imports-ti-repair-source-final.log`.

### Session: 2026-10-09 — Run A resume R0-R7
**Tasks Completed**: Added sanitized async callback error logging; changed sweep scanning to enumerate then scan each resource and continue after per-resource exceptions; expanded SDK contract example round trips and schema validation; added ASGI bad-signature and private-database PullWorker tests. Kept the direct ASGITransport workaround with a ty explanation. Confirmed R6 deviations: verify accepts tuple/list headers, callback uses `time.time()`.
**Tasks In Progress**: Formal test-integrity, adversarial and combined-tree integration review are downstream workflow steps.
**Blockers**: None for p11 implementation.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen_police_sdk/webhook.py` before=64668961a59f38f3fc68b1aa869ac2a5030b05029f061e551a073b36dfa607ba after=756d70e884e3eba9a0a851618b87784aad5a588ad05569e1c2fc6cda75b9c7fc; `src/telescreen_police_sdk/sweep.py` before=af4b8d48f27ad6ff490bf92afe17fea9a7b4f92c484a60efb034793172e6dc97 after=a97ebfea932a26d4c3bbd9cc9e44923c872998b847addadb55492702ad6c03de; `tests/police_sdk/test_sdk.py` before=bf0fb37aae13bbdc5c53a3b69aa530f7a3618a9da009527c7390af4ad417e78b after=971f1f3d0f591f9c2ccb74ec3936c968febdb35bc01f8ffca1db3eaf3aa5eea4; `tests/contract/test_sdk_contract.py` before=71ac7accb284d4b43e9746bb322cbb6992301d6380628c35d4372ac48614d266 after=fb46750ac7fdc7f1320a02ce86ffe4f901e2d77e61427b5b7cde144f4719570c.
**Verification**: Baseline scoped format, ruff, ty, pytest (15 passed), full typelint and p11 filter passed before edits (`/tmp/telescreen-p11-police-sdk-baseline-{format,ruff,ty,pytest,typelint}.log`). Final commands: `uv run --frozen ruff format --check --no-cache src/telescreen_police_sdk tests/police_sdk tests/contract/test_sdk_contract.py examples/police/python` -> 0; `uv run --frozen ruff check --no-cache ...` -> 0; `uv run --frozen ty check ...` -> 0; `uv run --frozen pytest -p no:cacheprovider tests/police_sdk tests/contract/test_sdk_contract.py` -> 0, 17 passed; standalone `uv run --frozen python tools/typelint.py` -> 0, 250 files clean; p11 typelint filter -> empty. Logs: `/tmp/telescreen-p11-police-sdk-{format,ruff,ty,pytest,typelint,typelint-scoped,imports,asgi-ignore}-final.log`. One concurrent-tree typelint attempt transiently found nine diagnostics in `tests/issues/test_issues_api.py`; a later current-tree standalone rerun passed.
**Notes**: Per-edit intentions are preserved in `tmp/telescreen-v1-20261009-s338/p11-police-sdk/edits/20261009-r1-r5.md`. No SDK imports from `telescreen.*`; no ASGI per-file-ignore. Downstream formal review and serial finalization remain owned by later workflow steps.

### Session: 2026-10-09 — TI-P11-01 repair (comm-005570)
**Tasks Completed**: Restored explicit sweep accounting coverage for one `completed=False` resource followed by an enumeration exception after a successful resource. The result remains COMPLETED, contains only `complete-a`, counts two failures, and omits the private exception message. Existing 3-resource and `partition_too_large` assertions are unchanged.
**Tasks In Progress**: Independent test-integrity re-review and downstream adversarial/integration review.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/police_sdk/test_sdk.py` before=971f1f3d0f591f9c2ccb74ec3936c968febdb35bc01f8ffca1db3eaf3aa5eea4 after=d5326ba766405964fd98cc4b25e7e21880c72b70426944b4be13de8e1be5d443. Source `sweep.py` remained before=after=a97ebfea932a26d4c3bbd9cc9e44923c872998b847addadb55492702ad6c03de.
**Verification**: Focused sweep test passed (1 passed; `/tmp/telescreen-p11-police-sdk-ti01-focused.log`). Mutation check injected an in-memory behavior equivalent to omitting the `completed=False` failure increment; test failed at the expected `ShardStats(1, 2, 0)` assertion (exit 1, `/tmp/telescreen-p11-police-sdk-ti01-mutation.log`). `src/telescreen_police_sdk/sweep.py` remained unchanged at sha256 `a97ebfea932a26d4c3bbd9cc9e44923c872998b847addadb55492702ad6c03de`; repair constraints restricted edits to this test and the plan, so no source-file mutation was performed. Final scoped format, Ruff, ty and typelint filter all exited 0 (`/tmp/telescreen-p11-police-sdk-{format,ruff,ty,typelint}-ti01.log`). Focused SDK/contract pytest exited 0 with 17 passed and zero failures (`/tmp/telescreen-p11-police-sdk-pytest-ti01.log`).
**Notes**: The full workflow review finding TI-P11-01 is addressed; independent review must re-check the added coverage and mutation evidence before acceptance. Intentions: `tmp/telescreen-v1-20261009-s338/p11-police-sdk/edits/20261009-ti-p11-01.md`.
