# GDP-P06 Drive Handlers: partition, scan_shard, detect, Result Assembly and Guards

**Status**: Ready
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 6, 7.1-7.3, 8.1-8.3, 13, 14
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p06-gdrive-handlers`
- planPath: `impl-plans/active/gdp-p06-gdrive-handlers.md`
- wave: 2
- dependsOn: `gdp-p01-foundation`, `gdp-p02-parity-fixtures`
- writePaths:
  - `impl-plans/active/gdp-p06-gdrive-handlers.md`
  - `go/internal/gdrive`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8. Use only the gdp-p01 interfaces
(`gapi.Drive`, `gapi.Sheets`, `gapi.Admin`, `detect.Engine`, `police.*`) and
`*pytext.Tables`. Do not import the concrete implementations of `gapi` or `detect`;
gdp-p04 and gdp-p08 build them in parallel. Tests use fakes and load tables with
`pytext.Load(os.DirFS("../pytext/data"))`, not `pytext.Default()`.

## Intent and Context

This is the Drive-specific behavior of the worker: turning Police tasks into Google
calls and masked, contract-valid results. The Python references, which are behavior to
mirror exactly:

- `src/telescreen/collectors/google/adapter.py`: `_matches`, `_text_like`,
  `_fetch_content`, `_ref`, `_line_segments`, `_sheet_segments`, `_drive_selector`.
- `src/telescreen/collectors/google/sweep.py`: `GoogleSweep.partition`,
  `shard_resources`, `_root_page`, `_query`, `_shard`, `_read_descriptor`, `_included`,
  `_escape_query`.
- `src/telescreen/collectors/google/exposure.py:drive_exposure`.
- `src/telescreen/inventory/observer.py:_default_kind` (gsheets -> table, others -> file).
- Bureau acceptance: `src/telescreen/police/results.py:ResultService.accept`
  (lines 245-300). For `resources_failed > 0` it discards `complete_locators`, so
  exposures and inventories of finding-free resources are dropped (design 8.1).

## Non-goals

- No Google HTTP code (gdp-p04), no detection or masking code (gdp-p05, gdp-p08), no
  worker loop (gdp-p03), no env parsing (gdp-p07).
- No changes feed. No owner, placement or `modified_at` emission (design V10, V11).
- No workaround for the Bureau's failed-shard rule. Send the members of 8.1 as designed.

## Deliverables (exported API is a contract for gdp-p10)

```go
type Config struct { InternalDomains []string; MaxBytes int; ShardSize int }
func NewService(cfg Config, drive gapi.Drive, sheets gapi.Sheets, admin gapi.Admin, engine detect.Engine, tables *pytext.Tables, logger *slog.Logger) *Service
func (s *Service) Handlers() map[string]police.Handler // keys: detect, partition, scan_shard
```

`admin` is nil when no delegated admin is configured. Compare with `admin == nil`;
callers pass an untyped nil.

Suggested files: `service.go`, `selector.go`, `match.go`, `exposure.go`, `segments.go`,
`fetch.go`, `partition.go`, `scanshard.go`, `detecthandler.go`, `assemble.go`, plus
`*_test.go` and `fakes_test.go`. Keep each file under about 400 lines.

1. Selector (design 6): parse `input.selector` as a closed object. `kind` is required and
   must be `gdrive`, `gdocs` or `gsheets`. `drive_ids`, `folder_ids` and `mime_types` are
   optional string arrays. Use `json.Decoder.DisallowUnknownFields` and check for a
   trailing token. Any violation -> `failed` with `CodeInvalidSelector`.
2. Matching and queries: exact ports of `_matches`, `_text_like`, `_included`,
   `_escape_query` and `_query`, plus the base `q` per corpus prefix from `partition`.
   `page_size` is 1000.
3. Exposure: the exact `drive_exposure` port.
   - Domains go through `tables.CaseFold` after `tables.Lower`, mirroring
     `core/config.py` `lowercase=True` followed by `casefold`.
   - The principal domain is `rsplit("@", 1)[-1]` casefolded; there is none when the
     address has no `@`.
   - `shared_drive = DriveID != nil`.
4. Locators and shards:
   - The locator is `kind + ":" + fileID`.
   - The shard key is lowercase hex `sha256(corpus + "\x00" + strings.Join(sortedIDs,
     ","))`.
   - Sort with byte order, which equals Python code-point order for valid UTF-8.
   - The descriptor is `{corpus, file_ids: sorted}` and the weight is `len`.
5. Segments:
   - Lines are `tables.SplitLines` with locators `L1..Ln`.
   - Sheets cells:
     - Text `str()` is verbatim for strings and `True`/`False` for bools.
     - For numbers: when the literal has no `.`, `e` or `E` it is an int, printed as
       canonical decimal via `big.Int`, so `-0` becomes `0`.
     - Otherwise it is a float printed as Python `repr`: shortest round-trip digits via
       `strconv.FormatFloat(f, 'e', -1, 64)`, then re-laid out as fixed notation when the
       exponent is in [-4, 16), with `.0` appended for integral values, and otherwise as
       `d.ddde+XX` or `d.ddde-XX` with at least two exponent digits.
   - Empty texts are skipped. The sheet name is `strings.SplitN(range, "!", 2)[0]` with
     `'` trimmed from both ends (Python `strip("'")`).
   - Range strings are `'<title with ' doubled>'!A:ZZZ`.
   - `item_count` is the number of rows with any non-empty cell text.
