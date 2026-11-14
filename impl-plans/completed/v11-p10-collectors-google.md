# V11-P10 Google Drive Collector: Exposure, Owner, Placement, Modified Time, Coverage

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 3 (Drive row), 4 (placement `gdrive_drive`, `gdrive_folder`), 6 (coverage, `permission_denied`), 7 (owner identity: `owners[0].emailAddress`)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p10-collectors-google`
- planPath: `impl-plans/active/v11-p10-collectors-google.md`
- wave: 2
- dependsOn: `v11-p01-foundation`
- writePaths:
  - `impl-plans/active/v11-p10-collectors-google.md`
  - `src/telescreen/collectors/google`
  - `tests/collectors/google/test_v11_context.py` (new)
  - `tests/collectors/google/test_sweep.py` (one assertion only; see deliverable 1)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Drive is the main source of public and external sharing. With read-only calls, compute
`Exposure` (enums and counts only), owner identity (email, transient, `repr=False`),
placement (shared drive id and immediate parent folder ids), modified time and coverage
for `gdrive`, `gdocs` and `gsheets` resources.

Current code:

- `collectors/google/drive.py` (`DriveFile` and the `fields=` strings at about lines 127,
  195 and 208; 403 handling at about 279);
- `collectors/google/adapter.py` (builds `ResourceContent` at about line 243; any
  `TelescreenError` becomes a partial scan at about 141);
- `collectors/google/sweep.py`.

## Non-goals

- No new OAuth scopes beyond read-only (`drive.readonly` or `drive.metadata.readonly` as
  already configured). No permission changes, no link revocation.
- No ancestry walk of folders (immediate parents only).
- Do not keep emails, domains or display names anywhere except inside the transient
  `OwnerIdentity`.

## Invariants

- The internal domain list comes from a new adapter dataclass field
  `internal_domains: tuple[str, ...] = ()` (default empty). v11-p19 binds
  `settings.internal_domains`; do not read settings here.
- Permission entries are reduced to counts inside the function that receives them. The
  parsed permission dataclass keeps `email_address` and `domain` as `field(repr=False)`.

## Deliverables

1. **`drive.py`**:
   - `fields` gains `owners(emailAddress),modifiedTime,size` on `files.get`,
     `files.list` and the `changes.list` file subfields.
   - `DriveFile` gains `owners: tuple[DriveOwner, ...] = ()` (`DriveOwner(email_address:
     str = field(repr=False))`), `modified_time: str | None = None`, `size: str | None =
     None`.
   - New `list_permissions(file_id) -> tuple[DrivePermission, ...]`: GET
     `files/{id}/permissions` with `supportsAllDrives=true`, `fields=permissions(type,role,domain,emailAddress,allowFileDiscovery),nextPageToken`,
     with pagination. `DrivePermission(type: str, role: str, domain: str | None =
     field(default=None, repr=False), email_address: str | None = field(default=None,
     repr=False), allow_file_discovery: bool = False)`.
   - In `DriveApi._check`, map HTTP 403 that is not a quota or rate-limit 403 (that is,
     `_is_rate_limited` is False) to `TelescreenError(ErrorCode.FORBIDDEN, <static
     message>)` instead of `FETCH_FAILED`. This is the design section 6 rule "provider
     403 ... -> `permission_denied` (today these map to `fetch_failed`)".
   - HTTP 404 keeps the v1 behavior unchanged: `_check` raises `ResourceGoneError` before
     any other branch (drive.py `_check`, about line 268). The resource is recorded as
     removed, which resolves as `resource_deleted` (design section 2: `ResourceGoneError`
     sets `removed=True`; section 5: `removed=true` -> `resource_deleted`). The section 6
     404 clause applies only to 404s that map to `fetch_failed` today; a Drive 404 does
     not, so it never becomes `FORBIDDEN`.
   - Rate-limit and quota 403s and 429 keep raising `RateLimited`. Other 4xx/5xx keep
     `FETCH_FAILED`.
   - `sweep.py` already yields `ShardEntry(FAILED, ref, error_code=error.code)`, so a 403
     reaches v11-p08 as `error_code=FORBIDDEN` (mapped there to `permission_denied`).
     Update exactly one v1 assertion: `tests/collectors/google/test_sweep.py` line 188
     (`failed_entry.error_code is ErrorCode.FETCH_FAILED` for the `denied` 403 file)
     becomes `ErrorCode.FORBIDDEN`. Do not touch any other line of that file; its 404 ->
     `REMOVED` assertion and the decode/export `FETCH_FAILED` assertions (about lines 318
     and 349) stay unchanged.
2. **`exposure.py`** (new, in `collectors/google/`): `drive_exposure(permissions,
   drive_id, internal_domains) -> Exposure`, implementing the design section 3 Drive row
   exactly:
   - `anyone` with `allowFileDiscovery` -> `public`;
   - other `anyone` -> `anyone_with_link`;
   - grants outside the internal domains -> `external_shared` with
     `external_principal_count`;
   - an internal `domain` grant -> `domain`;
   - at least one internal user or group grant besides the owner -> `internal`. The
     `role == "owner"` grant never counts;
   - owner only -> `private`;
   - no internal domains configured and user or group grants exist -> `unknown`;
   - `shared_drive = drive_id is not None`.
3. **`adapter.py` and `sweep.py`**: every `ResourceContent` sets:
   - `exposure` (permission read failure -> None);
   - `owner=OwnerIdentity(EMAIL, owners[0].email_address)` when an owner exists;
   - `placement` = `(gdrive_drive, driveId)` if present, plus `(gdrive_folder, parent)`
     for each parent;
   - `modified_at` parsed from RFC 3339 `modifiedTime`;
   - `coverage`:
     - unsupported MIME -> `UNSUPPORTED` with reason `IMAGE` for `image/*`, else
       `UNSUPPORTED_TYPE`;
     - oversized or truncated export -> `PARTIAL` with `truncated_bytes` when known;
     - `FORBIDDEN` errors -> `PERMISSION_DENIED`;
     - other `TelescreenError` -> `FETCH_FAILED`. The existing partial result is kept,
       with this coverage added.

## Pitfalls

- `permissions.list` is an extra call per file. Call it only for files actually fetched
  (not for listing pages), and only once per fetch.
- Domains compare lowercased, and the email domain is the part after the last `@`.
- A `domain` permission whose domain is external counts as an external grant.
- Keep `drive.py` under 1000 lines (303 lines now).
- Do not change 404 handling. `tests/collectors/google/test_adapter.py:265-277` (404 ->
  `ResourceGoneError`, "404 was not classified as removed") and
  `tests/collectors/google/test_sweep.py:118-187` (metadata 404 -> `REMOVED`) must keep
  passing; only the line-188 error-code assertion changes, as stated in deliverable 1.
- Keep `adapter.py` `fetch_as` catching `(ResourceGoneError, RateLimited)` and re-raising
  them before the `TelescreenError` branch; `FORBIDDEN` is a `TelescreenError` and takes
  the partial-scan path with coverage `PERMISSION_DENIED`.

## Tests (`tests/collectors/google/test_v11_context.py`)

Use MockTransport; imitate `tests/collectors/google/support.py` and `test_adapter.py`.

- `anyone` + discovery -> public; `anyone` -> anyone_with_link;
- `user` outside the internal domains x2 -> external_shared, count 2;
- an internal `domain` grant -> domain;
- owner + one internal user -> internal;
- owner only -> private;
- user grants with no internal domains configured -> unknown;
- a permissions call returning 500 -> exposure None and the fetch succeeds;
- owner email -> `OwnerIdentity(EMAIL, ...)`, and `repr(content)` has no `@`;
- placement includes the drive and parent folders;
- `modifiedTime` parsed;
- 403 (non-quota body) on fetch -> coverage `permission_denied`; a quota 403
  (`rateLimitExceeded` reason) -> `RateLimited`, not `FORBIDDEN`;
- regression: fetch 404 -> `ResourceGoneError` raised (not `TelescreenError` with
  `FORBIDDEN`), and in a sweep shard the entry kind is `REMOVED`;
- image MIME -> `unsupported/image`;
- every recorded request is a GET (no POST, PATCH or DELETE to the Drive API).

## Verification

Conventions section 7 with `<src>` = `src/telescreen/collectors/google` and `<tests>` =
`tests/collectors/google`. Existing Google tests must still pass; the only edited v1
assertion is `test_sweep.py` line 188 (`FETCH_FAILED` -> `FORBIDDEN`).

- `grep -n '404' src/telescreen/collectors/google/drive.py` -> the 404 branch still
  raises `ResourceGoneError`.
- `git diff --stat tests/collectors/google/test_sweep.py` -> 1 line changed (1 insertion,
  1 deletion).

## Completion Criteria

- [x] Exposure, owner, placement, modified time and coverage populated as specified.
- [x] Non-quota 403 -> `FORBIDDEN`; 404 -> `ResourceGoneError` unchanged (regression
      test passes).
- [x] GET-only assertion passes; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:09 JST
**Tasks Completed**: Added Drive owner/modified/size metadata, paginated permissions GET, count-only exposure mapping, transient owner and placement context, modified-time parsing, coverage states, non-quota 403 classification, and MockTransport regressions. Changed exactly the planned denied-file assertion in `test_sweep.py`.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `drive.py` before=834f0fe4526a43b243a0ffbea0a3fbbe87250d45751942f1fc35d0a59725206e after=e8b344b1aea045518c7f50c643a4bfb003f4930c41b370a89047e0514d6b9a77; `adapter.py` before=459ad10a2c9f9614cdfdbd9e2bcd096df43435b6c53a339981a15a4ab206509b after=523c88d960034ab96693de87291b549e68f255aded79bb32c12e731207d42fa9; `exposure.py` before=absent after=64969ba24875ab77182bc59a520f3527ecce7cf1217a27dc4e32101335655d28; `test_v11_context.py` before=absent after=5b394680900c543a1bc71d4a0d2e2aa995e7134d6a9e6a36176c0b32ba7f8cae; `test_sweep.py` before=6f9f6817acbad389e1d66944b0cb88788235501787d59a45ba410077f0a2875a after=40fdadcbd6c905415a482cffd0c70aca1a74f86658eee253943596e82bd8c668.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-v11-p10-collectors-google-format-final2.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-v11-p10-collectors-google-ruff-final2.log`).
- `uv run --frozen ty check src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-v11-p10-collectors-google-ty-final2.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/collectors/google` -> exit 0, 23 passed, 0 failed (log: `/tmp/telescreen-v11-p10-collectors-google-pytest-final2.log`).
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p10-collectors-google-typelint-final2.log 2>&1; ! grep -E '^(.*/)?(src/telescreen/collectors/google/|tests/collectors/google/)' /tmp/telescreen-v11-p10-collectors-google-typelint-final2.log` -> overall exit 0; whole-repository typelint reports diagnostics outside p10, while this absolute-path-aware p10 grep finds none.
- `grep -n '404' src/telescreen/collectors/google/drive.py` -> exit 0, 404 branch remains first and raises `ResourceGoneError` (log: `/tmp/telescreen-v11-p10-collectors-google-404-check.log`).
- `git diff --stat -- tests/collectors/google/test_sweep.py` -> exit 0, exactly 1 insertion and 1 deletion (log: `/tmp/telescreen-v11-p10-collectors-google-sweep-diff-stat.log`).
- `test "$(grep -c '^- \[ \]' impl-plans/active/v11-p10-collectors-google.md)" -eq 0` -> exit 0 (final log: `/tmp/telescreen-v11-p10-collectors-google-checkbox-final.log`).
- `git diff --check -- src/telescreen/collectors/google/drive.py src/telescreen/collectors/google/adapter.py src/telescreen/collectors/google/exposure.py tests/collectors/google/test_v11_context.py tests/collectors/google/test_sweep.py impl-plans/active/v11-p10-collectors-google.md` -> exit 0 (final log: `/tmp/telescreen-v11-p10-collectors-google-diff-check-final.log`).
**Notes**: The initial focused pytest log `/tmp/telescreen-v11-p10-collectors-google-pytest-initial.log` records 1 failure and 17 passes: direct test DTO construction used Pydantic `FieldInfo` defaults. The test now supplies explicit constructor fields; final source run passes 23 tests. Initial Ruff findings were corrected. The first typelint grep used a relative-path prefix and missed absolute-path diagnostics in this test; annotated all three locals and reran with the absolute-path-aware grep, which finds no p10 diagnostics. The whole-repository typelint still reports findings outside p10. All plan criteria are checked.

