# GDP-P04 Google Service-Account Auth and Drive/Sheets/Admin Clients (Go)

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p04-google-clients/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker. V1 stands: only 404 is gone, 410 is a fetch failure, even though the workflow input text still says otherwise.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 4, 5 (and 13 V1 withdrawn: 410 is a fetch failure)
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p04-google-clients`
- planPath: `impl-plans/active/gdp-p04-google-clients.md`
- wave: 2
- dependsOn: `gdp-p01-foundation`
- writePaths:
  - `impl-plans/active/gdp-p04-google-clients.md`
  - `go/internal/googleauth`
  - `go/internal/gapi/http.go`
  - `go/internal/gapi/drive.go`
  - `go/internal/gapi/sheets.go`
  - `go/internal/gapi/admin.go`
  - `go/internal/gapi/gapi_test.go`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8. Do not edit
`go/internal/gapi/types.go` (gdp-p01).

## Intent and Context

Port `src/telescreen/collectors/google/auth.py`, `drive.py`, `sheets.py` and `admin.py`.
The ported requests are the ones the handlers use: the exact query parameters, the
`fields` masks and the error mapping. The changes feed and watch are NOT ported (design
D6). Status mapping follows Python: only 404 is gone. The owner decided on 2026-10-30
that 410 is a fetch failure (`design-docs/user-qa/pending-go-gdrive-police-contract-gaps.md`,
row V1).

## Non-goals

- `changes.list`, `startPageToken`, `changes/watch`, `channels/stop`.
- Retries or backoff on Google calls. Rate limits surface as `RateLimitedError`, and the
  Bureau pauses the job.
- Any logging of response bodies, file names, e-mails or tokens.

## Deliverables (exported API is a contract for gdp-p10)

1. `go/internal/googleauth`:
   - `type ServiceAccountKey struct{ ClientEmail, TokenURI string; key *rsa.PrivateKey }`
     and `func ParseServiceAccountKey(data []byte) (ServiceAccountKey, error)`.
     - Parse the JSON members `client_email`, `private_key` and `token_uri`; the PEM may
       be PKCS#8 or PKCS#1.
     - On any failure, return a static error such as "google service-account key is
       invalid" that never includes the input.
   - `type TokenSource struct` (mutex-guarded cache) built by
     `func NewTokenSource(httpClient *http.Client, key ServiceAccountKey, now func() time.Time) *TokenSource`.
   - `func (t *TokenSource) Token(ctx context.Context, scopes []string, subject *string) (string, error)`:
     - The JWT is RS256 with header `{"alg":"RS256","typ":"JWT"}` and claims
       `iss, scope (space-joined), aud = token_uri, iat, exp = iat+3600`, plus `sub` only
       when `subject != nil`.
     - POST a form with `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and
       `assertion` to `token_uri`.
     - The cache key is (scopes joined, subject). A cached token is reused while
       `expiresAt > now+60s`. Prune expired entries.
     - Non-2xx, transport errors or bad JSON (`access_token`, `expires_in`) ->
       `googleauth.TokenError{}` (static message). `gapi` maps it to `FetchFailedError`
       in `doGet`, so the resource counts as failed (design 4). `googleauth` must NOT
       import `gapi`, because `gapi` imports `googleauth` and that would be an import
       cycle.
2. `go/internal/gapi/http.go`: a shared `doGet` that adds the Bearer token for the scope
   and subject, and maps the response:
   - 404 -> `GoneError`.
   - 429, or 403 whose body JSON has `error.errors[].reason` in {`rateLimitExceeded`,
     `userRateLimitExceeded`} -> `RateLimitedError`. `RetryAfterSeconds` is the
     `Retry-After` header parsed as a finite float >= 0; otherwise nil.
   - Other 403 -> `ForbiddenError`.
   - Any other non-2xx (including 410 and 401), transport error or JSON decode error ->
     `FetchFailedError`.
   - Every request has a 60 s timeout.
3. `drive.go`: `func NewDrive(httpClient *http.Client, tokens *googleauth.TokenSource, baseURL string) gapi.Drive`.
   The Drive scope is `https://www.googleapis.com/auth/drive.readonly`.
   - `FilesList`: `GET /drive/v3/files` with `q`, `corpora`, `pageSize = min(p, 1000)`,
     `supportsAllDrives=true`, `includeItemsFromAllDrives=true` and
     `fields=files(id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size),nextPageToken`,
     plus optional `driveId` and `pageToken`.
   - `FilesGetMeta`: `GET /drive/v3/files/{id}` with
     `fields=id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size`.
   - `ListPermissions`: paged `GET .../permissions` with `supportsAllDrives=true`,
     `fields=permissions(type,role,domain,emailAddress,allowFileDiscovery),nextPageToken`
     and `pageToken`. Loop until there is no token.
   - `Download`: `alt=media`. `Export`: `GET .../export?mimeType=<mime>`. Both read
     through `io.LimitReader(body, maxBytes+1)`. More than `maxBytes` bytes means
     `oversize=true` with nil content. Stop reading at the limit; do not buffer the rest.
   - `DrivesList`: `GET /drive/v3/drives` with `pageSize=100`,
     `fields=drives(id,name),nextPageToken` and `pageToken`.
   - Path ids are escaped with `url.PathEscape` (Python `quote(id, safe='')`).
