# Go Google Drive Police (`telescreen-gdrive-police`)

An optional Go deployment of the Google Drive collector (`gdrive`, `gdocs`, `gsheets`).
It runs as a pull-mode Police worker that speaks only Police contract v1.2. The Python
collector in `src/telescreen/collectors/google/` is unchanged and stays the default.

Status: Draft, 2026-10-30. Issue reference: none. The source is the workflow input
(execution mode `issue-resolution`).

## Overview

The Bureau never imports out-of-process code, so this worker reaches it only through the
language-agnostic contract (`design-police-contract.md`, schemas in
`src/telescreen/contracts/police/v1/`). A `police` kind collector config, or a `map` step
with `via: police` (`design-map-reduce-sweeps.md`), dispatches tasks to it:

- `partition`: lists corpora and file-id shards.
- `scan_shard`: fetches each file, runs the rule detector locally and returns masked
  findings, `complete_locators`, `stats`, `resource_exposures` (1.1) and
  `resource_inventories` (1.2).
- `detect`: a bounded single-task scan of a selector.

No raw content, raw value, permission e-mail or token ever leaves the process.

```text
Bureau (unchanged)                                   Go worker (new, go/)
  police collector config / map via:police
     -> police_tasks queued  <-- claim (long-poll) --   police.PullWorker
                             -- heartbeat ---------->     | handlers: detect, partition, scan_shard
                             <-- result ------------      v
  ResultService (schema, Redactor, record_external)    gdrive.* -> gapi.{Drive,Sheets,Admin} -> Google
                                                         detect.* (rule port, masking, fingerprints)
```

Non-goals:

- ML and LLM detectors.
- `verify_remediation`.
- Push mode.
- The Drive changes feed and watch channels.
- Any change to the Python collector, the contract schemas, the Bureau, the database
  schema or the Python SDK.

## Details

### 1. Module and package layout

The module is `github.com/tacogips/telescreen/go` under `go/`, with
`go 1.27` and `toolchain go1.27.3`. Every package lives under `go/internal/`, has explicit
types and holds no package-level mutable state. Embedded, read-only lookup tables are
loaded into values that the constructors pass down.

| Package | Responsibility | Python reference |
|---------|----------------|------------------|
| `cmd/telescreen-gdrive-police` | Parse env config, wire the packages, handle signals, exit codes | `examples/police/python/private_db_police.py` |
| `internal/config` | Env parsing, secret-file reading, validation | `telescreen/core/config.py` (names) |
| `internal/police` | Contract DTOs and JSON encoding, `BureauClient`, `PullWorker` | `telescreen_police_sdk/{models,client,worker}.py` |
| `internal/googleauth` | Service-account key parsing, RS256 JWT bearer, token cache | `collectors/google/auth.py` |
| `internal/gapi` | Drive v3, Sheets v4 and Admin Directory clients, error mapping | `collectors/google/{drive,sheets,admin}.py` |
| `internal/gdrive` | Selector, matching, queries, exposure, locators, segments, resource fetch, `partition`/`scan_shard`/`detect` handlers, result assembly and guards | `collectors/google/{adapter,sweep,exposure}.py`, `telescreen_police_sdk/sweep.py` |
| `internal/detect` | NFKC normalization with offset map, rule catalog matchers, validators, overlap merge, `mask_value`, snippet sealing, Redactor, fingerprints | `detection/{normalize,rule_detector}.py`, `detection/rules/*`, `masking/{mask,seal,redact,fingerprint}.py`, `telescreen_police_sdk/masking.py` |
| `internal/pytext` | Python string semantics tables generated from the pinned Python (section 9.2) | `re`, `str` and `unicodedata` of Python 3.12 |

Dependencies:

- Go standard library.
- `golang.org/x/text`, used only for `unicode/norm`. It is pinned in `go.mod`/`go.sum`
  and vendored under `go/vendor/`, so builds and tests run offline.

No other modules are used, and no version is unpinned or `latest`.

### 2. Configuration (environment only)

Secrets are read only from environment variables or from files named by them. When a
secret accepts both `NAME` and `NAME_FILE`, setting both is a config error. Values are
never logged. Any invalid configuration exits with code 2 and a message that names the
variable but never its value.

| Variable | Required | Meaning (default) |
|----------|----------|-------------------|
| `TELESCREEN_URL` | yes | Bureau base URL; the client appends `/api/police/v1` |
| `TELESCREEN_POLICE_ENROLLMENT_TOKEN` / `_FILE` | yes | Enrollment token for `POST /workers/register` |
| `TELESCREEN_POLICE_FINGERPRINT_KEY` / `_FILE` | yes | Stable worker HMAC key, UTF-8 bytes, at least 32 bytes; used for `external_fingerprint` and `item_fingerprint` |
| `TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE` | yes | Path to the service-account JSON (`client_email`, `private_key`, `token_uri`) |
| `TELESCREEN_GOOGLE_DELEGATED_ADMIN` | no | Admin e-mail; enables domain-wide user and shared-drive corpora (same name and meaning as Python) |
| `TELESCREEN_INTERNAL_DOMAINS` | no | Comma list, lowercased; exposure classification (same as Python) |
| `TELESCREEN_SCAN_MAX_BYTES` | no | Content size limit (5242880, same as Python) |
| `TELESCREEN_SWEEP_SHARD_SIZE` | no | File ids per shard, 1-1000 (100, same as Python) |
| `TELESCREEN_POLICE_NAME` | no | Manifest `name` (`telescreen-gdrive-police`) |
| `TELESCREEN_POLICE_LABELS` | no | Comma list of `key=value` manifest labels (`police=gdrive`) |
| `TELESCREEN_POLICE_CONCURRENCY` | no | Manifest `concurrency`, 1-64 (4) |
| `TELESCREEN_GOOGLE_API_BASE_URL`, `TELESCREEN_GOOGLE_SHEETS_BASE_URL`, `TELESCREEN_GOOGLE_ADMIN_BASE_URL` | no | API base overrides (Google defaults; used by tests and egress proxies) |
| `TELESCREEN_POLICE_ALLOW_HTTP` | no | `true` allows `http://` for the Bureau URL, the API bases and the key's `token_uri` (dev and tests only). Otherwise every one of them must be `https://`, so bearer tokens never travel in clear text |