### Session: 2026-10-10 17:20 JST (P10-TIC-001 repair)
**Tasks Completed**: Replaced Pydantic `Field(repr=False)` defaults on stdlib dataclasses with `dataclasses.field(repr=False)` for `DriveOwner.email_address`, `DrivePermission.domain`, `DrivePermission.email_address`, and `DriveFile.owners`. Kept Google camelCase aliases in `Annotated[..., Field(alias=...)]`. Added a regression test that parses the DTOs through `TypeAdapter`, confirms values, and confirms repr excludes emails and domains. `tests/collectors/google/test_sweep.py` remains unchanged from its single planned line-188 assertion.
**Tasks In Progress**: Independent test-integrity and adversarial re-review of P10-TIC-001 is downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `drive.py` after=3d6502111240ce72487885b3ab7663c4937376e1ea321cb2c9311ac7f69e557d; `test_v11_context.py` after=f4f567ffe73355cbdf937f729d73db81414c11f04f0eb0730c6bd81371bb2d53; unchanged `adapter.py`=523c88d960034ab96693de87291b549e68f255aded79bb32c12e731207d42fa9; unchanged `exposure.py`=64969ba24875ab77182bc59a520f3527ecce7cf1217a27dc4e32101335655d28; `test_sweep.py`=40fdadcbd6c905415a482cffd0c70aca1a74f86658eee253943596e82bd8c668.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/google/test_v11_context.py -k drive_principal_aliases_parse_without_repr_leaks 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-repr-pytest.log` -> exit 0, 1 passed (8 deselected).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python -c 'from pydantic import TypeAdapter; from telescreen.collectors.google.drive import DrivePermission, DriveFile; permission = TypeAdapter(DrivePermission).validate_json(b"{\"type\":\"user\",\"role\":\"reader\",\"emailAddress\":\"c@d.invalid\",\"domain\":\"d.invalid\"}"); drive_file = TypeAdapter(DriveFile).validate_json(b"{\"id\":\"x\",\"owners\":[{\"emailAddress\":\"a@b.invalid\"}]}"); print(repr(permission)); print(repr(drive_file)); assert "@" not in repr(permission) and "d.invalid" not in repr(permission); assert "@" not in repr(drive_file) and "b.invalid" not in repr(drive_file); assert permission.email_address == "c@d.invalid" and permission.domain == "d.invalid"; assert drive_file.owners[0].email_address == "a@b.invalid"' 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-repr-repro.log` -> exit 0; both printed reprs omit email/domain strings and the parsed values remain populated.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/google tests/collectors/google 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-format.log` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/google tests/collectors/google 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-ruff.log` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/google tests/collectors/google 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-ty.log` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/google 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-pytest.log` -> exit 0, 24 passed, 0 failed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p10-collectors-google-tic2-typelint.log 2>&1; ! grep -E '^(.*/)?(src/telescreen/collectors/google/|tests/collectors/google/)' /tmp/telescreen-v11-p10-collectors-google-tic2-typelint.log` -> compound exit 0; whole-repository typelint reports 39 diagnostics outside p10, and the p10 absolute-path-aware grep is empty.
- `set -o pipefail; grep -n '404' src/telescreen/collectors/google/drive.py 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-404.log` -> exit 0, the 404 handling branch remains present.
- `set -o pipefail; git diff --stat -- tests/collectors/google/test_sweep.py 2>&1 | tee /tmp/telescreen-v11-p10-collectors-google-tic2-sweep-diff.log` -> exit 0, exactly 1 insertion and 1 deletion.
- `test "$(grep -c '^- \[ \]' impl-plans/active/v11-p10-collectors-google.md)" -eq 0` -> exit 0 (complete log: `/tmp/telescreen-v11-p10-collectors-google-tic2-checkbox.log`); `git diff --check -- src/telescreen/collectors/google/drive.py src/telescreen/collectors/google/adapter.py src/telescreen/collectors/google/exposure.py tests/collectors/google/test_v11_context.py tests/collectors/google/test_sweep.py impl-plans/active/v11-p10-collectors-google.md` -> exit 0 (complete log: `/tmp/telescreen-v11-p10-collectors-google-tic2-diff-check.log`).
**Notes**: Previous test-integrity disposition `P10-TIC-001` was a mid finding: stdlib dataclass reprs exposed transient email/domain values. The source-matched regression and full Google suite now pass. No acceptance decision is claimed; independent review remains pending.
