# V11-P03 Police Contract 1.1 (resource_exposures) and SDK

**Status**: Completed
**Design Reference**: design-docs/specs/design-police-contract.md ("Contract 1.1 (v1.1, exposure)"); design-docs/specs/design-v1-1-dspm-adoption.md section 3 (Police row)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p03-police-contract`
- planPath: `impl-plans/active/v11-p03-police-contract.md`
- wave: 1
- dependsOn: none (base: main at 289699e)
- writePaths:
  - `impl-plans/active/v11-p03-police-contract.md`
  - `src/telescreen/contracts/police/v1`
  - `src/telescreen_police_sdk`
  - `tests/contract`
  - `tests/police_sdk/test_v11_exposures.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Police workers run in the customer's network and can now report the exposure of each
scanned resource. Minor version 1.1 adds one optional, closed member to the result
envelope. Both the Bureau validator (`telescreen.contracts.validator`, schema files) and
the standalone SDK must agree on it. The Bureau-side parsing of the new member is plan
v11-p12 (`telescreen/police/results.py`), not this plan.

## Non-goals

- No change to `src/telescreen/police/**` or `src/telescreen/api/**`.
- No `v2` paths, and no change to other schemas or to the version pattern.
- The SDK must never import `telescreen.*`. Its dependencies stay stdlib and httpx.

## Invariants

- Every existing example and every 1.0 envelope still validates. Existing contract tests
  pass unchanged.
- `contract_version` pattern stays `^1\.[0-9]+$`.
- The new schema is closed (`additionalProperties: false`).

## Deliverables

1. `src/telescreen/contracts/police/v1/resource-exposure.schema.json` (new, Draft
   2020-12, `$id` style as the sibling files): members and limits exactly as in the design
   table:
   - `locator` string, maxLength 1024, required;
   - `visibility` enum, required;
   - `external_principal_count` integer minimum 0;
   - `shared_drive`, `is_fork` boolean;
   - `channel_kind` enum;
   - `repo_visibility` enum.
2. `result-envelope.schema.json`: add property `resource_exposures` (array, `maxItems`
   1000, items `$ref: "resource-exposure.schema.json"`, using the same `$ref` form the file
   already uses for `masked-finding.schema.json`). Not required.
3. `openapi.json`: register the new component schema the way `masked-finding` is
   registered.
4. `examples/resource-exposure.json` and `examples/result-envelope-exposures.json` (a
   `detect` completed result with `contract_version: "1.1"`, one finding with locator
   `docs/example.txt` and a matching exposure with visibility `public`). Use only masked
   example values; no sample secrets.
5. `src/telescreen/contracts/validator.py`: change it only if `$ref` resolution needs the
   new file registered. Read `load_schema` first. If the files are discovered by glob, no
   change is needed.
6. SDK (`src/telescreen_police_sdk/models.py`):
   - `CONTRACT_VERSION = "1.1"`;
   - new StrEnums `Visibility`, `ChannelKind`, `RepoVisibility` (same values as the
     Bureau vocabulary);
   - new frozen dataclass `ResourceExposure(locator: str, visibility: Visibility,
     external_principal_count: int | None = None, shared_drive: bool | None = None,
     channel_kind: ChannelKind | None = None, repo_visibility: RepoVisibility | None =
     None, is_fork: bool | None = None)` with `to_json`/`from_json` (imitate
     `MaskedFinding` in the same file). Optional members are omitted when None.
   - The result envelope dataclass (read the file for its exact name) gains
     `resource_exposures: tuple[ResourceExposure, ...] = ()`. It is serialized only when
     non-empty and parsed when present.
7. SDK handler outputs: wherever the SDK builds a result envelope from a handler outcome
   (`worker.py`, `webhook.py`, `sweep.py`; read them), the handler outcome type gains an
   optional `resource_exposures` tuple (default `()`) passed through unchanged. No other
   behavior change.

## Pitfalls

- Do not add `resource_exposures` to `stats` or to `masked-finding`. It is top-level.
- Keep `_json.py` the only JSON boundary in the SDK. Do not import `json` natives
  elsewhere.
- Do not change the 1.0 examples. Tests that hard-code `"1.0"` manifests must keep
  passing, because the Bureau accepts any `1.x`.

## Tests

- `tests/contract/test_schemas.py` already validates every example against its schema.
  Confirm that the new examples are picked up: `result-envelope-*` maps to the
  result-envelope schema, and `resource-exposure.json` maps to the new schema.
- New cases in `tests/contract/test_v11_contract.py`:
  - an envelope with an extra member inside an exposure item -> schema error;
  - visibility `"world"` -> error;
  - 1001 items -> error;
  - a 1.0 envelope without the member -> valid;
  - SDK `to_json` of an envelope with exposures validates against
    `result-envelope.schema.json`.
- `tests/police_sdk/test_v11_exposures.py`:
  - `ResourceExposure` round trip with all and with only the required members;
  - the envelope without exposures does not emit the key;
  - `CONTRACT_VERSION == "1.1"`;
  - a pull worker handler returning exposures sends them in the submitted body (httpx
    MockTransport, imitate `tests/police_sdk/test_sdk.py`).