### 3. Contract client and worker loop (`internal/police`)

The worker matches `telescreen_police_sdk` except where section 13 lists a divergence.

- Startup registers once per process. The manifest is `{name, version, contract_version:
  "1.2", mode: "pull", task_types: ["detect", "partition", "scan_shard"], labels,
  concurrency}`. `version` is a Go constant. The registration response provides
  `worker_id`, `worker_token`, `heartbeat_interval_seconds` and `max_wait_seconds`.
  Credentials are kept only in memory. As with the Python example, each restart registers
  a new worker.
- Claim loop:
  - Claims are sent as `{max_tasks: free_slots, wait_seconds: max_wait_seconds}`.
  - Each claimed task runs in its own goroutine, up to `concurrency`. Its context has a
    timeout of `deadline - issued_at`, measured from when the claim was received. Both
    timestamps come from the Bureau's clock, so worker clock skew (and the E2E harness's
    `FakeClock`) cannot expire a task early. This "task budget" also bounds submit
    retries.
  - When no slot is free, the loop waits for a task to finish.
- Heartbeat loop:
  - Every `heartbeat_interval_seconds`, the worker posts `{task_ids}` for the active
    tasks.
  - Each id in `cancelled_task_ids` cancels that task's context. A cancelled task submits
    nothing, as in the SDK.
- Result submission:
  - The submission endpoint is `POST /tasks/{task_id}/result`.
  - Handler panics become a `failed` result with code `handler_error`. Unknown task types
    get `unsupported_task`. These codes match the SDK.
  - Error `detail` strings are static. They never contain exception text, ids or content.
- Bureau errors:
  - Responses are decoded only as `{error: {code}}`, like `BureauError(status, code)`.
  - Network errors, 5xx and 429 on claim, heartbeat or submit are retried with
    exponential backoff (1 s doubling to 30 s). Submit retries stop when the task budget
    runs out.
  - 401 and 403 on claim or heartbeat end the process with exit code 3, because the
    worker was disabled or its token revoked.
  - Other 4xx responses to a submit (409 task no longer open, 422) are logged by status
    and code and are not retried.
- Rate limiting reported to the Bureau: a handler that meets an upstream rate limit
  returns `status: failed, error: {code: "rate_limited", detail, retry_after_seconds?}`.
  `retry_after_seconds` is the parsed `Retry-After` value, sent only when it is finite and
  non-negative. The Bureau maps this result to `ShardFailure(RATE_LIMITED)`, which pauses
  the job without consuming an attempt.
- Shutdown:
  - SIGINT or SIGTERM stops claiming and cancels any claim request still in flight.
    Leases the Bureau granted to an aborted claim expire and are re-queued.
  - Active tasks are then allowed to finish, as `PullWorker.run` does. A second signal
    exits immediately.
- Logging: structured `log/slog` records go to stderr. They carry only task ids, task
  types, counts, HTTP status, contract error codes and durations. They never contain file
  content, file names, locators, permission e-mails, user e-mails, tokens, keys or Google
  response bodies.

### 4. Google authentication (`internal/googleauth`)

The worker uses the same flow as `auth.py`:

- Parse the key JSON. A parse failure gives a static error that does not echo the input.
- Build an RS256 JWT with `{iss: client_email, scope: space-joined scopes, aud: token_uri,
  iat, exp: iat+3600}`, plus `sub` when a delegation subject is given. The PEM private
  key may be PKCS#8 or PKCS#1. Signing uses `crypto/rsa` with SHA-256.
- POST the form `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=...`
  to `token_uri`.
- Cache the token by `(scopes, subject)`. A cached token is reused while
  `expires_at > now + 60 s`, and expired entries are pruned. The cache is a mutex-guarded
  field of the auth value, not package state.
- A token failure is a resource-level fetch failure (section 7). It is never logged with
  the assertion or the response.

Scopes:

| API | Scope | Subject |
|-----|-------|---------|
| Drive | `drive.readonly` | Corpus subject |
| Sheets | `spreadsheets.readonly` | Corpus subject |
| Admin `users.list`, user lookup | `admin.directory.user.readonly` | Delegated admin |
| `drives.list` | `drive.readonly` | Delegated admin |

### 5. Google API clients (`internal/gapi`)

The Drive client implements the same requests as `drive.py`, with identical query
parameters and `fields` masks:

- `files.list` with `q`, `corpora`, `pageSize` capped at 1000, `supportsAllDrives`,
  `includeItemsFromAllDrives`, optional `driveId` and `pageToken`.