6. Fetch (`fetch.go`): one resource maps to exactly one outcome: `complete`, `removed`,
   `failed`, or `rateLimited` (carrying `RetryAfterSeconds`). Apply the design 7.2 table
   row by row, in Python order:
   - `FilesGetMeta`: `GoneError` or `trashed` -> removed. A rate limit -> rateLimited.
     Any other error -> failed. If the file does not match -> failed.
   - `ListPermissions`: `GoneError` -> removed; a rate limit -> rateLimited; another
     error -> no exposure, continue.
   - `gdrive` and not text-like -> complete, with exposure, no findings and kind `file`.
   - `gdocs`: `Export(text/plain)`. `gsheets`: titles, then `ValuesBatchGet`. `gdrive`:
     `Download`.
   - Oversize (above `MaxBytes`, measured as the UTF-8 bytes of the joined Sheets
     segment text) -> failed. Invalid UTF-8 (`utf8.Valid`) -> failed.
   - Errors on export, download or Sheets reads: `ForbiddenError` or `FetchFailedError`
     -> failed; `GoneError` -> removed. Python's `ResourceGoneError` is not a
     `TelescreenError`, so it propagates out of `_fetch_content` into
     `shard_resources`, which yields REMOVED. A 410 is a `FetchFailedError` (owner
     decision V1), so it ends up failed.
   - Scanned: call `engine.ScanSegment` for each segment. If it returns `ok=false` ->
     failed. Locator hit `engine.HasHits(locator)` -> failed (design 8.3).
7. `partition` (design 7.1):
   - Root without admin -> one page of `drive:` then `folder:` corpora, with cursor null.
   - Root with admin: cursor `""`/null -> users page 1. Cursor `users:<tok>` -> the next
     users page. When users are exhausted, the next cursor is `drives:`. `drives:` or
     `drives:<tok>` -> a drives page via `DrivesList(token, admin.AdminSubject())`. The
     final cursor is null.
   - Corpora: `files.list` page, filter, chunk by `ShardSize`; `next_cursor` is the
     upstream token.
   - A `user:` corpus with admin nil -> `CodeSweepUnsupported`. Unknown prefix ->
     `CodeInvalidCorpus`. A rate limit -> `CodeRateLimited` with `retry_after_seconds`.
     Other errors -> `CodePartitionFailed`.
   - Guard: `engine.HasHits` on any shard key, descriptor id, corpus key or cursor ->
     `CodeRawValueGuard`.
8. `scan_shard` (design 7.2):
   - Descriptor validation as `_read_descriptor`: an object with string `corpus` and
     `file_ids` all strings; otherwise `CodeInvalidDescriptor`.
   - `user:` corpora resolve the subject via `admin.UserEmail`. A rate limit there ->
     failed `rate_limited`. Any other failure -> completed with
     `resources_failed = len(file_ids)` and nothing else.
   - Process files sequentially in descriptor order.
   - Any `rateLimited` outcome -> discard everything and return failed `rate_limited`.
   - Otherwise assemble the result.
9. `detect` (design 7.3):
   - Enumerate `drive:` then `folder:` corpora with no subject, using the 7.1 filters.
   - Scan up to 1000 files and omit failed ones.
   - The summary is `"scanned N; not scanned M (limit 1000); use a sweep"` when
     truncated, and empty otherwise.
   - A rate limit -> failed `rate_limited`.