4. `sheets.go`: `func NewSheets(httpClient, tokens, baseURL string) gapi.Sheets`. The
   scope is `https://www.googleapis.com/auth/spreadsheets.readonly`.
   - `SheetTitles`: `GET /v4/spreadsheets/{id}` with `fields=sheets(properties(title))`.
   - `ValuesBatchGet`: `GET /v4/spreadsheets/{id}/values:batchGet` with repeated
     `ranges` (in order) and `valueRenderOption=FORMATTED_VALUE`. Decode `valueRanges`;
     a missing `values` member means empty rows.
5. `admin.go`: `func NewAdmin(httpClient, tokens, baseURL, adminSubject string) gapi.Admin`.
   The scope is `https://www.googleapis.com/auth/admin.directory.user.readonly` and the
   subject is always `adminSubject`.
   - `UsersList`: `customer=my_customer`, `maxResults=500`, `pageToken`.
   - `UserEmail`: `GET /admin/directory/v1/users/{id}?fields=primaryEmail`, with a
     mutex-guarded per-instance cache.
   - `AdminSubject()` returns `adminSubject`.
   - `DrivesList` is on `Drive` and the caller passes the admin subject.

## Key Pitfalls

- Never put a token, assertion, e-mail, file name or body in an error string or log line.
  Errors are the typed structs from `types.go`.
- `Retry-After` may be an HTTP date. Python's `float()` fails on that, giving nil; Go
  must also give nil. Values such as `inf` or `nan` give nil.
- `Export` and `Download` with exactly `maxBytes` bytes -> not oversize (Python uses
  `len <= max_bytes`).
- Repeated `ranges` params must keep order and must not be comma-joined.
- The base URLs come from configuration. Do not hardcode test hosts. The defaults
  (`https://www.googleapis.com`, `https://sheets.googleapis.com`,
  `https://admin.googleapis.com`) live in gdp-p07 config, not here.
- No package-level mutable state. Scopes are constants.

## Tests (httptest; input -> expected)

- A token request JWT has the right claims with and without `sub`, and the fake server
  verifies the RS256 signature with the generated public key (`crypto/rsa.GenerateKey`
  at test time; never commit a key).
- The second `Token` call within the validity window makes no HTTP request. At
  `expires_in - 59 s` it refreshes.
- A bad PEM -> error, and the message has no PEM text in it.
- The token endpoint returns 500 -> `Token` returns `TokenError`, and a Drive call
  using that source returns `FetchFailedError`.
- `FilesList` sends the exact query parameters (assert each) and decodes `nextPageToken`.
- `ListPermissions` over 3 pages -> concatenated permissions.
- 404 -> `GoneError`; 410 -> `FetchFailedError`; 429 with `Retry-After: 7` ->
  `RateLimitedError{7}`.
- 403 with reason `userRateLimitExceeded` -> `RateLimitedError{nil}`; 403 with another
  reason -> `ForbiddenError`; 403 with a non-JSON body -> `ForbiddenError`.
- 500 -> `FetchFailedError`.
- `Download` of `maxBytes+1` bytes -> `oversize=true`; of exactly `maxBytes` bytes ->
  content returned.
- `ValuesBatchGet` sends two ranges in order; an empty `values` member -> empty rows;
  mixed cell kinds decode.
- `UserEmail` called twice -> one HTTP request.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -race -count=1 ./internal/googleauth/... ./internal/gapi/... 2>&1 | tee /tmp/telescreen-gdp-p04-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/googleauth/... ./internal/gapi/... 2>&1 | tee /tmp/telescreen-gdp-p04-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/googleauth internal/gapi   # must print nothing
```

All commands exit 0, and the test log shows `ok` for both packages.

## Completion Criteria

- [ ] Auth, Drive, Sheets and Admin are implemented with the constructors above;
      `types.go` is unchanged.
- [ ] Every test above passes with `-race`; `vet` and `gofmt` are clean.
- [ ] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