- `files.get` (metadata).
- Paged `permissions.list`.
- `files.get?alt=media`.
- `files.export?mimeType=text/plain`.
- `drives.list` (`pageSize` 100).

Sheets implements `spreadsheets.get` (`fields=sheets(properties(title))`) and
`values:batchGet` (repeated `ranges`, `valueRenderOption=FORMATTED_VALUE`). Admin
implements `users.list` (`customer=my_customer`, `maxResults=500`) and the user lookup
(`fields=primaryEmail`). The user lookup is cached per process in a mutex-guarded field.

The changes feed (`changes.list`, `startPageToken`, `changes/watch`, `channels/stop`) is
not ported, because no Police task type carries a change cursor (decision D6).

Error mapping is shared by all three clients. Response bodies are parsed only to classify
rate limits and are never logged or returned.

| Upstream response | Go error | Python today |
|-------------------|----------|--------------|
| 404 | `ErrGone` | `ResourceGoneError` |
| 410 | `ErrFetchFailed` | fetch failed (same as Python; owner decision V1) |
| 429, or 403 with `error.errors[].reason` in {`rateLimitExceeded`, `userRateLimitExceeded`} | `RateLimited{RetryAfter}` | `RateLimited` |
| Other 403 | `ErrForbidden` | `FORBIDDEN` |
| Other non-2xx, transport error, invalid JSON | `ErrFetchFailed` | `FETCH_FAILED` |

Requests time out after 60 s. Media and export bodies are read through a limit of
`max_bytes + 1`, so oversize content is classified without buffering the whole file. The
classification is identical to Python's full download followed by a length check.

### 6. Selector document

`PoliceSelector.input` for this worker is a closed JSON object:

```json
{"kind": "gdrive", "drive_ids": ["0A..."], "folder_ids": [], "mime_types": []}
```

- `kind` is required and must be one of `gdrive`, `gdocs` or `gsheets`.
- The three lists are optional string arrays with the meaning of `DriveSelector`.
- Unknown members, wrong types or a missing `kind` fail the task with code
  `invalid_selector`.

A worker serves all three kinds. The Bureau-side config chooses the kind through
`selector.input`, and chooses the worker through `labels`.

### 7. Task handlers (`internal/gdrive`)

The matching rules mirror `adapter.py`:

- `_matches`, `_text_like` and `_included` are ported exactly.
- The query builder ports `GoogleSweep._query` and `_escape_query`.
- The shard key is lowercase hex `sha256(corpus_key + "\0" + ",".join(sorted file_ids))`.
- The descriptor is `{"corpus": corpus_key, "file_ids": [sorted ids]}` and the weight is
  the number of ids.
- The locator is `"<kind>:<file_id>"`, the same as `adapter._ref` `raw_locator`.

#### 7.1 `partition` (input `{sweep_id, corpus_key, cursor, selector}`)

- Root corpus (`corpus_key == ""`):
  - Without a delegated admin: one page with `sub_corpora` = `drive:<id>` for each
    selector `drive_ids` entry, then `folder:<id>` for each `folder_ids` entry, and
    `next_cursor: null`.
  - With a delegated admin: the same corpora as Python (`user:<id>` for each domain user,
    then `drive:<id>` for each shared drive), returned one upstream page per result. The
    worker cursor is `users:<token>`, then `drives:` or `drives:<token>`, and `null` at
    the end.
  - Python returns all corpora in one page. Paging here keeps every result under the
    Bureau's 1 MiB body limit. The final corpus set is the same, because sub-corpora
    insertion is idempotent (divergence V4).
- `user:`, `drive:` and `folder:` corpora: one `files.list` page with Python's `q`,
  `corpora`, `driveId` and subject.
  - Files are filtered by `!trashed && matches && included`.
  - Each page is chunked into `TELESCREEN_SWEEP_SHARD_SIZE` shards.
  - `next_cursor` is the upstream `nextPageToken`.
  - A page holds at most 1000 files, so a result holds at most 1000 shards.
- Failures:
  - `user:` corpus without a delegated admin: `sweep_unsupported`.
  - Unknown prefix: `invalid_corpus`.
  - Rate limit: `rate_limited`.
  - Any other upstream error: `partition_failed`.
  - Guard hit on any shard key, descriptor id, corpus key or cursor: `raw_value_guard`
    (section 8.3). A failed partition makes the sweep incomplete, so reconciliation never
    runs on a partial enumeration.

#### 7.2 `scan_shard` (input `{sweep_id, shard_key, descriptor, selector}`)

The descriptor is validated like `_read_descriptor`. An invalid descriptor gives a
`failed` result with `invalid_descriptor`. For `user:` corpora, the subject is resolved
through Admin.

If subject resolution fails without a rate limit, the result is `completed` with
`resources_failed = len(file_ids)` and nothing else (divergence V6). A rate limit there
fails the task with `rate_limited`.

Files are then processed one at a time, in descriptor order. Only one file's content is
held in memory at a time.