10. Assembly (`assemble.go`, design 8.1-8.2):
    - Findings come from `engine` results with `locator` set.
    - Exposures only for complete, non-removed resources with an exposure.
    - Inventories: gsheets `{locator, kind: "table", item_count}`; gdrive and gdocs
      `{locator, kind: "file"}`; none for removed resources.
    - `complete_locators` includes removed resources.
    - Stats: `resources_scanned = len(complete)`, `resources_failed`, `bytes_scanned`
      (sum of the content bytes scanned).
    - Size guard: encode with `police.EncodeResult`. While the encoded size is above
      1048576, move the complete resource with the most findings (ties: last in
      descriptor order) to failed and drop its entries. `detect` drops the resource
      instead. Append the shrink count to `summary` (counts only).
    - Backstop: if `engine.HasHits` is true for any string leaf of the final envelope ->
      failed `CodeRawValueGuard`.

## Key Pitfalls

- Accounting identity: for every completed `scan_shard`,
  `len(complete_locators) + resources_failed == len(file_ids)`. Enforce it in one place
  and assert it in tests.
- Removed resources go into `complete_locators` with no exposure and no inventory.
- Do not log locators, file ids, names, e-mails or content. Log the task id, counts and
  codes only.
- `json.RawMessage` input: parse into typed structs. Do not use `map[string]any`
  traversal for known members.
- The Sheets range quoting and the sheet-name strip are easy to get subtly wrong. They
  are checked by `segments.json`.
- No package-level mutable state. Fakes live in `_test.go` files.

## Tests (fakes for `gapi.*` and `detect.Engine`; parity fixtures from `../../testdata/parity/`)

- Parity:
  - `exposure.json` -> identical visibility, count and shared drive.
  - `locators.json` refs, shards and queries -> identical. Each `queries[]` case runs the
    partition with a recording fake Drive and compares the captured `FilesListParams`.
  - `matching.json` and `segments.json` -> identical.
  - Each loader asserts a non-zero case count.
- 7.2 table, one test per row:
  - gone meta -> complete (removed), no exposure;
  - trashed -> same;
  - meta 500 -> failed;
  - non-matching -> failed;
  - permissions 404 -> removed;
  - permissions 500 -> complete without exposure;
  - image -> complete with exposure, no findings;
  - oversize -> failed;
  - invalid UTF-8 -> failed;
  - export Gone -> removed;
  - engine `ok=false` -> failed;
  - locator hit -> failed;
  - a 429 on the third file -> failed task `rate_limited` with `retry_after_seconds`
    copied, no findings.
- Mixed shard (one failed file, one clean file with findings, one finding-free file) ->
  the accounting identity holds, and the worker result contains exposure and inventory
  entries for both complete files. Add a comment citing design 8.1: the Bureau will drop
  the finding-free file's entries. Do not assert anything about Bureau behavior here.
- Partition:
  - root non-delegated;
  - root delegated across two users pages and two drives pages (cursor sequence
    `users:` -> `drives:` -> null);
  - a corpus page of 250 files with shard size 100 -> 3 shards;
  - user corpus without admin -> `sweep_unsupported`;
  - `bogus:x` -> `invalid_corpus`;
  - an engine hit on a cursor -> `raw_value_guard`.
- Selector: unknown member, missing kind or wrong type -> `invalid_selector`.
- Descriptor: `file_ids` containing a number -> `invalid_descriptor`.
- Subject lookup failure -> completed with `resources_failed == len(file_ids)`.
- Size guard: a fake engine emitting large snippets -> the result fits within 1048576
  bytes, the heaviest resources are moved to failed, and the identity holds.
- detect: 1005 matching files -> 1000 scanned and a truncation summary. An empty
  selector -> completed with zero resources.
- Log leak: run `scan_shard` with content containing sample secrets (built from
  fragments), e-mails and a file name. The captured `slog` buffer contains none of them
  and no file id.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -race -count=1 ./internal/gdrive/... 2>&1 | tee /tmp/telescreen-gdp-p06-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/gdrive/... 2>&1 | tee /tmp/telescreen-gdp-p06-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/gdrive   # must print nothing
```

All commands exit 0, and the log shows `ok .../internal/gdrive`.

## Completion Criteria

- [ ] `NewService` and `Handlers` exist with the signatures above, and the package
      imports only the gdp-p01 contract packages plus the stdlib.
- [ ] Every test above passes with `-race`, including the four parity fixture files.
- [ ] `vet` and `gofmt` are clean, and nothing outside `go/internal/gdrive` changed.
- [ ] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
