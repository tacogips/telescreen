# P22 Google Drive, Docs and Sheets Collector Adapters Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Per-kind behavior: gdrive, gdocs, gsheets; Resource identity; Google auth), design-docs/specs/design-map-reduce-sweeps.md (Built-in sweep implementations: Drive single enumeration)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p22-collectors-google`
- planPath: `impl-plans/active/p22-collectors-google.md`
- wave: 3
- dependsOn: `p04-ports`
- writePaths:
  - `impl-plans/active/p22-collectors-google.md`
  - `src/telescreen/collectors/google`
  - `tests/collectors/google`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. Do not create `src/telescreen/collectors/__init__.py`
(owned by p20).

## Intent and Context

These are the built-in collectors for Google Drive, Docs and Sheets:

- push notifications (`changes.watch`) and scheduled `changes.list`;
- export of Docs and Sheets as text;
- whole-domain sweeps via domain-wide delegation, where each file is enumerated exactly
  once per sweep (accepted design fix DR-2).

All HTTP goes through httpx (no google-api-python-client). Service-account auth uses a
JWT signed with PyJWT (RS256).

## Non-goals

- No database access, no wiring, no notifications.

## Deliverables (`src/telescreen/collectors/google/`)

- `auth.py`:
  - `ServiceAccountKey(client_email: str, token_uri: str, private_key: str = field(repr=False))`,
    parsed from the JSON text of the secret ref with `TypeAdapter`.
  - `GoogleAuth(http, key: ServiceAccountKey, clock: Clock)` with
    `async token(scopes: tuple[str, ...], subject: str | None) -> str`:
    - JWT claims iss, scope, aud=token_uri, iat, exp=iat+3600, and sub when impersonating;
    - POST `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`;
    - cache per (scopes, subject) until 60 s before expiry;
    - never log tokens.
- `drive.py`: `DriveApi(http, auth, base="https://www.googleapis.com")` with typed
  calls:
  - `get_start_page_token`, `changes_list(page_token, drive_id?)`,
    `watch_changes(channel_id, token, address, expiration)`, `stop_channel`;
  - `files_list(q, corpora, drive_id, page_token, page_size, subject)`;
  - `files_get_meta`, `files_get_media`, `files_export(file_id, mime="text/plain")`;
  - `drives_list(page_token)`.
  - 404 -> `ResourceGoneError`; 429, or 403 with reason `rateLimitExceeded` or
    `userRateLimitExceeded` -> `RateLimited`; other errors -> `TelescreenError(fetch_failed)`.
- `sheets.py`: `SheetsApi(http, auth)` with `sheet_titles(spreadsheet_id)` and
  `values_batch_get(spreadsheet_id, ranges) -> tuple[SheetValues, ...]`.
- `admin.py`: `AdminApi(http, auth, admin_subject: str)` with `users_list(page_token)`
  (impersonating the admin) and `user_email(user_id)` (cached).
- `adapter.py`: `GoogleAdapter(kind: CollectorKind, drive_factory, sheets_factory, channel_token_key: bytes = field(repr=False), public_base_url: str | None, max_bytes: int)`.
  Construct one instance per kind (gdrive, gdocs, gsheets).
  - Mime filter: gdocs -> `application/vnd.google-apps.document`; gsheets ->
    `application/vnd.google-apps.spreadsheet`; gdrive -> non-Google-native, text-like
    files (`text/*`, json, csv, yaml, xml) up to `max_bytes`.
  - `verify_event`:
    - `X-Goog-Channel-ID` and `X-Goog-Channel-Token` must be present, and the token
      must equal hex HMAC-SHA256(channel_token_key, `drive-channel\0` + channel_id);
    - `X-Goog-Resource-State: sync` -> valid with no work;
    - otherwise valid with `config_hint=channel_id`.
  - `resources_from_event` -> `EventResources((), (), poll_changes=True)` (Drive
    notifications carry no file ids).
  - `list_changed`:
    - no cursor -> `get_start_page_token`, returning an empty page with that cursor;
    - otherwise `changes.list` from the cursor (paged);
    - a change with `removed`, `file.trashed`, or a file that left the selected folders
      or drives -> removed ref;
    - a matching file -> changed ref;
    - `newStartPageToken` becomes the next cursor; `full_listing=False`.
  - ref_json `{"file_id": id}`; raw_locator `gdrive:<id>`, `gdocs:<id>` or
    `gsheets:<id>` by kind.
  - `fetch`:
    - gdrive: media, decoded UTF-8; failure -> no segments and `complete=False`;
    - gdocs: export as text/plain;
    - gsheets: every sheet via `values.batchGet`, one segment per non-empty cell
      (`<sheet>!R<r>C<c>`);
    - text files are segmented per line (`L<n>`);
    - display_title is the file name;
    - `sweep_covered=True` when the file is within the selector.
  - `renew_watch(config, current)`:
    - requires `public_base_url`, else `config_error`;
    - stops the current channel (errors ignored);
    - creates a new channel (uuid4 id) with the computed token, address
      `<public_base_url>/api/collectors/gdrive/webhook` and a 7-day expiration;
    - returns the `WatchState`.
