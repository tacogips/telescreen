# V11-P03 Police Contract 1.1 (resource_exposures) and SDK

**Status**: Ready
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

- [ ] New schema, envelope member, openapi registration and two examples exist.
- [ ] SDK version is 1.1 and exposures round-trip; the SDK imports no `telescreen`.
- [ ] Scoped gates and the wide pytest pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
