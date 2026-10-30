# GDP-P03 Police Contract Client and Pull Worker (Go)

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p03-police-worker/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker.
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

- [x] Client, worker and backoff are implemented with the exported API above.
- [x] Every test case above exists and passes with `-race`; `go vet` and `gofmt` are
      clean.
- [x] `contract.go` is unchanged (hash recorded before and after).
- [x] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)

### Run 2 implementation, 2026-10-30

Implemented the contract client, pull worker, task cancellation, graceful drain and
transient backoff. The accepted gdp-p01 dependency is admitted by dispatch. The plan
and design section 3 agree; downstream handler wiring and formal reviews remain with
their assigned steps. All paths below were absent before this session:

- `go/internal/police/client.go`: after `ebb12c5cef07e231d38e401ddf3f463dff696b41105a867be29920654ce9ecc4`
- `go/internal/police/client_test.go`: after `5762971b82f95dd4203664bbef59314099e70df39aec91162fc45bf3fca73bc4`
- `go/internal/police/worker.go`: after `5a5065fecf4caa86486c6629b37f8e282c1007dea27c8ba3c7653bfc1e6f2a93`
- `go/internal/police/worker_test.go`: after `902f369dee8d9b9d5e04f4b2cb66f4713b81994990076a0d67777bb480ee8060`
- `go/internal/police/backoff.go`: after `8d228904040153e172c88637fdf44148beb26a942154d47ca1c836047823504b`
- This plan before: `6fbaa9f73af4683a0ef6524444b82471bbbffc2f313ecaba933efaa464df2617`; its after hash is recorded in `attempt-2/final-hashes.log`.
- `go/internal/police/contract.go` before and after: `cd1389e775ffd3c1e84d06ecff3ffd460c444f7ae195e34712d954e6b29ad59d`.

Final checks, from `go/`, all exit 0; logs are under
`tmp/telescreen-gdp-20261030-h2/gdp-p03-police-worker/attempt-2/`:

- `GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -race -count=1 -v ./internal/police/...`: 10 top-level tests pass, `test.log`.
- `GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/police/...`: `vet.log`.
- `test -z "$(mise exec -- gofmt -l internal/police)"`: `format.log`.

Earlier interrupted test run and its fake-server correction are retained in
`attempt-1/test-initial.log` and `attempt-worker-tests/`. Per-edit intentions are in
`attempt-1/`, `client-agent/` and `attempt-worker-tests/`. Shared tree contains sibling
changes; none was altered as part of this plan.