| Step / condition | Python `ShardEntry` | Go placement |
|------------------|---------------------|--------------|
| `files.get` gone (404) or `trashed` | `removed` | `complete_locators`, no findings, exposure or inventory |
| `files.get` other error, or file no longer `matches` | `failed` | `resources_failed += 1` |
| Rate limit at any step | `RateLimited` aborts the shard | Whole task `failed` with `rate_limited`; partial output discarded |
| Permissions gone | `removed` (`ResourceGoneError` propagates) | `complete_locators` (removed) |
| Permissions other error | Exposure `None`, continue | No exposure entry, continue |
| `gdrive` and not `text_like` (images, binaries) | `fetched`, `complete=false`, no segments | `complete_locators` + exposure, no findings (divergence V2) |
| Downloaded, exported or joined Sheets content larger than `max_bytes` | `fetched`, incomplete (skipped) | `resources_failed += 1` (divergence V3; contract gap G1) |
| Not valid UTF-8, export or Sheets error | `failed` | `resources_failed += 1` |
| Content scanned | `fetched`, complete | `complete_locators` + findings + exposure + inventory |

Content and segments:

- `gdocs` exports `text/plain`. `gdrive` text files are downloaded. Both are split into
  segments `L<n>` (1-based) with Python `str.splitlines` semantics (the line-break set in
  section 9.2).
- `gsheets`:
  - Get the titles.
  - Build the ranges `'<title with ' doubled>'!A:ZZZ` and call `values:batchGet`.
  - Each non-empty cell becomes the segment `<sheet>!R<r>C<c>`, where `<sheet>` is
    `range.split("!", 1)[0].strip("'")`.
  - The cell text follows Python `str()`. Strings are verbatim, booleans become
    `True`/`False`, integers are decimal, and floats use Python `repr` (fixed notation
    for exponents in [-4, 16) with `.0` for integral values, shortest scientific notation
    `d.ddde±XX` otherwise).
  - Size is the UTF-8 length of the joined segment text.
  - `item_count` is the number of rows with any non-empty cell.

Each segment is scanned separately, as in `ResourceScanner`. Multi-line rules such as
`private_key` therefore match only within a segment.

Shard accounting rules for a `completed` `scan_shard` result:

- `len(complete_locators) + resources_failed == len(file_ids)`. A Go test asserts this
  for every handler test.
- `resources_scanned` = `len(complete_locators)`.
- `bytes_scanned` = the sum of the content bytes scanned.

Any row of the table above that adds to `resources_failed` degrades the whole shard on the
Bureau side (section 8.1). This includes the V3 oversize, the 8.2 body guard and the 8.3
locator guard. With up to `TELESCREEN_SWEEP_SHARD_SIZE` ids per shard (default 100), one
failed file costs up to 99 other files in that shard their complete-scan state, and the
finding-free ones also lose their exposure and inventory in that sweep. The worker still
reports exact accounting and sends the same members; it does not try to work around this
Bureau rule.

#### 7.3 `detect` (input `{selector, collector_config_id}`)

`detect` is a bounded convenience scan, not a domain sweep.

- It enumerates the selector's `drive:` then `folder:` corpora with the service-account
  identity, using the filters and deduplication of 7.1.
- It processes up to 1000 matching files in enumeration order, because the contract allows
  at most 1000 `complete_locators`. Each file follows the 7.2 table, but failed files are
  simply omitted, because `detect` has no failure statistic or reconciliation.
- When more files match, the result `summary` reports only counts:
  `"scanned N; not scanned M (limit 1000); use a sweep"`.
- An empty selector (no drive or folder ids) completes with zero resources. Domain-wide
  coverage is only available through sweeps.
- A rate limit fails the task with `rate_limited`.

### 8. Result assembly and safety guards

#### 8.1 Members

- `findings[]`: `{locator, rule_id, rule_class, masked_snippet, location, confidence,
  external_fingerprint}`. The values are produced as described in 9.4.
- `resource_exposures[]`: for completed, non-removed resources with a classified
  exposure, `{locator, visibility, external_principal_count, shared_drive}`, computed by
  the exact port of `drive_exposure`.
- `resource_inventories[]`:
  - Completed, non-removed `gsheets` resources: `{locator, kind: "table", item_count}`.
  - Completed `gdrive` and `gdocs` resources: `{locator, kind: "file"}`.
  - This mirrors the Python collector, which reports only a Sheets row count and has the
    asset kinds of `inventory/observer._default_kind`.
  - Item fingerprints are not emitted, because the Python collector reports no Drive item
    keys. `item_fingerprint` is still implemented and parity-tested for the SDK contract.
- `complete_locators` and `stats` as in 7.2. `contract_version` is always `"1.2"`.

Members that are empty are omitted, as the SDK serializes them only when non-empty. Every
`exposures` and `inventories` locator is also a `complete_locators` entry or a finding
locator. What the Bureau keeps depends on `resources_failed`, under unchanged Bureau rules:

- `resources_failed == 0` (and every `detect` result): nothing is dropped. Each
  complete locator is recorded as a complete scan, and its exposure and inventory are
  applied.
- `scan_shard` with `resources_failed > 0`: `ResultService.accept` replaces
  `complete_locators` with `()` (`src/telescreen/police/results.py:259-260`).
  `allowed_locators` then holds only finding locators (`results.py:261-263`).
  - Exposures and inventories of finding-free resources (including removed ones) are
    dropped, and the drops are counted in the `police.result_accepted` audit
    (`exposures_dropped`, `inventories_dropped`).
  - Finding-bearing resources are recorded with their findings but with `complete=False`
    (`src/telescreen/issues/external_scan.py:112`), so none of their issues auto-resolve.
  - Removed resources in that shard do not auto-resolve either. The sweep becomes
    `partial`, so reconciliation does not run.

