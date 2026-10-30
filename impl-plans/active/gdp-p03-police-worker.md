# GDP-P03 Police Contract Client and Pull Worker (Go)

**Status**: Ready
**Design Reference**: design-docs/specs/design-go-gdrive-police.md section 3 (and 13 V5)
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p03-police-worker`
- planPath: `impl-plans/active/gdp-p03-police-worker.md`
- wave: 2
- dependsOn: `gdp-p01-foundation`
- writePaths:
  - `impl-plans/active/gdp-p03-police-worker.md`
  - `go/internal/police/client.go`
  - `go/internal/police/client_test.go`
  - `go/internal/police/worker.go`
  - `go/internal/police/worker_test.go`
  - `go/internal/police/backoff.go`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8. Do not edit
`go/internal/police/contract.go` (gdp-p01). If you need a contract change, write a
Reconciliation request.

## Intent and Context

Implement the Bureau HTTP client and the pull loop, with the same behavior as the Python
SDK except for the divergences listed in design 3/V5. The Python references are:

- `src/telescreen_police_sdk/client.py:BureauClient`: endpoints, bearer header, and
  `_raise_for_error`, which reads only `{error:{code}}` and falls back to
  `request_failed`.
- `src/telescreen_police_sdk/worker.py:PullWorker`: claim free slots, heartbeat loop,
  cancel, graceful stop, `handler_error`/`unsupported_task`.
- Bureau timings: `src/telescreen/police/registration.py` (60/20/30).

## Non-goals

- No push mode, no webhook signing, no `verify_remediation`.
- No retry inside handlers; handlers are gdp-p06.
- No config or env parsing (gdp-p07), and no `main` (gdp-p10).

## Deliverables (exported API is a contract for gdp-p09 and gdp-p10)

1. `client.go`:
   - `type Client struct` with unexported `baseURL` and `http *http.Client`.
   - `func NewClient(baseURL string, httpClient *http.Client) *Client`. The URL is
     `strings.TrimRight(baseURL, "/") + "/api/police/v1" + path`. Path ids are escaped
     with `url.PathEscape`.
   - `Register(ctx, m Manifest, enrollmentToken string) (Registration, error)`.
   - `Claim(ctx, workerID, token string, req ClaimRequest) ([]TaskEnvelope, error)`.
     A missing or non-array `tasks` member is an error.
   - `Heartbeat(ctx, workerID, token string, taskIDs []string) ([]string, error)`.
     `cancelled_task_ids` must be an array of strings.
   - `SubmitResult(ctx, token string, r ResultEnvelope) error`. The body comes from
     `EncodeResult`.
   - `type BureauError struct{ Status int; Code string }`, with
     `Error() = "bureau request failed (<status>): <code>"`.
   - A transport failure returns `TransportError{}`, whose static message does not wrap
     URLs that contain ids.
   - Headers: `Authorization: Bearer <token>` and `Content-Type: application/json`.
     Requests use the gdp-p01 encoders.
2. `worker.go`:
   - `type Worker struct`, built by
     `func NewWorker(c *Client, reg Registration, handlers map[string]Handler,
     concurrency int, logger *slog.Logger, now func() time.Time) *Worker`.
   - `func (w *Worker) Run(ctx context.Context) error`. Cancelling `ctx` is the graceful
     stop.
   - `Run` returns `nil` after a graceful stop. It returns `*BureauError` with status 401
     or 403 when claim or heartbeat is unauthorized; `main` maps that to exit 3.
   - Claim loop:
     - Free slots are `concurrency - active`. When none are free, wait for a task to
       finish or for stop.
     - Send `ClaimRequest{MaxTasks: free, WaitSeconds: reg.MaxWaitSeconds}`.
     - The claim HTTP request uses `ctx`, so stop aborts it at once.
   - Per task:
     - Run in a goroutine with
       `context.WithTimeout(parent, task.Deadline.Sub(task.IssuedAt))`. The timeout is
       measured from claim receipt and never compared with the worker's wall clock. This
       is the "task budget".
     - The parent of task contexts is NOT `ctx`. Graceful stop lets active tasks finish,
       so use `context.WithoutCancel(ctx)` plus per-task cancel.
     - Recover a panic -> `failed` result with `CodeHandlerError` and a static detail.
     - An unknown task type -> `CodeUnsupportedTask`.
     - Submit unless the task was cancelled by heartbeat.
   - Heartbeat loop:
     - Every `reg.HeartbeatIntervalSeconds`, post the ids of the active tasks (skip when
       none are active).
     - Cancel each returned id's task context and mark the task cancelled. A cancelled
       task submits nothing, even if its handler returns a result.
   - Errors from claim, heartbeat or submit:
     - Transport error, 5xx or 429: exponential backoff (`backoff.go`: 1 s doubling,
       capped at 30 s, reset on success), and keep running.
     - 401 or 403 on claim or heartbeat: return the error from `Run`.
     - 409 or 422 on submit: log status and code and drop the result (no retry).
     - Submit retries for transient errors stop when the task budget runs out.
   - Logging uses `slog` attributes only: `task_id`, `task_type`, `status`, `code`,
     `duration_ms`, `count`. Never log bodies, tokens, inputs or results.

## Key Pitfalls

- A handler that keeps running after the heartbeat cancels it must not submit. Check the
  cancelled flag after the handler returns, under a mutex.
- Do not let graceful stop cancel running handlers (the SDK waits for them). A second
  signal is handled by `main`, not here.
- Free-slot accounting must not exceed `concurrency`, even if the Bureau returns more
  tasks than requested. Ignore extras beyond free slots, as the SDK does with `break`.
  Extra leases then expire.
- No package-level mutable state. The backoff state lives in the worker.
- Inject `now` and use it only for durations and logging. Never derive the task deadline
  from it.
- Race-free: tests run with `-race`.

## Tests (httptest fake Bureau; input -> expected)

- Register posts the manifest JSON with the enrollment bearer and returns the parsed
  `Registration`.
- Claim with an empty `tasks` list -> the loop claims again. A `tasks` member that is not
  a list -> error.
- Error body `{"error":{"code":"conflict"}}` with 409 -> `BureauError{409,"conflict"}`.
  A non-JSON 500 -> code `request_failed`.
- Two claimed tasks with concurrency 1 -> only one runs at a time, and the second is
  ignored or not claimed.
- Heartbeat returns a task id as cancelled -> the handler's ctx is done, and no
  `/result` request is made for it.
- A handler panics -> the submitted result is `failed` with `handler_error` and a static
  detail.
- An unknown task type -> `unsupported_task`.
- Claim returns 500 twice, then succeeds -> backoff delays grow (test with a fake sleep
  or short durations) and the worker continues.
- Claim returns 401 -> `Run` returns a `*BureauError` with status 401.
- Submit returns 422 -> no retry, and the worker continues.
- `IssuedAt` and `Deadline` are 2 minutes apart, but the worker clock is 10 hours ahead
  -> the handler still runs and submits (task budget).
- Graceful stop while a handler is running -> `Run` returns only after that handler's
  result is submitted. No new claims are sent after the stop.
- The captured `slog` output never contains the worker token or the enrollment token.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -race -count=1 ./internal/police/... 2>&1 | tee /tmp/telescreen-gdp-p03-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/police/... 2>&1 | tee /tmp/telescreen-gdp-p03-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/police   # must print nothing
```

All commands exit 0 and the test log shows `ok .../internal/police`.

## Completion Criteria

- [ ] Client, worker and backoff are implemented with the exported API above.
- [ ] Every test case above exists and passes with `-race`; `go vet` and `gofmt` are
      clean.
- [ ] `contract.go` is unchanged (hash recorded before and after).
- [ ] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
