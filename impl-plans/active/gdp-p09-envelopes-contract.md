# GDP-P09 Go Envelope Goldens and Python Schema Contract Test

**Status**: Completed
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p09-envelopes-contract/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 8.1, 9.5, 10
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p09-envelopes-contract`
- planPath: `impl-plans/active/gdp-p09-envelopes-contract.md`
- wave: 3
- dependsOn: `gdp-p01-foundation`, `gdp-p03-police-worker`
- writePaths:
  - `impl-plans/active/gdp-p09-envelopes-contract.md`
  - `go/internal/envelopes`
  - `go/testdata/envelopes`
  - `tests/go_police/test_go_envelopes_contract.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` (all sections; Python rules apply to the test).

## Intent and Context

Acceptance requires a Python contract test that validates Go-produced envelopes against
`src/telescreen/contracts/police/v1/` with `telescreen.contracts.validate`. The Go side
produces committed golden JSON through the production encoders
(`police.EncodeManifest`, `EncodeClaim`, `EncodeHeartbeat`, `EncodeResult` from gdp-p01).
A Go test asserts that the goldens are byte-for-byte what the encoders produce. The
Python test validates the same files.

Imitate:

- `tests/contract/test_sdk_contract.py` and `tests/contract/test_v14_contract.py` (how
  SDK envelopes are validated);
- `src/telescreen/contracts/validator.py:validate(name, raw)`.

## Non-goals

- No change to schemas, examples or the SDK.
- No live Bureau (that is gdp-p10's E2E).

## Deliverables

1. `go/internal/envelopes/doc.go`: package `envelopes`, doc comment only.
2. `go/internal/envelopes/golden_test.go`:
   - Builds each envelope from `police` DTOs with synthetic, redactor-clean values:
     - locators such as `gdrive:fileid0001`;
     - masked snippets that are ALREADY masked, for example
       `key=********[24]` or `**** **** **** 1111`;
     - fixed 64-hex fingerprints chosen to be redactor-clean: letters `a-f` only, no
       digit runs.
   - Encodes each with the production encoder and compares it with
     `../../testdata/envelopes/<name>.json`.
   - With `go test ./internal/envelopes -args -update`, the files are rewritten. Define
     the flag inside the test file via `flag.Bool` in `TestMain`; a package-level flag
     var is acceptable in `_test.go` only.
3. `go/testdata/envelopes/` contains exactly these files:
   - `manifest.json` (pull, task types `detect`, `partition`, `scan_shard`, labels
     `police=gdrive`, concurrency 4);
   - `claim-request.json`;
   - `heartbeat-request.json`;
   - `result-detect-findings.json` (findings and `complete_locators` only, the 1.0
     members);
   - `result-detect-exposures.json` (adds `resource_exposures`, the 1.1 member);
   - `result-scan-shard.json` (findings, `stats`, `complete_locators`,
     `resource_exposures`, and `resource_inventories` with one `table` + `item_count`
     and one `file` kind entry, the 1.2 member);
   - `result-partition.json` (payload with 2 shards, cursor string, empty
     `sub_corpora`);
   - `result-partition-root.json` (payload with empty shards, cursor `drives:`, 2
     `sub_corpora`);
   - `result-failed-rate-limited.json` (status `failed`, error `rate_limited`,
     `retry_after_seconds` 7).
4. `tests/go_police/test_go_envelopes_contract.py`:
   - The expected file set equals the directory listing. A missing or extra file fails,
     so the test cannot pass vacuously.
   - Map name to schema: `manifest` -> `manifest.schema.json`, `claim-request` ->
     `claim-request.schema.json`, `heartbeat-request` -> `heartbeat-request.schema.json`,
     `result-*` -> `result-envelope.schema.json`. `validate(...)` must return an empty
     list for each.
   - `result-*` files have `contract_version == "1.2"`.
   - Both partition files: `validate("partition-result.schema.json",
     dump_json(payload).encode())` is empty.
   - `result-scan-shard.json`: `len(complete_locators) + stats.resources_failed`
     equals the declared shard size, which the test pins as a constant matching the Go
     test.
   - Every exposure and inventory locator is in `complete_locators` or a finding
     locator.
   - Every string leaf is clean: `Redactor.default().has_hits(...)` is False for all of
     them, which is the Bureau `_contains_hit` rule.
   - Parse with `telescreen.core.json.parse_json`. No `dict` bindings (typelint).

## Key Pitfalls

- Do not hand-edit golden JSON. Regenerate with `-update` and then run the test without
  the flag.
- Fingerprints must avoid digit runs (see design G4). Using `a`-`f` only is simplest.
- `next_cursor` must be `null` (not absent) when there is no cursor, and `shards` and
  `sub_corpora` must be arrays. This is checked by the schema.
- No gitleaks hits: no secret-shaped values anywhere.

## Tests (input -> expected)

- Go: each of the 9 envelopes encoded -> equals its golden file.
- Python: 9 files validate. The partition payloads validate. The identity and locator
  rules hold. No Redactor hits.
- Mutation check (Progress Log only): remove `status` from a copy of one golden in
  `/tmp` and run `validate` on it -> errors are non-empty.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
(cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -count=1 ./internal/envelopes/... 2>&1 | tee /tmp/telescreen-gdp-p09-gotest.log); echo "exit=$?"
(cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/envelopes/... && mise exec -- gofmt -l internal/envelopes)
uv run --frozen pytest -p no:cacheprovider tests/go_police/test_go_envelopes_contract.py 2>&1 | tee /tmp/telescreen-gdp-p09-pytest.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tests/go_police/test_go_envelopes_contract.py
uv run --frozen ruff check --no-cache tests/go_police/test_go_envelopes_contract.py
uv run --frozen ty check tests/go_police/test_go_envelopes_contract.py
uv run --frozen python tools/typelint.py > /tmp/telescreen-gdp-p09-typelint.log 2>&1; grep -E '^tests/go_police/test_go_envelopes_contract' /tmp/telescreen-gdp-p09-typelint.log
```