This is the conservative behavior the contract intends: a partial shard never resolves
anything. Its cost is described in 7.2 and in gaps G1 and G3.

#### 8.2 Body size guard

The Bureau accepts at most 1,048,576 bytes per body. After the result is assembled and
serialized, an oversized `scan_shard` result is shrunk:

- Resources move from complete to `resources_failed`, highest finding count first, until
  the result fits. Their findings, exposures and inventories are dropped.
- The resource is never marked complete with truncated findings, because that would
  auto-resolve issues whose findings were cut.
- `detect` drops such resources instead.
- The shrink count goes into `summary` (counts only). This is contract gap G3.
- A shrunk `scan_shard` now has `resources_failed > 0`, so the shard-wide degradation of
  8.1 applies.

#### 8.3 Redactor pre-submit guard

The Bureau scans every string leaf of a result with its `Redactor` and rejects any hit
with 422 `raw_value_detected` (`police/results.py` `_contains_hit`). The worker carries an
exact port of that Redactor (section 9) and applies it first:

- `masked_snippet`: built by the sealing port, which falls back to `"[REDACTED]"` when
  hits remain, as in `Masker.seal`. It is truncated to 200 code points.
- `location`: `redact(segment_locator)`. If hits remain, the value is `"[REDACTED]"`. It is
  truncated to 200 code points, the schema maximum.
- `external_fingerprint`: clean derivation (9.4). Hex HMAC strings can contain bounded
  digit runs that `phone_jp` or `my_number` match, because those rules have no word
  boundary. This is contract gap G4.
- A resource locator with a hit: the resource counts as failed and all of its entries are
  dropped.
- Partition strings: see 7.1.
- Backstop: if any leaf of the final envelope still hits, the worker submits
  `failed`/`raw_value_guard` instead.

### 9. Detection, masking and fingerprints (`internal/detect`)

#### 9.1 Behavior to reproduce (Python is the reference)

`RuleDetector.scan_text` over one segment:

1. Per-code-point NFKC with an index map (`normalize`), and `to_original` span mapping.
2. UUID spans.
3. For each rule in catalog order, find matches with Python `re.finditer` semantics over
   the normalized code points. Then apply in order: the validator on the normalized value
   group, the UUID-partial exclusion, and the keyword window (`lower()` of the normalized
   text, plus or minus `keyword_window`).
4. Stable sort by priority, descending. Priority is `(confidence, severity_for(class) ==
   high, span length)`.
5. Overlap merge. When all overlapping survivors have the same class, they merge into the
   covering span, taking the first maximal-priority id. Removal uses value equality.
6. Stable sort by span start.

All offsets are code-point indices, never bytes.

#### 9.2 Python string semantics tables (`internal/pytext`)

Go RE2 cannot express Python's lookbehind or lookahead. Go's `\b`, `\d`, `\w` and `\s` are
ASCII, while Python's are Unicode. Go's case folding and lowercasing also differ from
`str.lower`, `str.casefold` and `re.IGNORECASE`. The worker therefore does not depend on
Go's own Unicode semantics for any decision that affects parity.

`tools/gen_go_police_fixtures.py` evaluates the pinned Python on every code point and
writes compact range and mapping tables under `go/internal/pytext/data/`, which Go embeds:

- `re` `\w`, `\d` (with decimal value), `\s`.
- `str.isdigit`, `str.isspace` (for `strip`).
- `str.lower` and `str.casefold` mappings.
- For each `re.IGNORECASE` rule, the code points Python folds onto the rule's ASCII
  letters.
- The `str.splitlines` break set.

Each table header records the Python version and `unicodedata.unidata_version`.

Whole-string NFKC (fingerprint normalization) and per-code-point NFKC use
`golang.org/x/text/unicode/norm`. Its `norm.Version` must equal the recorded Python
`unidata_version` (15.0.0 for Python 3.12). A Go test asserts the equality. The pinned
x/text version is chosen to satisfy this under go1.27.3, and if none does, that is a
blocker to report, not something to work around.

Each catalog rule is a hand-written Go matcher. It combines RE2 sub-patterns, whose
character classes are expanded from the pytext tables, with code-point predicates for
`\b`, `(?<!...)` and `(?!...)`. Where trailing assertions or lazy quantifiers interact
with backtracking (`credit_card`, `email`, `phone_jp`, `address_jp`, `address_en`,
`password_assignment`), the matcher must reproduce Python's backtracking preference
order. The differential corpus (9.5) is the proof of parity.

The validators are ported exactly:

- Luhn and the IIN ranges over decimal values.
- The My Number check digit.
- Shannon entropy summed in first-occurrence order.
- The JWT header check. It uses non-strict URL-safe base64 with computed padding,
  rejects data lengths of 1 mod 4, requires strict UTF-8, and accepts the same JSON
  grammar as `json.loads`, including `NaN` and `Infinity`. The decoded value must be an
  object with an `alg` member.
- `is_placeholder`, using `str.strip` with Python whitespace.

#### 9.3 Masking and sealing

- `mask_value` ports `telescreen/masking/mask.py` (the Bureau version, not the SDK's
  `mask_value`). Its PEM header check uses `splitlines` and `strip`, `\D` removal uses the
  pytext decimal table, and `isdigit` uses the pytext digit table.
