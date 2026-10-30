# Go Google Drive Police

This optional pull-mode worker covers Google Drive files, Docs and Sheets through Police contract 1.2. The Python collector remains the default. Mode: pull.

Build and check from the repository root with `mise run go-build`, `mise run go-test`, and `mise run go-lint`. Run `go/bin/telescreen-gdrive-police` after configuring the environment. Exit codes: 0 for graceful shutdown, 2 for invalid configuration, 3 for unauthorized worker access.

## Environment

Secrets come from environment variables or files named by them. Set exactly one of each secret's value and `_FILE` form; files lose one trailing newline. The fingerprint key must contain at least 32 UTF-8 bytes and remain stable across scans.

| Variable | Required | Meaning or default |
| --- | --- | --- |
| `TELESCREEN_URL` | yes | Bureau base URL |
| `TELESCREEN_POLICE_ENROLLMENT_TOKEN` / `TELESCREEN_POLICE_ENROLLMENT_TOKEN_FILE` | yes | Enrollment secret |
| `TELESCREEN_POLICE_FINGERPRINT_KEY` / `TELESCREEN_POLICE_FINGERPRINT_KEY_FILE` | yes | Stable HMAC key |
| `TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE` | yes | Service account JSON path |
| `TELESCREEN_GOOGLE_DELEGATED_ADMIN` | no | Delegated admin email; enables domain corpora |
| `TELESCREEN_INTERNAL_DOMAINS` | no | Comma-separated internal domains, trimmed and lowercased |
| `TELESCREEN_SCAN_MAX_BYTES` | no | Maximum content bytes; 5242880, minimum 1 |
| `TELESCREEN_SWEEP_SHARD_SIZE` | no | IDs per shard; 100, range 1-1000 |
| `TELESCREEN_POLICE_NAME` | no | Worker name; `telescreen-gdrive-police`, 1-100 characters |
| `TELESCREEN_POLICE_LABELS` | no | Comma-separated labels; `police=gdrive` |
| `TELESCREEN_POLICE_CONCURRENCY` | no | Worker slots; 4, range 1-64 |
| `TELESCREEN_GOOGLE_API_BASE_URL` | no | Drive base; `https://www.googleapis.com` |
| `TELESCREEN_GOOGLE_SHEETS_BASE_URL` | no | Sheets base; `https://sheets.googleapis.com` |
| `TELESCREEN_GOOGLE_ADMIN_BASE_URL` | no | Admin base; `https://admin.googleapis.com` |
| `TELESCREEN_POLICE_ALLOW_HTTP` | no | Set to `true` for HTTP in development and tests; otherwise HTTPS is required for Bureau, API bases and token URI |

Labels have lowercase keys matching `[a-z0-9_.-]+` and values matching `[A-Za-z0-9_.:/-]+`. Configuration errors name the variable and never its value.

## Selector and logging

`selector.input` is a closed JSON object such as `{"kind":"gdrive","drive_ids":["0AExampleDriveId"],"folder_ids":[],"mime_types":[]}`. The required `kind` is `gdrive`, `gdocs` or `gsheets`; the three optional lists contain strings. Unknown members, invalid types or missing kind produce `invalid_selector`. Labels select workers.

Logs contain only task IDs, types, counts, HTTP status, contract error codes and durations. They do not contain content, file names, locators, permission or user emails, tokens, keys or Google response bodies. Results carry masked snippets; the pre-submit Redactor guard fails unsafe results with `raw_value_guard`, and unsafe locations fall back to `[REDACTED]`.

## Parity and limits

Python-generated fixtures support parity for the rule detector, masking, exposure, locators and fingerprints. ML and LLM detectors are out of scope. The deliberate differences are:

- V1: withdrawn by owner decision; only 404 is gone, while 410 is a fetch failure, as in Python.
- V2: unsupported binary types complete with exposure and no findings.
- V3: oversized content counts as failed, with the shard-wide consequences below.
- V4: delegated root partition uses a paged worker cursor.
- V5: Bureau network, 5xx and 429 errors back off.
- V6: failed subject lookup counts all descriptor IDs as failed.
- V7: flagged fingerprints use deterministic clean re-derivation.
- V8: the size guard moves heavy resources to failed.
- V9: location falls back to `[REDACTED]` and is limited to 200 code points.
- V10: owner, placement, modified time and coverage state are absent.
- V11: changes feed and watch are absent; detect covers selected drives and folders up to 1000 files.

## Contract gaps

- G1: no skipped or coverage channel. Any `resources_failed > 0`, including V3, causes the Bureau to discard the whole shard's complete locators; finding-free files also lose exposure and inventory, finding-bearing files are incomplete, and the sweep is partial. Up to shard size minus one other files are affected.
- G2: owner, placement and modified time have no contract members.
- G3: the 1 MiB result limit has no multipart submission. Guard-moved resources cause the same shard-wide loss as G1; a sufficiently large single resource cannot be covered.
- G4: the Bureau Redactor can flag hex fingerprints; deterministic re-derivation mitigates rejection.
- G5: the 1000 complete-locator limit bounds detect; use a sweep for wider coverage.

See the [design](../design-docs/specs/design-go-gdrive-police.md) and [open owner questions](../design-docs/user-qa/pending-go-gdrive-police-contract-gaps.md).