- `sweep.py`: `GoogleSweep(kind, drive_factory, admin: AdminApi | None, shard_size: int)`
  implements `SweepCapable`, following the accepted design:
  - Corpus `""` (root):
    - with `admin`: sub-corpora `user:<user_id>` for each `users.list` user and
      `drive:<drive_id>` for each `drives.list` drive;
    - without `admin`: `drive:<id>` for the selector's `drive_ids` and
      `folder:<id>` for its `folder_ids`.
  - `user:<id>` lists files with `q` containing `'me' in owners and trashed = false`
    (plus the mime filter), `corpora=user`, impersonating `admin.user_email(id)`. User
    emails NEVER go into descriptors; descriptors carry `user_id`.
  - `drive:<id>` lists with `corpora=drive`, `driveId`,
    `includeItemsFromAllDrives=true` and `supportsAllDrives=true`.
  - `folder:<id>` lists `'<id>' in parents`. A file reachable from several selected
    entries is kept only in the first matching entry in selector order (drives before
    folders): skip files whose `driveId` is a selected drive, and for folders, skip files
    whose parents include an earlier selected folder.
  - Each page is split into shards of `shard_size` file ids:
    - `shard_key` = sha256 hex of `corpus_key` + `\0` + sorted ids joined by commas;
    - descriptor `{"corpus": corpus_key, "file_ids": [...]}`;
    - `next_cursor` = `nextPageToken`.
  - `shard_resources`: per file id, `fetched` (with content), `removed` (404 or
    trashed) or `failed` (403, 500 or export failure), using the corpus's impersonation.

## Key Pitfalls

- The private key, access tokens and user emails never appear in logs, errors, `repr`,
  descriptors or ref_json.
- `q` strings: quote ids safely (`'` is escaped as `\'`).
- Tests generate an RSA key at runtime with `cryptography`. No key material is
  committed.
- Shared-drive files have no `owners`, which is why the `'me' in owners` filter never
  double-counts them.

## Tests (`tests/collectors/google/`)

All with `httpx.MockTransport`.

- Auth: the token request JWT decodes (with the test public key) to the expected claims;
  `sub` is present when impersonating; the token is cached until near expiry.
- `verify_event`: a valid token -> valid with the config hint; a wrong token -> invalid;
  `sync` -> valid with no work.
- `list_changed`: a trashed file -> removed; a file outside the folders -> removed; a
  matching doc -> changed (gdocs adapter only); the cursor advances.
- Fetch: a doc export containing a sample email -> segments per line; sheets -> cell
  locators `Sheet1!R2C3`; 404 -> `ResourceGoneError`; 429 -> `RateLimited`.
- `renew_watch` posts `changes.watch` with the token and address, and stops the old
  channel.
- Sweep single enumeration: a file owned by A and shared with B and C. The fake
  `files.list` honors `'me' in owners` per impersonated subject, so the root yields
  users A, B, C and one drive, and the file appears in exactly one shard across all
  corpora. A file in a shared drive appears only in the drive corpus.