- Snippet sealing ports `Masker._snippet` and `Masker.seal`: a line-bounded window of plus
  or minus 40 code points with every overlapping finding masked, then `Redactor.redact`,
  the `"[REDACTED]"` fallback, and the 200 code-point cut.
- `Redactor.redact` and `has_hits` are ports of `masking/redact.py` over the same
  detector.

#### 9.4 Fingerprints

- `ExternalFingerprint(value, key)` = hex HMAC-SHA256(key, UTF-8 value), the same as SDK
  `masking.external_fingerprint`.
- `ItemFingerprint(itemKey, key)` = the same construction, the same as SDK
  `masking.item_fingerprint`.
- Finding `external_fingerprint`:
  1. `message = "v1\0" + rule_id + "\0" + normalize_for_fingerprint(raw, rule_class)` and
     `fp0 = ExternalFingerprint(message, key)`. Here `raw` is the original-text span, and
     `normalize_for_fingerprint` ports `Fingerprinter.normalize_for_fingerprint`:
     whole-string NFKC, then removal of spaces and hyphens for card, national ID and phone
     classes, or `str.lower` for e-mail.
  2. If the ported Redactor flags `fp0`, the worker uses
     `fp_n = ExternalFingerprint(message + "\0" + str(n), key)`, with the same `message`
     and `n` in decimal, for the first `n` in 1-16
     whose value is clean. If none of the 16 is clean, the resource counts as failed.
- The derivation is deterministic, so deduplication across scans holds. The Bureau re-keys
  these values in any case (`Fingerprinter.external`).

#### 9.5 Parity fixtures and envelope goldens

The generator and freshness check work as follows:

- `tools/gen_go_police_fixtures.py` imports the Python implementation, which is the
  reference. Run with `--write` it updates the files. Run with `--check` it exits
  non-zero on any difference.
- The Python test `tests/go_police/test_parity_fixtures_current.py` runs the check mode,
  so Python-side drift fails CI until the fixtures are regenerated.
- The script stays under 1000 lines (split into a helper module under `tools/` if
  needed) and follows the strict typing rules.

Fixtures go to `go/testdata/parity/*.json`:

| Fixture | Content | Python source of truth |
|---------|---------|------------------------|
| `exposure.json` | Permission sets × internal domains → visibility, count, shared drive | `drive_exposure` |
| `locators.json` | Kind and file id → locator; corpus and ids → shard key and descriptor; Drive `q` strings per kind, selector and corpus | `adapter._ref`, `sweep._shard`, `GoogleSweep._query` |
| `matching.json` | File metadata × selector × kind → `matches` and `included` | `_matches`, `_included`, `_text_like` |
| `segments.json` | Text → line segments; Sheets value ranges → cell segments, `item_count` | `_line_segments`, `_sheet_segments` |
| `fingerprints.json` | `external_fingerprint`, `item_fingerprint`, finding fingerprint, including at least one clean-derivation case | SDK `masking`, `Fingerprinter.normalize_for_fingerprint` |
| `detector.json` | Handpicked boundary cases per rule, plus a deterministic seeded corpus of at least 2000 strings built from rule fragments, Unicode neighbours, UUIDs, keywords and separators → `(rule_id, rule_class, start, end, confidence)` | `RuleDetector().scan_text` |
| `sealing.json` | Segment text and locator → `masked_snippet`, `location`, `external_fingerprint` per finding | `Masker`, `Redactor`, `mask_value` |
| `redactor.json` | Strings → `has_hits`, `redact` | `Redactor.default()` |

Text inputs are stored base64-encoded and the fixture key is a fixed, labelled test key.
No committed file contains a contiguous secret-shaped string, and the gitleaks pre-commit
hook must pass. Go test sources likewise build samples from fragments or from decoded
fixtures.

Envelope goldens go to `go/testdata/envelopes/*.json`:

- `manifest.json`, `claim-request.json`, `heartbeat-request.json`.
- `result-detect-findings.json` (1.0 members only).
- `result-detect-exposures.json` (adds 1.1 `resource_exposures`).
- `result-scan-shard.json` (findings, stats, `complete_locators`, exposures and 1.2
  `resource_inventories`).
- `result-partition.json` and `result-partition-root.json`.
- `result-failed-rate-limited.json`.

A Go test builds each envelope through the production encoders and compares it byte for
byte with the golden file (`-update` rewrites). The Python contract test validates them.

### 10. Tests

Go (`mise run go-test`, `go test ./...` with `httptest` fakes; no network):

- `police`: register, claim and heartbeat request shapes. Long-poll claim with empty
  responses. Cancellation through `cancelled_task_ids` stops the handler and submits
  nothing. Error envelope decoding. Backoff on 5xx and 429, exit on 401. Submit is not
  retried on 409 or 422. The task budget is derived from `deadline - issued_at`, and a
  worker clock skewed by hours still runs the task. Contract `examples/*.json` decode.
- `googleauth`: JWT claims with and without `sub`, signature verified by the fake token
  server with the public key, cache reuse and refresh at the 60 s margin, errors that do
  not echo key material.
- `gapi`: every request shape. 404, 410 (fetch failed, not gone), 429, 403 rate-limit reasons, 403 forbidden and
  5xx mapping. `Retry-After` parsing. Permission paging. The media limit.
