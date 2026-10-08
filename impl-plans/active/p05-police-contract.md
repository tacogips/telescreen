# P05 Police Contract v1 (OpenAPI + JSON Schemas) Implementation Plan

**Status**: Ready
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

- [ ] All schema files, examples and openapi.json exist and validate
- [ ] `validate` never exposes instance values
- [ ] The wheel contains the contract files
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
