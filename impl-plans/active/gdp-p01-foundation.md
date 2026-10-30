# GDP-P01 Foundation: Go Module, Toolchain, Vendored x/text, Cross-Package Contracts

**Status**: Ready
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 1, 3, 5, 8.1, 9.2, 11
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p01-foundation`
- planPath: `impl-plans/active/gdp-p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/gdp-p01-foundation.md`
  - `mise.toml`
  - `.gitignore`
  - `go/go.mod`
  - `go/go.sum`
  - `go/vendor`
  - `go/internal/unorm/unorm.go`
  - `go/internal/unorm/unorm_test.go`
  - `go/internal/police/contract.go`
  - `go/internal/police/contract_test.go`
  - `go/internal/gapi/types.go`
  - `go/internal/gapi/types_test.go`
  - `go/internal/detect/api.go`
  - `go/internal/pytext/tables.go`
  - `go/internal/pytext/tables_test.go`
  - `go/internal/pytext/testdata`
- sharedPaths: none
- artifactRoots: none
- Artifact strategy:
  - The Go toolchain (the mise install directory), `GOMODCACHE` and `GOCACHE` all live
    outside the repository, so nothing in the repository is an artifact root.
  - `go/vendor` is COMMITTED source produced by `go mod vendor` (design sections 1 and
    11); every `-mod=vendor` build depends on it. It is tracked as a plain source
    writePath, `go/vendor/modules.txt` included, and stays small: about 20 files, under
    512 entries, no file over 8,000,000 bytes.

Read `impl-plans/conventions.md` sections 2, 7 and 8 first. Sections 3-6 are for Python and do not apply here.
This is the ONLY plan that may run `go get`, `go mod tidy` or `go mod vendor`, or edit
`go/go.mod`, `go/go.sum`, `go/vendor`, `mise.toml` and `.gitignore`. Every other plan
that needs a module change writes a "Reconciliation request" instead.

## Intent and Context

The work adds an optional Go deployment of the Google Drive collector, running as a
pull-mode Police worker. The design is `design-docs/specs/design-go-gdrive-police.md`.
This plan creates the module and the toolchain gates. It also pins every type and
interface that the wave-2 plans share, so those plans can work in parallel with disjoint
files:

- `police` DTOs (contract 1.2 wire shapes) and the handler signature.
- `gapi` data types, typed errors and the `Drive`, `Sheets` and `Admin` interfaces.
- `detect` data types plus the `Scanner` and `Engine` interfaces.
- The `pytext` table loader API and its on-disk data format. `gdp-p02` produces the
  data.
- The `unorm` wrapper over `golang.org/x/text/unicode/norm`, which is the only external
  dependency.

## Non-goals

- No HTTP client, worker loop, Google client, detector, handler or `cmd` code (wave 2+).
- No `go/internal/pytext/embed.go` and no `go/internal/pytext/data/` (gdp-p05, gdp-p02).
- No change to any Python source, contract schema or `pyproject.toml`.
- Do not run `mise run go-build` or `mise run ci`. `cmd/` does not exist yet, so both are
  expected to fail until gdp-p10.

## Deliverables and File-Level Changes

1. `mise.toml`.
   - Add `go = "1.27.3"` under `[tools]`.
   - Add three tasks. Each has `dir = "go"` and
     `env = { GOTOOLCHAIN = "local", GOFLAGS = "-mod=vendor" }`:
     - `go-test`: `go test ./...`.
     - `go-lint`: compute `files=$(gofmt -l $(go list -f '{{.Dir}}' ./...))`, print the
       files and exit 1 when non-empty, then run `go vet ./...`. Vendor is excluded
       because `go list ./...` excludes it.
     - `go-build`: `go build -trimpath -o bin/telescreen-gdrive-police ./cmd/telescreen-gdrive-police`.
   - Change `[tasks.ci].run` to exactly `mise run fmt-check && mise run lint && mise run
     typelint && mise run typecheck && mise run go-lint && mise run go-test && mise run
     go-build && mise run test && mise run build`.
   - Do not touch any other task.
2. `.gitignore`: append a `## go` block with `/go/bin/`.
3. `go/go.mod`:
   - Directives: `module github.com/tacogips/telescreen/go`, `go 1.27`,
     `toolchain go1.27.3`.
   - One `require golang.org/x/text vX.Y.Z` at an exact version (see pitfalls).
   - Produce `go/go.sum` and `go/vendor/` with `go mod tidy` and then `go mod vendor`.