- `gdrive`: every row of the 7.2 table, including the accounting identity, the root and
  corpus partition paging, `detect` truncation, `invalid_selector`, `invalid_descriptor`,
  the body size guard and the Redactor guard. These tests assert only what the worker
  submits. One mixed-shard case (one failed file, one clean file with findings, one
  finding-free file) asserts the accounting identity and that the worker still sends the
  members of 8.1. It makes no claim that the Bureau applies exposures or inventories for
  that shard; per 8.1 it drops them for the finding-free file.
- `detect`: all parity fixtures, the pytext and `norm.Version` assertion, and envelope
  goldens.
- Log leak test: run handlers over seeded secrets, e-mails and tokens, and assert that
  none of them, and no file name, appears in the captured `slog` output.

Python (`uv run pytest tests/go_police`):

- `test_parity_fixtures_current.py`: generator `--check`.
- `test_go_envelopes_contract.py`: validates every file in `go/testdata/envelopes/`. It
  uses `telescreen.contracts.validate` against the matching schema (`manifest`,
  `claim-request`, `heartbeat-request`, `result-envelope`) and validates partition
  `payload`s against `partition-result.schema.json`. It asserts that the expected file
  set is present, so it cannot pass vacuously, and that `contract_version` is `1.2`.
- `test_gdrive_police_e2e.py`:
  - Builds the binary with `go build` into `tmp_path`, with `go` from the mise-provided
    PATH, `cwd=go/`, `GOTOOLCHAIN=local` and `GOFLAGS=-mod=vendor`. A missing toolchain is
    a test failure, never a skip.
  - Serves the Bureau's real FastAPI app from the existing hermetic harness
    (`tests/e2e/harness.py`, SQLite) over loopback with `uvicorn.Server` in the test's
    event loop. Fake token, Drive, Sheets and Admin endpoints run on a stdlib
    `ThreadingHTTPServer` on loopback.
  - The fake token endpoint verifies the JWT against a key pair generated at runtime.
  - The fake estate has delegation, two users and one shared drive. It includes a text
    file with credential and e-mail samples, a Doc with a password assignment, a Sheet
    with card cells, an image, a file gone on `files.get` (404) and a trashed file, with
    mixed permissions. Every file is below `TELESCREEN_SCAN_MAX_BYTES` and has no fetch
    error, and every outcome in the estate is complete or removed. Every shard therefore
    has `resources_failed = 0`, which is the precondition (8.1) for the Bureau to apply
    exposures and inventories. The test asserts `resources_failed = 0` in the job's shard
    summaries before it asserts exposures and inventories.
  - The test creates a `police` collector config with the selector of section 6, starts
    `POST /api/collectors/{id}/sweep`, and drains engine tasks while the worker runs.
  - It asserts that the job `succeeded`; that findings were recorded under
    `police:<config>` with the expected rule classes; that exposures and inventories were
    applied (visibility, Sheets `item_count`, asset kinds); that the audit log has no
    `police.result_rejected` and no Bureau 422; and that no raw sample appears in worker
    stdout or stderr.
  - Every wait is bounded, with an overall limit of 180 s. The subprocess is terminated
    in `finally`.
- Sample secrets come from `tests/support/samples.py` fragments.

New Python files follow `python-coding-standards` and the strict typing rules in
`tools/typelint.py`, and each stays under 1000 lines.

### 11. Tooling

- `mise.toml`:
  - `[tools] go = "1.27.3"`.
  - Tasks run in `dir = "go"` with `GOTOOLCHAIN=local` and `GOFLAGS=-mod=vendor`:
    - `go-test`: `go test ./...`.
    - `go-lint`: fails if `gofmt -l` lists any file in `go list -f '{{.Dir}}' ./...`
      (vendor excluded), then runs `go vet ./...`.
    - `go-build`: `go build -trimpath -o bin/telescreen-gdrive-police
      ./cmd/telescreen-gdrive-police`.
  - `[tasks.ci]` becomes `fmt-check && lint && typelint && typecheck && go-lint &&
    go-test && go-build && test && build`.
- `.gitignore` adds `/go/bin/`.
- `go/vendor/` is committed (`go mod vendor`).
- `uv build` is unaffected, because the wheel packages only `src/telescreen` and
  `src/telescreen_police_sdk`.

### 12. Documentation deliverables

- This document.
- `go/README.md`: build and test commands, the env table of section 2, the run command,
  logging guarantees, and parity guarantees and limits. Parity is limited to the rule
  detector, and sections 13 and 14 apply.
- `examples/police/gdrive-go/README.md`:
  - Enrollment through the Bureau enrollment token and the worker env.
  - A `police` kind collector config JSON (`labels: ["police=gdrive"]`, the `input`
    selector of section 6).
  - Starting a sweep with `POST /api/collectors/{id}/sweep`, and a `map` step with
    `via: police` (`task_type: partition` / `scan_shard`).
  - Scaling: run N replicas with the same labels, set `TELESCREEN_POLICE_CONCURRENCY`,
    and bound the job with `max_concurrency`.
  - A note that findings appear under the source `police:<config>`, separate from any
    built-in `gdrive` collector on the same estate. Running both double-reports.
- `design-police-contract.md` (Examples section) and `architecture.md` section 10 point to
  this document.

### 13. Intentional divergences from the Python implementation