Every command exits 0. `gofmt` and the typelint grep print nothing, and the pytest log
shows all tests passed with zero skipped.

## Completion Criteria

- [x] The 9 golden files exist and the Go golden test passes without `-update`.
- [x] The Python contract test passes and covers 1.0, 1.1 and 1.2 members, partition,
      `scan_shard` and failed shapes.
- [x] ruff, ty and typelint are clean; `vet` and `gofmt` are clean. Progress Log
      complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)

### Session: 2026-10-31
**Tasks Completed**: Generated nine production-encoded goldens and added Go byte comparison and Python schema, redaction, accounting and locator checks.
**Tasks In Progress**: Formal reviews and integration are downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: New files had before=absent; after hashes are recorded in `tmp/telescreen-gdp-20261030-h2/gdp-p09-envelopes-contract/attempt-1/final-hashes.log`. Plan before=cb46b384750d83d65dcd77ef202d0209e6372207517bb9cd29f472429b77a2b9; after is in the same log.
**Verification**: All commands exited 0; complete logs are under `tmp/telescreen-gdp-20261030-h2/gdp-p09-envelopes-contract/attempt-1/`.
- `cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -count=1 -v ./internal/envelopes/...` (go-test.log): nine cases passed without update.
- `cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/envelopes/...` (vet.log); `cd go && test -z "$(mise exec -- gofmt -l internal/envelopes)"` (gofmt.log).
- `PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider tests/go_police/test_go_envelopes_contract.py` (final-python-test.log): 11 passed, zero skipped.
- `uv run --frozen ruff format --check --no-cache tests/go_police/test_go_envelopes_contract.py` (final-format.log); `uv run --frozen ruff check --no-cache tests/go_police/test_go_envelopes_contract.py` (final-ruff.log); `uv run --frozen ty check tests/go_police/test_go_envelopes_contract.py` (final-ty.log); `uv run --frozen python tools/typelint.py` (final-typelint.log).
- `mise exec -- gitleaks dir --config .gitleaks.toml --redact --no-banner go/testdata/envelopes` (final-gitleaks.log): no leaks.
- Mutation: removed status from a copy in `/tmp/telescreen-gdp-p09-mutated.json`; validator returned errors (mutation.log).
**Notes**: Plan aligns with design sections 8.1, 9.5 and 10; dependency acceptance follows dispatch.