4. `go/internal/unorm/unorm.go` (package `unorm`):
   - `func NFKC(s string) string` (whole string).
   - `func NFKCRune(r rune) string` (one code point).
   - `func Version() string` returns `norm.Version`.
   - No other API.
5. `go/internal/police/contract.go` (package `police`). The exact shapes below are the
   wire contract; json tags must match the schema member names.
   - `const ContractVersion = "1.2"`.
   - Task types: `TaskDetect = "detect"`, `TaskPartition = "partition"`,
     `TaskScanShard = "scan_shard"`.
   - Error codes: `CodeRateLimited = "rate_limited"`, `CodeHandlerError =
     "handler_error"`, `CodeUnsupportedTask = "unsupported_task"`, `CodeInvalidSelector =
     "invalid_selector"`, `CodeInvalidDescriptor = "invalid_descriptor"`,
     `CodeInvalidCorpus = "invalid_corpus"`, `CodeSweepUnsupported =
     "sweep_unsupported"`, `CodePartitionFailed = "partition_failed"`,
     `CodeRawValueGuard = "raw_value_guard"`.
   - Status constants: `StatusCompleted = "completed"`, `StatusFailed = "failed"`.
   - `Manifest{Name, Version, ContractVersion, Mode string; TaskTypes []string; Labels []string; Concurrency int}`.
     Tags: `name`, `version`, `contract_version`, `mode`, `task_types`, `labels`,
     `concurrency`. `Labels` must encode as `[]`, never `null`.
   - `Registration{WorkerID, WorkerToken string; LeaseSeconds, HeartbeatIntervalSeconds, MaxWaitSeconds int}`.
     Tags: `worker_id`, `worker_token`, `lease_seconds`, `heartbeat_interval_seconds`,
     `max_wait_seconds`.
   - `ClaimRequest{MaxTasks int `json:"max_tasks"`; WaitSeconds int `json:"wait_seconds"`}`.
   - `HeartbeatRequest{TaskIDs []string `json:"task_ids"`}`.
   - `TaskEnvelope{ContractVersion, TaskID, TaskType string; Attempt int; IssuedAt, Deadline time.Time; Labels []string; Input json.RawMessage}`.
     Tags: `contract_version`, `task_id`, `task_type`, `attempt`, `issued_at`,
     `deadline`, `labels`, `input`.
   - `MaskedFinding{Locator, RuleID, RuleClass, MaskedSnippet, Location string; Confidence float64; ExternalFingerprint string}`,
     with snake_case tags.
   - `ResourceExposure{Locator, Visibility string; ExternalPrincipalCount int; SharedDrive bool}`.
     Count and shared drive are always emitted (no `omitempty`).
   - `ResourceInventory{Locator string; Kind string `json:"kind,omitempty"`; ItemCount *int `json:"item_count,omitempty"`}`.
     There is deliberately no `item_fingerprints` field (design 8.1).
   - `ShardStats{ResourcesScanned, ResourcesFailed, BytesScanned int}`.
   - `ResultError{Code, Detail string; RetryAfterSeconds *float64 `json:"retry_after_seconds,omitempty"`}`.
   - `ShardDescriptor{Corpus string `json:"corpus"`; FileIDs []string `json:"file_ids"`}`.
   - `PartitionShard{ShardKey string; Descriptor ShardDescriptor; Weight int}`.
   - `SubCorpus{CorpusKey string `json:"corpus_key"`}`.
   - `PartitionResult{Shards []PartitionShard `json:"shards"`; NextCursor *string `json:"next_cursor"`; SubCorpora []SubCorpus `json:"sub_corpora"`}`.
     `next_cursor` encodes `null` when nil. `shards` and `sub_corpora` must never encode
     as `null`.
   - `ResultEnvelope`, with fields in exactly this order (the goldens depend on it):
     `ContractVersion` (`contract_version`), `TaskID`, `Status`, `Summary` (omitempty),
     `Findings` (omitempty), `ResourceExposures` (omitempty), `ResourceInventories`
     (omitempty), `Payload *PartitionResult` (omitempty), `Error *ResultError`
     (omitempty), `Stats *ShardStats` (omitempty), `CompleteLocators []string`
     (omitempty).
   - `type Handler func(ctx context.Context, task TaskEnvelope) ResultEnvelope`.
   - Encoders: `func EncodeResult(r ResultEnvelope) ([]byte, error)`,
     `EncodeManifest`, `EncodeClaim` and `EncodeHeartbeat`. Each normalizes nil required
     slices to empty slices (`Manifest.Labels`, `PartitionResult.Shards`,
     `PartitionResult.SubCorpora`, `ShardDescriptor.FileIDs`) and then calls
     `json.Marshal`. Every plan that measures or sends a body MUST use these encoders.
