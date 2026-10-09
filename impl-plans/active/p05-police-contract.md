# P05 Police Contract v1 (OpenAPI + JSON Schemas) Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-police-contract.md (Contract files, Envelopes, Endpoints, Versioning), design-docs/specs/design-map-reduce-sweeps.md (Police contract additions)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p05-police-contract`
- planPath: `impl-plans/active/p05-police-contract.md`
- wave: 2
- dependsOn: `p01-foundation`
- writePaths:
  - `impl-plans/active/p05-police-contract.md`
  - `src/telescreen/contracts`
  - `tests/contract/test_schemas.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The Bureau and Police share ONLY this language-agnostic contract. The Bureau routers
(p19), the SDK (p11) and the n8n example (p11) all validate against these files. The
files are package data inside the wheel and are served at
`GET /api/police/v1/openapi.json` (by p19).

## Non-goals

- No HTTP endpoints (p19), no SDK (p11).

## Deliverables

### `src/telescreen/contracts/__init__.py`
Docstring only.

### `src/telescreen/contracts/validator.py`
- `SchemaError(path: str, keyword: str)` (frozen). `path` is a JSON pointer such as
  `/findings/0/rule_class`.
- `load_schema(name: str) -> str`: returns the text of
  `contracts/police/v1/<name>` via `importlib.resources.files("telescreen.contracts")`.
  Unknown name -> `TelescreenError(not_found)`.
- `validate(name: str, raw: bytes) -> list[SchemaError]`: invalid JSON -> one
  `SchemaError("", "json")`. Otherwise Draft 2020-12 validation with a `referencing`
  registry that contains every schema in the directory, so cross-file `$ref` works.
  Errors carry only the path and the failing keyword, NEVER the instance value or the
  message.
- `openapi_document() -> str`: text of `openapi.json`.
- Typing pitfall: never bind `json.loads(...)` results to variables. Pass them inline,
  for example `Draft202012Validator(json.loads(text), registry=...)`, and
  `validator.iter_errors(json.loads(raw))` inline. Build the registry with
  `Registry().with_resources([...])`, where each resource is built inline from
  `Resource.from_contents(json.loads(text))`.

### `src/telescreen/contracts/police/v1/` (no `__init__.py` needed)

Every schema has `"$schema": "https://json-schema.org/draft/2020-12/schema"` and
`"$id": "https://schemas.telescreen.invalid/police/v1/<file>"`. Cross-references use the
relative file name. All object schemas set `additionalProperties: false` (v1.0 is strict;
minor versions add optional properties). `contract_version` uses the pattern
`^1\.[0-9]+$`.

| File | Required members (types) |
|------|--------------------------|
| `manifest.schema.json` | `name` (string 1-100), `version` (string), `contract_version`, `mode` (`pull`/`push`), `task_types` (array of strings matching `^(detect|verify_remediation|partition|scan_shard|custom:[a-z0-9_-]{1,64})$`, min 1), `labels` (array of `^[a-z0-9_.-]+=[A-Za-z0-9_.:/-]+$`), `concurrency` (int 1-64); optional `endpoint_url` (uri). Rule: `if mode == push then endpoint_url required` (an `if/then` block) |
| `registration.schema.json` | `worker_id`, `lease_seconds`, `heartbeat_interval_seconds`, `max_wait_seconds`; optional `worker_token` (pull) and `signing_secret` (push) |
| `claim-request.schema.json` | `max_tasks` (1-64), `wait_seconds` (0-30); optional `task_types` |
| `claim-response.schema.json` | `tasks` (array of `task-envelope.schema.json`) |
| `heartbeat-request.schema.json` | `task_ids` (array of strings) |
| `heartbeat-response.schema.json` | `cancelled_task_ids` (array of strings) |
| `task-envelope.schema.json` | `contract_version`, `task_id`, `task_type`, `attempt` (int >= 1), `issued_at` and `deadline` (date-time), `labels`, `input` (object); optional `callback` {`url`, `result_token`} |
| `result-envelope.schema.json` | `contract_version`, `task_id`, `status` (`completed`/`failed`); optional `outcome` (`passed`/`failed`), `summary` (max 2000), `findings` (array of `masked-finding`), `payload` (object), `error` {`code`, `detail`, optional `retry_after_seconds` number >= 0}, `result_token`, `stats` {`resources_scanned`, `resources_failed`, `bytes_scanned`: int >= 0}, `complete_locators` (array of strings, max 1000). Rule: `if status == failed then error required` |
| `masked-finding.schema.json` | `locator` (max 1000), `rule_id`, `rule_class` (enum of the 9 `RuleClass` values), `masked_snippet` (max 200), `location` (max 200), `confidence` (0-1), `external_fingerprint` (string 16-128) |
| `partition-result.schema.json` | `shards` (max 1000 items of {`shard_key` (1-200), `descriptor` (object), `weight` (int >= 1)}), `next_cursor` (string or null), `sub_corpora` (array of {`corpus_key`}) |
| `error.schema.json` | `error` {`code`, `detail`} |
| `openapi.json` | OpenAPI 3.1.0 document. Info title "Telescreen Police Contract", version "1.0". Paths: `POST /workers/register`, `POST /workers/{worker_id}/claim`, `POST /workers/{worker_id}/heartbeat`, `POST /tasks/{task_id}/result`, `POST /callbacks/{task_id}`, `GET /openapi.json`. Server url `/api/police/v1`. Schemas via `$ref` to the files above. Security schemes: bearer (enrollment and worker token) and the push signature headers `X-Telescreen-Timestamp`, `X-Telescreen-Nonce`, `X-Telescreen-Signature` documented as header parameters on `/callbacks/{task_id}` |
| `examples/<schema-stem>.json` | one valid example per schema (manifest pull, manifest push, registration, claim request and response, heartbeat request and response, task envelope with callback, result for verify_remediation, result for scan_shard with stats and complete_locators, failed result with rate_limited, masked finding, partition result, error) |

Example contents must use placeholder values only (`example.invalid` hosts, masked
snippets such as `ghp_****************[40]`, fingerprints of hex zeros). Never a real-looking
secret.

## Key Pitfalls

- Hatch includes non-Python files inside the package directory. Confirm with
  `uv build --out-dir /tmp/...` and `unzip -l` that the JSON files are in the wheel.
- `importlib.resources.files(...)` returns a Traversable; read it with `.read_text("utf-8")`.
- Validation of `custom:<name>` payloads is free-form `object` in v1.

## Tests (`tests/contract/test_schemas.py`)

- Every `*.schema.json` is a valid Draft 2020-12 schema (`check_schema`).
- Every file in `examples/` validates against its schema (map the example stem to the
  schema file) -> `[]`.
- A manifest with `mode: push` and no `endpoint_url` -> an error with keyword `required`.
- A result with `status: failed` and no `error` -> an error.
- A result whose masked snippet is 201 characters -> keyword `maxLength`, and the
  rendered errors do not contain the snippet text.
- A task envelope whose `contract_version` is `2.0` -> keyword `pattern`.
- `validate("result-envelope.schema.json", b"{bad")` -> `[SchemaError("", "json")]`.
- `openapi.json` parses; every `$ref` target file exists.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/contracts tests/contract/test_schemas.py
uv run --frozen ruff check --no-cache src/telescreen/contracts tests/contract/test_schemas.py
uv run --frozen ty check src/telescreen/contracts tests/contract/test_schemas.py
uv run --frozen pytest -p no:cacheprovider tests/contract/test_schemas.py 2>&1 | tee /tmp/telescreen-p05-police-contract-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p05-police-contract-typelint.log 2>&1; grep -E '^(src/telescreen/contracts|tests/contract/test_schemas.py)' /tmp/telescreen-p05-police-contract-typelint.log   # prints nothing
uv build --out-dir /tmp/telescreen-p05-police-contract-dist && unzip -l /tmp/telescreen-p05-police-contract-dist/*.whl | grep -c 'contracts/police/v1/.*\.json'   # >= 26
```

