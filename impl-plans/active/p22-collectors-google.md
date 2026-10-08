# P22 Google Drive, Docs and Sheets Collector Adapters Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Per-kind behavior: gdrive, gdocs, gsheets; Resource identity; Google auth), design-docs/specs/design-map-reduce-sweeps.md (Built-in sweep implementations: Drive single enumeration)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

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

- [ ] GoogleAuth, the Drive, Sheets and Admin clients, GoogleAdapter (3 kinds) and GoogleSweep delivered with the exact names
- [ ] Event, schedule and sweep tests, including single enumeration, pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