6. `go/internal/gapi/types.go` (package `gapi`):
   - `DriveFile{ID, Name, MimeType string; Trashed bool; DriveID *string; Parents []string}`.
     The JSON tags are Drive's (`id`, `name`, `mimeType`, `trashed`, `driveId`,
     `parents`). `Name` is never logged.
   - `DrivePermission{Type, Role string; Domain *string; EmailAddress *string; AllowFileDiscovery bool}`,
     with tags `type`, `role`, `domain`, `emailAddress`, `allowFileDiscovery`.
   - `FileListPage{Files []DriveFile; NextPageToken *string}`.
   - `DriveRef{ID string}` and `DrivesPage{Drives []DriveRef; NextPageToken *string}`.
   - `DirectoryUser{ID, PrimaryEmail string}` and `UsersPage{Users []DirectoryUser; NextPageToken *string}`.
   - `CellKind` (string constants `CellString`, `CellNumber`, `CellBool`) and
     `CellValue{Kind CellKind; Text string; Number string; Bool bool}`. `Number` is the
     raw JSON literal. Implement `UnmarshalJSON` on `CellValue`: a JSON string goes to
     `Text`, a JSON number to `Number` via `json.Number`, a JSON bool to `Bool`, and
     anything else is an error.
   - `SheetValues{Range string; Values [][]CellValue}`.
   - `FilesListParams{Q, Corpora string; DriveID, PageToken *string; PageSize int; Subject *string}`.
   - Typed errors, each with a static `Error()` string that contains no ids or bodies:
     `GoneError{}`, `ForbiddenError{}`, `FetchFailedError{}`,
     `RateLimitedError{RetryAfterSeconds *float64}`. Do not declare package-level
     sentinel variables.
   - Interfaces (callers branch with `errors.As`):

     ```go
     type Drive interface {
         FilesList(ctx context.Context, p FilesListParams) (FileListPage, error)
         FilesGetMeta(ctx context.Context, fileID string, subject *string) (DriveFile, error)
         ListPermissions(ctx context.Context, fileID string, subject *string) ([]DrivePermission, error)
         Download(ctx context.Context, fileID string, subject *string, maxBytes int) (content []byte, oversize bool, err error)
         Export(ctx context.Context, fileID, mimeType string, subject *string, maxBytes int) (content []byte, oversize bool, err error)
         DrivesList(ctx context.Context, pageToken *string, subject string) (DrivesPage, error)
     }
     type Sheets interface {
         SheetTitles(ctx context.Context, spreadsheetID string, subject *string) ([]string, error)
         ValuesBatchGet(ctx context.Context, spreadsheetID string, ranges []string, subject *string) ([]SheetValues, error)
     }
     type Admin interface {
         UsersList(ctx context.Context, pageToken *string) (UsersPage, error)
         UserEmail(ctx context.Context, userID string) (string, error)
         AdminSubject() string
     }
     ```