- Sweep entries: 404 -> `removed`; 403 -> `failed`; 200 -> `fetched`.
- No descriptor contains an `@` (no emails).

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/collectors/google tests/collectors/google
uv run --frozen ruff check --no-cache src/telescreen/collectors/google tests/collectors/google
uv run --frozen ty check src/telescreen/collectors/google tests/collectors/google
uv run --frozen pytest -p no:cacheprovider tests/collectors/google 2>&1 | tee /tmp/telescreen-p22-collectors-google-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p22-collectors-google-typelint.log 2>&1; grep -E '^(src/telescreen/collectors/google|tests/collectors/google)' /tmp/telescreen-p22-collectors-google-typelint.log   # prints nothing
```

## Completion Criteria

- [x] GoogleAuth, the Drive, Sheets and Admin clients, GoogleAdapter (3 kinds) and GoogleSweep delivered with the exact names
- [x] Event, schedule and sweep tests, including single enumeration, pass
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 06:57
**Tasks Completed**: Repaired the Google sweep fakes for Admin user-detail lookups and shared-drive metadata, added an explicit optional descriptor guard, retained single-enumeration coverage, asserted each shard resource's removed/failed/fetched classification, annotated all p22 test bindings, and formatted/fixed p22 Ruff diagnostics.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: The prior wave-reconciliation filter logs are outside p22 writePaths; this session recorded its own non-empty typelint filter evidence under `tmp/telescreen-v1-20261008-s319/p22-collectors-google/` for serial reconciliation to consume.
**Hashes**: `src/telescreen/collectors/google/adapter.py` before=d96ba04b7bed83d39b14e805a479849e25539aba7b3b6e84c2b3947ffbe79833 after=99ce5badb3b67d36f8cb086a96e37240606c855f647ef460aac7399522408237; `src/telescreen/collectors/google/auth.py` before=27098b4b544e8cffb4a24f5f784cf6d4a54b67deba7a68d9ce1cdc9b660a8d66 after=5dd96555bcbf9571f2692d5ece9793c6b8060825cdbd6b35f421f9b242086725; `src/telescreen/collectors/google/drive.py` before=2a96d78d7ca0ea389ab55d5e53b6bca61a154618a799b4010ae62f053b73de37 after=834f0fe4526a43b243a0ffbea0a3fbbe87250d45751942f1fc35d0a59725206e; `src/telescreen/collectors/google/sheets.py` before=38cb5a727268287052b98644be55e48ae3cb3b2f53ea5445b9a691f084dd6e29 after=3c7ca46a320eb677759ad8cbfe037bf2168cb8fa5aaa995db47acae54f888d6e; `src/telescreen/collectors/google/sweep.py` before=d9f645a69fa877d4a63a4dd98f380f508f0f982c96f1167f67c4345adc0b99d0 after=de8398d7765a6bf84824cd9ef3e9d0d9d805f39cb5c8790773debfa4b1e6d47b; `tests/collectors/google/support.py` before=e4413703fbd1659e332d9e95a6bba51195c1c2b9c89990879481e21549182a94 after=11cff7a8db4d728766658d193367d45891c3c156f5932b92166cad0e70407df3; `tests/collectors/google/test_adapter.py` before=940a4168559c47fdf3ac4c78a0613efcbbda1de2e628cd35f585189e33eb5959 after=c748cf15a4d6e55ff0565e903f57b04556918f853f64b03a185771cc1bb163d3; `tests/collectors/google/test_auth.py` before=ff6b121eea502f5a614c8c4f77dc322095756c9097af358890e4209c9c59658b after=ca88d2b94107109f763bf0643664e481a911a437215cf09310d8de0696303e55; `tests/collectors/google/test_sweep.py` before=ee7bc81fdd1bff2af6ea721c9739f556453c4cd8f9db3dffeb88d27b131119f8 after=efc832df1dffdf622d7481056d11ab8bd87fce42b80f89d95e8532daea76ec2c.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-p22-final-format.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-p22-final-ruff.log`); `uv run --frozen ty check src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `/tmp/telescreen-p22-final-ty.log`); `uv run --frozen python tools/typelint.py` plus `! grep -E '(^|/)src/telescreen/collectors/google|(^|/)tests/collectors/google' /tmp/telescreen-p22-final-typelint-gate.log` -> exit 0, no p22 diagnostics (full log: `/tmp/telescreen-p22-final-typelint-gate.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/google` -> exit 0, 13 passed, 0 failed (log: `/tmp/telescreen-p22-final-pytest.log`).
**Notes**: Initial reproduction `/tmp/telescreen-p22-reproduce.log` showed 2 failing sweep tests; the first combined verification attempt `/tmp/telescreen-p22-final-pytest.log` was subsequently replaced only after the fixture corrections and now records the passing current-source run. Formal test-integrity and adversarial reviews remain downstream gates.

### Session: 2026-10-09 07:05
**Tasks Completed**: Addressed `P22-SWEEP-SIZE-SKIP-FAILED`: extracted `GoogleAdapter._fetch_content` so size/type skips return incomplete content while fetch, export and UTF-8 errors raise typed failures; retained `fetch_as` event-path error handling; changed `GoogleSweep.shard_resources` to emit any returned content as `FETCHED`; added oversized Drive/Docs, export-500, invalid-UTF-8, entry-count and rate-limit propagation coverage.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: The earlier `EVIDENCE-EMPTY-FILTER-LOGS` artifacts remain owned by serial reconciliation. Current p22 verification logs are stored under `tmp/telescreen-v1-20261008-s319/p22-collectors-google/`.
**Hashes**: `src/telescreen/collectors/google/adapter.py` before=99ce5badb3b67d36f8cb086a96e37240606c855f647ef460aac7399522408237 after=recorded in `tmp/telescreen-v1-20261008-s319/p22-collectors-google/final-source-hashes.sha256`; `src/telescreen/collectors/google/sweep.py` before=de8398d7765a6bf84824cd9ef3e9d0d9d805f39cb5c8790773debfa4b1e6d47b after=recorded in the same manifest; `tests/collectors/google/test_sweep.py` before=efc832df1dffdf622d7481056d11ab8bd87fce42b80f89d95e8532daea76ec2c after=recorded in the same manifest; package `__init__.py`, `admin.py`, and the plan file are included in the refreshed manifest.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p22-collectors-google/ruff-format-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p22-collectors-google/ruff-check-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/google tests/collectors/google` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p22-collectors-google/ty-final.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > tmp/telescreen-v1-20261008-s319/p22-collectors-google/typelint-final.log 2>&1; ! grep -E '(^|/)(src/telescreen/collectors/google|tests/collectors/google)' tmp/telescreen-v1-20261008-s319/p22-collectors-google/typelint-final.log` -> exit 0, repository reports 134 files clean; `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/google 2>&1 | tee tmp/telescreen-v1-20261008-s319/p22-collectors-google/pytest-final-size-skip.log` -> exit 0, 15 passed, 0 failed.
**Notes**: Event-path tests remained unchanged and pass within the 15-test run. Regression checks compare emitted counts to fixture file-id tuple lengths. Independent adversarial re-review and serial reconciliation are pending downstream.