- SDK isolation: the existing check in `tests/police_sdk` or `tests/contract` that the
  SDK does not import `telescreen` must still pass.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/contracts src/telescreen_police_sdk` and
`<tests>` = `tests/contract tests/police_sdk`. Plus
`uv run --frozen pytest -p no:cacheprovider tests/contract tests/police_sdk tests/police > /tmp/telescreen-v11-p03-police-contract-pytest-wide.log 2>&1; echo "exit=$?"`,
which must show `exit=0` (Bureau police tests still accept results).

## Completion Criteria

- [x] New schema, envelope member, openapi registration and two examples exist.
- [x] SDK version is 1.1 and exposures round-trip; the SDK imports no `telescreen`.
- [x] Scoped gates and the wide pytest pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 10:30
**Tasks Completed**: Added the closed resource exposure schema, optional result-envelope member, OpenAPI registration, two examples, SDK 1.1 DTO serialization/parsing, sweep and webhook pass-through, and contract/SDK regression tests.
**Tasks In Progress**: None for this plan.
**Blockers**: None. Whole-repository typelint currently exits 1 on the concurrent sibling finding `tests/core/test_v11_foundation.py:79`; no owned-path findings were reported.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/contracts/police/v1/openapi.json` before=80f02405e21da0ae3ec2aa46ccdd6db263542b87b6cb84d2465b5a544ba1f8b8 after=b7c893f95888c8caf0e454d73efcb0081103400c804daacfbe41739746eb98ac; `result-envelope.schema.json` before=ba7555339be28c37efd8fdf49b7d6df048ffdd69fdd11e679431c6d4a661d6f0 after=f657509731a78db538b93d81026bb414350842486b9a468b80bf8dbc22348253; `validator.py` before=961136623841ef249febc3e9b573bb6b608b2fb7dcc6cf515c8e101e8126c67e after=670387ade1c8c366739fda954cd5956c3ac46037e3796138457012f5060b1e59; `models.py` before=80ea50982dbe8ebbbfd4dd485a812db9ecfe066da807eca1e53f393e2c0a4a19 after=be80ec44f777105a60d34d3730f6fc7e9f455b5d71073b1bd64a68d55648ca5d; `sweep.py` before=a97ebfea932a26d4c3bbd9cc9e44923c872998b847addadb55492702ad6c03de after=abae686d3f9728af2f5a794862bcd3bb157c51a024d3ba5df4660fbbfd12a1d1; `webhook.py` before=756d70e884e3eba9a0a851618b87784aad5a588ad05569e1c2fc6cda75b9c7fc after=92898fdb0ad9bf8f12081469b2343b015a23e7567d9ef3f9a43921dce457fcaa; `test_sdk_contract.py` before=fb46750ac7fdc7f1320a02ce86ffe4f901e2d77e61427b5b7cde144f4719570a after=ead66a685bbe82507a1161111156a1090fd6d8985603943eb8e384d618a3a7c2.
Additional new-file hashes (before=absent): `resource-exposure.schema.json` after=cdc5ff7b929301de087fb298f5646d363b6aeac40ecac9fd0700c5fb28268b26; `examples/resource-exposure.json` after=fe1fd776cc97f1c384fb0da550757b0fe85df5e597407280b00018cf7557f6e0; `examples/result-envelope-exposures.json` after=2e24c8c9882501f61750e80d2ef17aa41ee1e2066a500601086af177e3030b1b; `tests/contract/test_v11_contract.py` after=a01c82a271ee72b235a03a33f7f0920628b062033060406a2bef21e52d6bd717; `tests/police_sdk/test_v11_exposures.py` after=47c3010e0c3264c0693dfe4a960980793beab4d997ea421182d0206c33a28ff0.
Plan self-hash before=6b48d6676d5ec55ae3ac980188c30b379810ad8c289d9d48f247ed792ea14b96; record its final hash in the workflow evidence after this edit.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/contracts src/telescreen_police_sdk tests/contract tests/police_sdk` -> exit 0 (log: `/tmp/telescreen-v11-p03-police-contract-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/contracts src/telescreen_police_sdk tests/contract tests/police_sdk` -> exit 0 (log: `/tmp/telescreen-v11-p03-police-contract-ruff-final.log`); `uv run --frozen ty check src/telescreen/contracts src/telescreen_police_sdk tests/contract tests/police_sdk` -> exit 0 (log: `/tmp/telescreen-v11-p03-police-contract-ty.log`); `uv run --frozen pytest -p no:cacheprovider tests/contract tests/police_sdk` -> exit 0, 38 passed (log: `/tmp/telescreen-v11-p03-police-contract-pytest.log`); `uv run --frozen pytest -p no:cacheprovider tests/contract tests/police_sdk tests/police` -> exit 0, 56 passed (log: `/tmp/telescreen-v11-p03-police-contract-pytest-wide.log`); `uv run --frozen python tools/typelint.py` followed by owned-path grep -> grep empty (whole run exit 1 on sibling path above; log: `/tmp/telescreen-v11-p03-police-contract-typelint.log`); SDK import isolation grep -> exit 0 (log: `/tmp/telescreen-v11-p03-police-contract-sdk-imports.log`). Initial format and lint attempts found only the reported formatting/import ordering; fixed by exact-file Ruff formatting and import sorting before final passing gates (`/tmp/telescreen-v11-p03-police-contract-format.log`, `/tmp/telescreen-v11-p03-police-contract-ruff.log`, `/tmp/telescreen-v11-p03-police-contract-format-write.log`, `/tmp/telescreen-v11-p03-police-contract-import-fix.log`).
**Notes**: SDK locator fields are excluded from repr. Existing 1.0 examples remain unchanged. No source files outside this plan's write paths were edited by this plan; concurrent sibling changes are preserved.