7. `go/internal/detect/api.go` (package `detect`):
   - `RuleClass` string constants matching the masked-finding enum: `credential`,
     `token`, `private_key`, `password`, `payment_card`, `national_id`, `email`, `phone`,
     `address`.
   - `Span{Start, End int}` (code-point offsets).
   - `RawFinding{RuleID string; RuleClass RuleClass; Span Span; Confidence float64}`.
   - `Finding{RuleID string; RuleClass RuleClass; MaskedSnippet, Location string; Confidence float64; ExternalFingerprint string}`.
   - `type Scanner interface { ScanText(text string) []RawFinding }` (gdp-p05 implements
     it).
   - `type Engine interface { ScanSegment(text, segmentLocator string) (findings []Finding, ok bool); HasHits(s string) bool }`
     (gdp-p08 implements it). `ok=false` means no clean fingerprint could be derived, and
     the caller counts the resource as failed.
8. `go/internal/pytext/tables.go` (package `pytext`). This is the loader for the data
   format below; gdp-p02 produces the data in this exact format.
   - `func Load(fsys fs.FS) (*Tables, error)` reads the files at the root of `fsys`.
   - Methods on `*Tables`:
     - `PythonVersion() string`, `UnidataVersion() string`.
     - `IsWord(r rune) bool`, `DecimalValue(r rune) (int, bool)`, `IsDigit(r rune) bool`,
       `IsSpace(r rune) bool`, `IsLineBreak(r rune) bool`.
     - `LowerRune(r rune) []rune`, `Lower(s string) string`, `CaseFold(s string) string`.
     - `FoldsTo(letter rune, r rune) bool`: Python `re.IGNORECASE` equivalence for ASCII
       letters a-z; `letter` is lowercase.
     - `SplitLines(s string) []string`: Python `str.splitlines()`, where `\r\n` is one
       break and there is no trailing empty element.
     - `Strip(s string) string`: Python `str.strip()` with the space table.
   - Data format. Every file starts with the line `# python=<sys.version_info dotted>
     unidata=<unicodedata.unidata_version>`. All files must carry the same header, or
     `Load` fails. Hex is uppercase without `0x`; ranges are inclusive, sorted and
     non-overlapping.
     - `word.txt`, `digit.txt`, `space.txt`, `linebreak.txt`: lines `XXXX-YYYY`.
     - `decimal.txt`: lines `XXXX-YYYY V`. `V` is the value of `XXXX`, and the value
       increments by one per code point within the range.
     - `lower.txt`, `casefold.txt`: lines `XXXX=YYYY[ ZZZZ...]`, only for code points
       whose mapping differs from themselves.
     - `ignorecase.txt`: lines `a=XXXX XXXX ...` for each letter `a`-`z`, listing every
       code point Python matches with `(?i)<letter>`.
   - Store the tables in unexported fields: sorted range slices with binary search, and
     maps. Lookups never mutate state.

## Key Pitfalls

- x/text version:
  - Pick the newest `golang.org/x/text` release for which `norm.Version` under go1.27.3
    equals the Python runtime's `unicodedata.unidata_version`. Obtain that value with
    `uv run --frozen python -c "import unicodedata; print(unicodedata.unidata_version)"`;
    it is expected to be `15.0.0` for Python 3.12.
  - x/text picks tables by Go build tags, so a newer release may report a newer version.
    In that case step down release by release.
  - If no release gives the Python version, STOP. Write a Blocker in the Progress Log
    with the versions you tried; do not work around it.
- Module download needs `GOPROXY` network or a warm module cache. If neither is available,
  record a Blocker. Never hand-write `go.sum`.
- `go mod tidy` drops x/text unless some package imports it. `unorm` must import
  `golang.org/x/text/unicode/norm` before you tidy and vendor.
- Use `mise exec -- go ...` for every Go command, so 1.27.3 is used. Set
  `GOTOOLCHAIN=local` so Go never downloads another toolchain.
- No package-level mutable state anywhere. Constants are fine. Do not use `init()`.
- JSON `null` versus `[]`: the schemas require arrays. Unit-test the encoders' nil-slice
  normalization.
- Do not add `item_fingerprints` or any field not listed above; contract changes go
  through the design.

## Tests (contract_test.go, types_test.go, tables_test.go, unorm_test.go)