| Id | Divergence | Reason |
|----|------------|--------|
| V1 | Withdrawn by owner decision (2026-10-30): HTTP 410 is NOT gone. Only 404 maps to `ErrGone`, exactly like Python; 410 maps to `ErrFetchFailed` | The workflow input wrongly stated that Python maps 410 to gone. Parity with Python wins, and treating 410 as removal could auto-resolve open issues |
| V2 | Unsupported-type `gdrive` files (images, binaries) are reported complete, with exposure and without findings; Python records an incomplete, skipped scan | The contract has no "skipped" channel. Counting them as failed would make every sweep that contains an image `partial`. They can never carry findings, so a complete scan with none resolves nothing that a Python scan would keep open |
| V3 | Oversized content counts in `resources_failed`; Python records a skipped scan | The contract says that anything not fully scanned counts as failed. A complete report would falsely auto-resolve issues found earlier (gap G1). Consequences: estates with text files above `TELESCREEN_SCAN_MAX_BYTES` sweep `partial`. Because the Bureau discards `complete_locators` for any shard with `resources_failed > 0` (`police/results.py:259-260`), one oversized file also costs up to `TELESCREEN_SWEEP_SHARD_SIZE - 1` other files in its shard their complete-scan state; the finding-free ones also lose their exposure and inventory for that sweep (8.1) |
| V4 | The delegated root partition is paged with a worker cursor | Keeps every result within the 1 MiB body limit; idempotent sub-corpora give the same final set |
| V5 | Bureau network, 5xx and 429 errors back off; the SDK raises out of `run` | A long-running binary would otherwise re-register a new worker on every restart |
| V6 | A `scan_shard` that cannot start enumerating (subject lookup) counts all descriptor ids as failed; the SDK counts 1 | Exact accounting per the contract |
| V7 | A finding fingerprint that the Redactor flags uses the clean derivation | Otherwise the Bureau rejects the whole result (gap G4) |
| V8 | The body size guard moves resources to failed | 1 MiB limit (gap G3). The shard then has `resources_failed > 0`, so the shard-wide loss of V3 and 8.1 applies |
| V9 | `location` falls back to `[REDACTED]` and is cut to 200 code points | Schema `maxLength` and the Bureau's rejection of any hit |
| V10 | No owner, placement, `modified_at` or coverage state is sent | Contract 1.2 has no members for them (gap G2) |
| V11 | The changes feed and watch are not ported; `detect` is bounded to the selector's drives and folders and 1000 files | No task type uses a change cursor, and `complete_locators` allows at most 1000 entries |

### 14. Contract gaps and open questions

These gaps were found against contract 1.2 as published. No schema is changed. Each has
the default described above and is recorded for the owner in
`design-docs/user-qa/pending-go-gdrive-police-contract-gaps.md`.

- G1: no channel to report "checked but not fully scanned" (`resources_skipped`, coverage).
  Combined with the Bureau rule that a `scan_shard` with `resources_failed > 0` loses all
  of its `complete_locators` (`police/results.py:259-260`), one oversized or failed file
  degrades its whole shard:
  - up to `TELESCREEN_SWEEP_SHARD_SIZE - 1` other files are not recorded as complete;
  - finding-free files lose their exposure and inventory for that sweep;
  - finding-bearing files are recorded as incomplete (`issues/external_scan.py:112`);
  - the sweep ends `partial`.
- G2: no owner identity, placement or modified-time members.
- G3: results are capped at a 1 MiB body with no multi-part submission. A single resource
  with enough findings can never be covered by a Police worker. Each guard-moved resource
  makes its shard fail, with the same shard-wide loss as G1.
- G4: the Bureau `Redactor` scans fingerprint strings. `phone_jp` and `my_number` can
  match digit runs inside hex HMAC values, which affects the Python SDK equally. This gap
  is pre-existing. The estimated hit rate is about 1% per fingerprint; it is to be
  confirmed by the parity tests.
- G5: `complete_locators` allows at most 1000 entries, which bounds `detect`.

### 15. Risks

- Regex backtracking parity on the six complex rules. Mitigation: hand-written matchers,
  the differential corpus, and Python-generated tables.
- Unicode version drift: Python 3.12 has 15.0.0, which must match the pinned x/text. The
  Go test asserts the equality, and the fixture freshness test catches Python upgrades.
- Security: the handling of keys, tokens and content. Mitigations: the `https` rule,
  env-only secrets, the log leak test, the pre-submit Redactor guard, and adversarial
  review of `detect`, `gdrive` and `police`.
- E2E timing. The harness `FakeClock` and real worker time interact on leases and
  deadlines. Waits are bounded and the subprocess is cleaned up deterministically.
- Toolchain: `mise install` must provide go 1.27.3. Vendoring removes the need for module
  downloads.
- `uvicorn` serving the harness app in the test loop is new. If the harness cannot be
  served this way, the fallback is a dedicated SQLite app built from the same `wiring`.
  The plan must decide this before implementation.

### 16. Acceptance and verification

The acceptance signals are those of the intake. Evidence is recorded with the final exit
status and the full log path of each command, and only commands that execute tests count:

- `mise run go-lint`
- `mise run go-test`
- `mise run go-build`
- `uv run pytest tests/go_police -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python tools/typelint.py`
- `uv run ty check`
- `mise run ci`
- `mise exec -- pre-commit run gitleaks`, on the staged changes before commit

## References

- `design-docs/specs/design-police-contract.md`
- `design-docs/specs/design-map-reduce-sweeps.md`
- `design-docs/specs/design-masking.md`
- `design-docs/specs/design-detectors.md`
- See `design-docs/references/README.md`.