## Completion Criteria

- [x] All schema files, examples and openapi.json exist and validate
- [x] `validate` never exposes instance values
- [x] The wheel contains the contract files
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
### Session: 2026-10-08 20:04
**Tasks Completed**: Added 11 strict JSON Schemas, 14 placeholder-only examples, the OpenAPI 3.1 document, resource loader, value-safe validator, and six contract tests.
**Tasks In Progress**: Resolved by the 2026-10-09 repair verification below.
**Blockers**: Resolved: the owner ruling below authorizes the `/tmp` wheel output under conventions section 7, and the wheel gate passed.
**Owner resolution (2026-10-08)**: Not a blocker. `/tmp/telescreen-p05-police-contract-dist` is
outside the repository and outside fanout change tracking (conventions section 7). Run the
exact wheel build and `unzip -l` count gate as written; no checkpoint amendment is needed.
**Reconciliation requests**: None. The wheel gate passed using the owner-resolved `/tmp` output location.
**Hashes**: `impl-plans/active/p05-police-contract.md` before=2495aa14174ae8a89cec5716a467deee8a22d545c7b66be92b5f042a70d337cf after=recorded in `tmp/telescreen-v1-20261008-s319/p05-police-contract/plan-hash.sha256`;
**Source hash**: `src/telescreen/contracts/__init__.py before=absent after=8be77d2c51e7b1216750fc18756b5623e8ff09b75e66a71995b06d0ea2e628dc`
**Source hash**: `src/telescreen/contracts/police/v1/claim-request.schema.json before=absent after=873c93475e31c0dc7955fc350d988c42344a3fd621790e0c44a15f6a4737f320`
**Source hash**: `src/telescreen/contracts/police/v1/claim-response.schema.json before=absent after=a7e725c10ceca7d41465a56f0ac11a90e9893b054273b81a764a37299730a7a6`
**Source hash**: `src/telescreen/contracts/police/v1/error.schema.json before=absent after=7341e48727d59bebe0feba1dce71aeed8c7d4915fdbe4ca2a288c306484a5ada`
**Source hash**: `src/telescreen/contracts/police/v1/examples/claim-request.json before=absent after=8faec2d8536b537ad403d8eb100a309c829a26316a30536d0899fc989d1a5045`
**Source hash**: `src/telescreen/contracts/police/v1/examples/claim-response.json before=absent after=3b484097e54c399abc3b3693b4ca6b826f6958e9d2145c6639b73b764ebf11bd`
**Source hash**: `src/telescreen/contracts/police/v1/examples/error.json before=absent after=01a4c1b2ebc27646264114441938d648d0e73969a867671e53a5c941f02e807c`
**Source hash**: `src/telescreen/contracts/police/v1/examples/heartbeat-request.json before=absent after=7268bbcd6ac52e422d930f0d11d4b2562a9e1424c19954e875b0b56384187173`
**Source hash**: `src/telescreen/contracts/police/v1/examples/heartbeat-response.json before=absent after=db64b2471235d61c21196fe29119e53af5cb179568af2c29cfff3cac34be6b98`
**Source hash**: `src/telescreen/contracts/police/v1/examples/manifest-pull.json before=absent after=aceff181e27adc66f39dcb1c823d8494b4b37c7f85746ade176bbd6b0cf2616b`
**Source hash**: `src/telescreen/contracts/police/v1/examples/manifest-push.json before=absent after=82a6e259c21a42dd0be90a27eaa121ce4376bd4ddace817b554d4868ac0f4560`
**Source hash**: `src/telescreen/contracts/police/v1/examples/masked-finding.json before=absent after=8fb1194fb235259ae214bdbef04d3e5d590b39e8323012ab4854f644f2afabdd`
**Source hash**: `src/telescreen/contracts/police/v1/examples/partition-result.json before=absent after=02808886146c3e3ecaef1906c014d336fa552f50b5c1133f3913e7230e0cb3a3`
**Source hash**: `src/telescreen/contracts/police/v1/examples/registration.json before=absent after=bdc1ae740b67e61de97b8390f7a7b004ed78d73f1470a924ed201996ab46ccf3`
**Source hash**: `src/telescreen/contracts/police/v1/examples/result-envelope-failed.json before=absent after=e6510ce580e40278cab1f2416cf0d827889f9d8d4033ba874ae980aa09089df5`
**Source hash**: `src/telescreen/contracts/police/v1/examples/result-envelope-scan-shard.json before=absent after=9e21a55a922c6a2bec57fab62078728eed9a75becb779f627ce3a73a3aea5941`
**Source hash**: `src/telescreen/contracts/police/v1/examples/result-envelope.json before=absent after=2d7ca12028023e851d0024c0dbc381b7d2e3b44fa6f83a03df4554ea72dd9218`
**Source hash**: `src/telescreen/contracts/police/v1/examples/task-envelope.json before=absent after=5655e0de2d3c4fe8a56ecfd590fe1262d051bad5e2da51c664e47adcbae79b00`
**Source hash**: `src/telescreen/contracts/police/v1/heartbeat-request.schema.json before=absent after=d23792a2eefe0a9330eab0c68bcc2b91f1c68d0be6d1b813ee301793b09102f1`
**Source hash**: `src/telescreen/contracts/police/v1/heartbeat-response.schema.json before=absent after=4d9d0a54ec2f8ac20b91a3fe0fad08e7e2ee1b8fd3167e19063485d3471ede8d`
**Source hash**: `src/telescreen/contracts/police/v1/manifest.schema.json before=absent after=36f8590a453e8aaec7ecd5c23b2fba13180ff6ead3642088dd0b7e37f109f224`
**Source hash**: `src/telescreen/contracts/police/v1/masked-finding.schema.json before=absent after=c22b2737f4b41ac8f50bc506e1c53f750aa0f5e57e877e886413f027ca006175`
**Source hash**: `src/telescreen/contracts/police/v1/openapi.json before=absent after=80f02405e21da0ae3ec2aa46ccdd6db263542b87b6cb84d2465b5a544ba1f8b8`
**Source hash**: `src/telescreen/contracts/police/v1/partition-result.schema.json before=absent after=674697f63c1c132b8a0ebb621cbfdd1f514f102d7ccfa334e1db22c06e365ecb`
**Source hash**: `src/telescreen/contracts/police/v1/registration.schema.json before=absent after=fa7f493e83658f25f4d8c404bee63d9798ca3e915dde2a853754219bf8f39cd1`
**Source hash**: `src/telescreen/contracts/police/v1/result-envelope.schema.json before=absent after=ba7555339be28c37efd8fdf49b7d6df048ffdd69fdd11e679431c6d4a661d6f0`
**Source hash**: `src/telescreen/contracts/police/v1/task-envelope.schema.json before=absent after=16bc2c2e816c73c6a86afa9a2832a849f4e4fb48a4b50eb763641d65c982e43d`
**Source hash**: `src/telescreen/contracts/validator.py before=absent after=961136623841ef249febc3e9b573bb6b608b2fb7dcc6cf515c8e101e8126c67e`
**Source hash**: `tests/contract/test_schemas.py before=absent after=93e2e2c861d32def4c5357caeed0ac982d550059c2cab2c9eb7eac732d91e118`
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/contracts tests/contract/test_schemas.py` -> exit 0 (log: `/tmp/telescreen-p05-police-contract-format.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/contracts tests/contract/test_schemas.py` -> exit 0 (log: `/tmp/telescreen-p05-police-contract-ruff.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/contracts tests/contract/test_schemas.py` -> exit 0 (log: `/tmp/telescreen-p05-police-contract-ty.log`); focused pytest -> exit 0, 6 passed (log: `/tmp/telescreen-p05-police-contract-pytest.log`); p05 typelint path filter -> no diagnostics (global typelint exit 1, log: `/tmp/telescreen-p05-police-contract-typelint.log`, current unrelated p02/p03 diagnostics); JSON count -> exit 0, 26 files; initial wheel build attempt -> blocked before execution by the then-unresolved empty artifactRoots, resolved by the 2026-10-09 gate below.
**Notes**: No review decision claimed. Other plans' concurrent edits were preserved. The initial pytest attempts exposed a malformed OpenAPI document and a too-short maxLength probe; both were corrected and the final current-source suite passes.

### Final source verification: 2026-10-08 20:06
**Tasks Completed**: Added a safe unknown-schema error test; final focused suite now has seven passing tests.
**Tasks In Progress**: None for p05 implementation. Formal test-integrity, adversarial, and branch-evidence reviews remain downstream workflow gates.
**Blockers**: None.
**Hashes**: `tests/contract/test_schemas.py` before=93e2e2c861d32def4c5357caeed0ac982d550059c2cab2c9eb7eac732d91e118 after=0e0971e6a834b47357bde3e2d2796a93c5d828cc0911e3441f908992262df836; `impl-plans/active/p05-police-contract.md` before=3a10704a3a42e4baf7083a04a03d5065216c30bf077c1d77afbc96de41b8cd05 after=see `tmp/telescreen-v1-20261008-s319/p05-police-contract/plan-hash.sha256`.
**Verification**: Final-source scoped format check -> exit 0 (`/tmp/telescreen-p05-police-contract-format.log`); Ruff -> exit 0 (`/tmp/telescreen-p05-police-contract-ruff.log`); ty -> exit 0 (`/tmp/telescreen-p05-police-contract-ty.log`); pytest -> exit 0, 7 passed (`/tmp/telescreen-p05-police-contract-pytest.log`); p05 typelint filter -> no diagnostics (`/tmp/telescreen-p05-police-contract-typelint.log`; global typelint exit 1 from unrelated concurrent db plan files); wheel build and count -> exit 0, 26 JSON contract files (`/tmp/telescreen-p05-police-contract-wheel-gate-complete.log`).
**Notes**: Review and serial integration remain downstream workflow steps.

### Repair verification: 2026-10-09
**Tasks Completed**: Resolved the stale artifactRoots blocker and reconciliation request; confirmed the wheel includes all 26 Police v1 JSON files and reran the contract tests.
**Tasks In Progress**: None for p05 implementation.
**Blockers**: None.
**Hashes**: `impl-plans/active/p05-police-contract.md` before=650d4413f965888c7c40488e073528335f0f2d8cfdcdfedf71f64220aa6f4bdb after=recorded in `tmp/telescreen-v1-20261008-s319/p05-police-contract/plan-hash.sha256`.
**Verification**: `set -o pipefail; uv build --out-dir /tmp/telescreen-p05-police-contract-dist && test "$(unzip -l /tmp/telescreen-p05-police-contract-dist/*.whl | grep -c 'contracts/police/v1/.*\.json')" -ge 26` -> exit 0; 26 files (`/tmp/telescreen-p05-police-contract-wheel-gate-complete.log`). `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/contract` -> exit 0; 7 passed, 0 failed (`/tmp/telescreen-p05-police-contract-review-pytest.log`). Scoped format check, Ruff, and ty checks each exit 0 (`/tmp/telescreen-p05-police-contract-format-final.log`, `-ruff-final.log`, `-ty-final.log`). Typelint p05 path filter has no diagnostics (filter exit 0); global typelint exits 1 on unrelated `src/telescreen/api/routers/directory.py:53` TL008 (`/tmp/telescreen-p05-police-contract-typelint-final.log`).
**Notes**: Formal test-integrity, adversarial, and branch-evidence reviews are pending downstream workflow steps; no review approval is claimed.
