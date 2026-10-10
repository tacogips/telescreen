# V11-P10 Google Drive Collector: Exposure, Owner, Placement, Modified Time, Coverage

**Status**: Ready
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

- [ ] Exposure, owner, placement, modified time and coverage populated as specified.
- [ ] Non-quota 403 -> `FORBIDDEN`; 404 -> `ResourceGoneError` unchanged (regression
      test passes).
- [ ] GET-only assertion passes; scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