- `EncodeResult` with nil `Findings`, `Stats` and `CompleteLocators` -> JSON has no
  `findings`, `stats` or `complete_locators` keys.
- `EncodeResult` with `Payload{Shards:nil, NextCursor:nil, SubCorpora:nil}` -> payload is
  `{"shards":[],"next_cursor":null,"sub_corpora":[]}`.
- `EncodeManifest` with nil labels -> `"labels":[]`.
- `ResourceExposure` with count 0 and `SharedDrive` false -> both keys present.
- `ResultError` with nil `RetryAfterSeconds` -> no key; with 2.5 -> `"retry_after_seconds":2.5`.
- Decoding the repository example `src/telescreen/contracts/police/v1/examples/task-envelope.json`
  (read via a relative path from the test) into `TaskEnvelope` -> no error, and the
  `Input` raw JSON is non-empty.
- `CellValue` decodes `"x"`, `12`, `1.5e3` and `true` into the right kinds, and `null`
  -> error.
- `pytext.Load` over `go/internal/pytext/testdata/` (a tiny handwritten table set in the
  pinned format):
  - range lookups at the boundaries;
  - `DecimalValue` inside a range;
  - `Lower` of a multi-rune mapping;
  - `SplitLines("a\r\nb\rc\n")` -> `["a","b","c"]`;
  - `SplitLines("")` -> `[]`;
  - `Strip` with table spaces;
  - mismatched headers -> error.
- `unorm.Version()` equals the Python `unidata_version` (literal `"15.0.0"`, with a
  comment pointing at the design 9.2 assertion). `NFKCRune('Ａ')` = `"A"`. `NFKC("e" + combining acute)` = `"é"`.

## Verification (run from the repository root; record exit status and log path)

```bash
set -o pipefail
mise install go 2>&1 | tee /tmp/telescreen-gdp-p01-mise.log; echo "exit=$?"
mise exec -- go version   # must print go1.27.3
cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test ./internal/... 2>&1 | tee /tmp/telescreen-gdp-p01-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/... 2>&1 | tee /tmp/telescreen-gdp-p01-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal   # must print nothing
GOTOOLCHAIN=local mise exec -- go mod verify 2>&1 | tee /tmp/telescreen-gdp-p01-modverify.log; echo "exit=$?"
cd .. && mise tasks ls | grep -E '^go-(test|lint|build) '   # three lines
find go/vendor -type f | wc -l            # must be below 512
find go/vendor -type f -size +7812k       # must print nothing
shasum -a 256 go/go.mod go/go.sum go/vendor/modules.txt
```

Each command must exit 0. `gofmt` and the size check must print nothing. `go test` must
report `ok` for `unorm`, `police`, `gapi` and `pytext` (`detect` contains no tests yet).

Record in the Progress Log:

- the x/text version chosen and the `norm.Version` value;
- the exact `go get golang.org/x/text@<version>`, `go mod tidy` and `go mod vendor`
  commands, each with its exit code;
- the vendor file count and the (empty) output of the size check;
- the sha256 of `go/go.mod`, `go/go.sum` and `go/vendor/modules.txt`.

## Completion Criteria

- [ ] `mise.toml` pins go 1.27.3 and has `go-test`, `go-lint`, `go-build`, plus the new
      `ci` chain verbatim.
- [ ] `.gitignore` ignores `/go/bin/`.
- [ ] `go.mod` has exactly one requirement, x/text, at a pinned version.
- [ ] `go/vendor` is populated as committed source with fewer than 512 files and no file
      over 7812k. `go mod verify` exits 0.
- [ ] The vendoring audit (x/text version, `go get`/`go mod tidy`/`go mod vendor`
      commands and exit codes, and the sha256 of `go.mod`, `go.sum` and `modules.txt`) is
      in the Progress Log.
- [ ] All contract files exist with the exact names and shapes above. Tests pass, `go
      vet` is clean, and `gofmt` reports nothing.
- [ ] `unorm.Version()` equals the Python `unidata_version`, and both are recorded.
- [ ] Progress Log has hashes, commands, exit codes and log paths.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
