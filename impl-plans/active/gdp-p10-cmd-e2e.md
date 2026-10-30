# GDP-P10 Binary Wiring (cmd/telescreen-gdrive-police) and Offline End-to-End Sweep Test

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker; `go/bin` is this plan's only artifact root.
**Run 3**: Re-dispatched 2026-10-31, alone, under `impl-plans/active/telescreen-gdp-20261031-h3-dispatch.json` (supersedes h2; same tasks and writePaths). gdp-p01..gdp-p09 are accepted dependencies at `fb93c5e`. Evidence goes under `tmp/telescreen-gdp-20261031-h3/gdp-p10-cmd-e2e/attempt-<n>/`, and the h3 manifest's verification log paths replace the `/tmp/telescreen-gdp-p10-*.log` paths below. See the Owner resolution at the end of the Progress Log.
**Run 4**: Re-dispatched 2026-10-31, alone in wave 1, under `impl-plans/active/telescreen-gdp-20261031-h4-dispatch.json` (supersedes h3; same tasks, writePaths, sharedPaths and artifactRoots). gdp-p01..gdp-p09 are accepted dependencies at `fd86812`. Evidence goes under `tmp/telescreen-gdp-20261031-h4/gdp-p10-cmd-e2e/attempt-<n>/`. Both Owner resolutions at the end of the Progress Log apply. The E2E binary is `go/bin/telescreen-gdrive-police-e2e`, and the CI gate is the `mise run ci` sequence with `uv build --out-dir /tmp/telescreen-gdp-p10-dist`; the literal `mise run ci` is never run.
**Run 5**: Re-dispatched 2026-10-31 as VERIFY-AND-COMPLETE. It runs alone in wave 1 under `impl-plans/active/telescreen-gdp-20261031-h5-dispatch.json`, which supersedes h4; tasks, writePaths, sharedPaths and artifactRoots are unchanged. gdp-p01..gdp-p09 are accepted dependencies at `cae1af3`. All three deliverables are committed at `cae1af3`. Deliverables 2 and 3 were written by the owner; see the run 4 -> run 5 Owner resolution. Do not rewrite working code. Re-run every gate in the Verification section with real counts, record the `go/bin` audit, and complete the Completion Criteria and the Progress Log with `priorVerification` empty. Evidence goes under `tmp/telescreen-gdp-20261031-h5/gdp-p10-cmd-e2e/attempt-<n>/`.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 2, 3, 8.1, 10, 11, 15, 16
**Created**: 2026-10-30
**Last Updated**: 2026-10-31

## Plan Metadata

- planId: `gdp-p10-cmd-e2e`
- planPath: `impl-plans/active/gdp-p10-cmd-e2e.md`
- wave: 1 (h5; also wave 1 in h3/h4; it was wave 4 in h1/h2)
- dependsOn: `gdp-p02-parity-fixtures`, `gdp-p03-police-worker`, `gdp-p04-google-clients`, `gdp-p05-detect-core`, `gdp-p06-gdrive-handlers`, `gdp-p07-config-docs`, `gdp-p08-detect-seal`, `gdp-p09-envelopes-contract`
  - p02 and p09 are needed because this plan's gates run their tests:
    `tests/go_police/test_parity_fixtures_current.py`,
    `tests/go_police/test_go_envelopes_contract.py` and `go/internal/envelopes`.
- writePaths:
  - `impl-plans/active/gdp-p10-cmd-e2e.md`
  - `go/cmd/telescreen-gdrive-police`
  - `go/bin`
  - `tests/go_police/e2e_fakes.py`
  - `tests/go_police/test_gdrive_police_e2e.py`
- sharedPaths: none
- artifactRoots:
  - `go/bin` (build output of `mise run go-build`; gitignored by gdp-p01)

Read `impl-plans/conventions.md` (all sections). Do not edit `tests/e2e/harness.py`,
`src/` or any other plan's Go package. If a dependency is broken, write a Reconciliation
request naming the plan, file and failing test.

## Intent and Context

This plan wires the packages into the binary and proves the acceptance criterion: "an
offline end-to-end test runs the Go worker binary against an in-process Bureau (the real
FastAPI police router on SQLite or the existing test harness) with fake Drive/Sheets
servers, completes a partition plus scan_shard sweep, and the Bureau accepts the results
(findings recorded, exposures and inventories applied, no raw_value_detected)."

Imitate:

- `tests/e2e/harness.py:make_world`, `E2EWorld.drain`, `bearer`, `admin_post`;
- `tests/e2e/test_flow_v14_inventory.py:test_e4_police_pull_inventory_creates_rekeyed_items`
  (police enrollment env `("TELESCREEN_POLICE_ENROLLMENT_TOKEN", "env:E2E_ENROLL")`,
  collector creation body, `independent_sessions=True`);
- `tests/e2e/test_flow_sweep.py` (`POST /api/collectors/{id}/sweep`, map-job polling);
- `tests/leak/flows_v11.py:police_exposures` (asserting `police:` sources and exposure
  application);
- `tests/support/samples.py` (sample secrets from fragments).

## Non-goals

- No live Google or Bureau. No edits to the harness or the Bureau.
- No new Go packages beyond `cmd`.
- No change to `mise.toml` (gdp-p01 owns it).

## Deliverables

Run 5: deliverables 1-3 already exist at `cae1af3`. They are the reference for review, not
work to redo. Where deliverable 3 below differs from the owner-written test, the owner
deviations in the run 4 -> run 5 Owner resolution win. If a gate fails, first save the
failing log under `EV`. Then make the smallest fix inside this plan's writePaths and
record it with pre/post hashes. Never edit `src/`, the harness or `mise.toml`, and never
delete or skip an assertion to make a gate pass.

1. `go/cmd/telescreen-gdrive-police/main.go`, plus `run.go` and `run_test.go`:
   - `func run(ctx context.Context, lookup func(string) (string, bool), readFile func(string) ([]byte, error), stderr io.Writer) int`.
     `main` calls `os.Exit(run(...))` with `signal.NotifyContext` for SIGINT and SIGTERM.
     A second signal calls `os.Exit(1)` immediately.
   - Exit codes:
     - `config.Load` error -> print the error (variable name only) and return 2.
     - Read and parse the service-account key (`googleauth.ParseServiceAccountKey`).
       Failure, or a `token_uri` scheme other than `https` without `AllowHTTP` -> 2.
   - Wiring, in this order:
     - `tables := pytext.Default()` (error -> 1);
     - `detect.NewDetector`, then `detect.NewEngine(det, tables, cfg.FingerprintKey)`;
     - `googleauth.NewTokenSource`;
     - `gapi.NewDrive`, `gapi.NewSheets`, and `gapi.NewAdmin` only when
       `cfg.DelegatedAdmin != nil`. Otherwise pass an untyped nil `gapi.Admin`. Never a
       typed-nil pointer.
     - `gdrive.NewService(gdrive.Config{InternalDomains, MaxBytes: ScanMaxBytes,
       ShardSize: SweepShardSize}, ...)`;
     - `police.NewClient(cfg.BureauURL, httpClient)`.
   - Register with `Manifest{Name, Version: "0.1.0", ContractVersion: police.ContractVersion,
     Mode: "pull", TaskTypes: [detect, partition, scan_shard], Labels, Concurrency}`.
     A `BureauError` with 401 or 403 -> 3; any other failure -> 1.
   - `police.NewWorker(...).Run(ctx)`: nil -> 0; `*BureauError` 401 or 403 -> 3; other
     error -> 1.
   - The logger is `slog.NewJSONHandler(stderr)`.
   - `run_test.go`:
     - missing `TELESCREEN_URL` -> 2, and stderr names the variable;
     - an enrollment token value never appears in stderr;
     - an `http://` `token_uri` without allow -> 2;
     - a fake Bureau returning 401 on register -> 3.
2. `tests/go_police/e2e_fakes.py`: loopback fake Google services on one stdlib
   `http.server.ThreadingHTTPServer` (port 0), routed by path:
   - token endpoint `/token`: verifies the assertion with PyJWT
     `jwt.decode(..., key=public_key, algorithms=["RS256"], audience=token_uri)` and
     records `sub` and `scope`;
   - Drive `/drive/v3/...`, Sheets `/v4/...` and Admin `/admin/directory/v1/...`.
   - Build the handler through a factory closure over a frozen dataclass `FakeEstate`;
     typelint TL008 forbids typed fields on a plain class. Generate the RSA key at
     runtime (`cryptography`) and write the service-account JSON to `tmp_path`. Never
     commit a key.
   - The estate is delegated: admin `admin@example.com`, users `u1` and `u2`, and shared
     drive `0AExampleDrive`.
   - Files:
     - a text file owned by `u1` holding an AWS key sample and an e-mail sample;
     - a Google Doc owned by `u2` holding a password assignment sample;
     - a Google Sheet in the shared drive with card cells (sample from fragments) and 3
       non-empty rows;
     - an image;
     - a file that is listed but returns 404 on `files.get`;
     - a file listed as not trashed whose metadata says `trashed: true`.
   - Permissions are mixed: anyone-with-link, an external user `x@partner.example.org`,
     and a domain grant.
   - Every file is below the max size and nothing returns 5xx, so every shard has
     `resources_failed = 0`. This is the precondition of design 8.1 for exposures and
     inventories to be applied.
3. `tests/go_police/test_gdrive_police_e2e.py`, one async test (plus a second one only if
   it is needed for clarity):
   1. Build: `shutil.which("go")`. If go is missing -> `pytest.fail` with "run via mise",
      never a skip. Then `subprocess.run(["go", "build", "-trimpath", "-o", <repo>/go/bin/telescreen-gdrive-police-e2e,
      "./cmd/telescreen-gdrive-police"], cwd=<repo>/go, env={...GOTOOLCHAIN=local,
      GOFLAGS=-mod=vendor}, timeout=300, check=True)`.
   2. World: `make_world(tmp_path, (enrollment env pair,), OutboundRecorder(),
      independent_sessions=True)`. Serve `create_app(world.container)` with
      `uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))`
      as an asyncio task in the same loop, and read the bound port from
      `server.servers`. If serving the harness app this way proves impossible, use the
      design 15 fallback: a dedicated app from the same wiring in `e2e_fakes.py`. Record
      the decision in the Progress Log.
   3. Create three `police` collector configs (kinds `gdrive`, `gdocs`, `gsheets`), each
      with `selector_json` `{"labels":["police=gdrive"],"input":{"kind":<kind>}}` and an
      inventory mode that records inventories (check the v14 test for the field).
   4. Start the worker with `asyncio.create_subprocess_exec`, stdout and stderr piped.
      Env:
      - `TELESCREEN_URL`, enrollment token, a 32+ byte fingerprint key, and the
        service-account file;
      - `TELESCREEN_GOOGLE_DELEGATED_ADMIN=admin@example.com`;
      - `TELESCREEN_INTERNAL_DOMAINS=example.com`;
      - the three base URLs pointing at the fake;
      - `TELESCREEN_POLICE_ALLOW_HTTP=true`, `TELESCREEN_POLICE_CONCURRENCY=4`.
   5. Start the three sweeps (`POST /api/collectors/{id}/sweep`). Loop until all three
      map jobs are terminal or 180 s pass:
      - `await world.container.worker.run_until_idle()`;
      - `world.clock.advance(timedelta(seconds=1))`;
      - `await asyncio.sleep(0.2)`.
   6. Assert:
      - each job is `succeeded`, and `resources_failed` (or the equivalent summary
        counter) is 0;
      - issues exist under the source `police:<config name>` with the rule classes
        `credential` and `email` (gdrive), `password` (gdocs) and `payment_card`
        (gsheets);
      - exposures were applied (`anyone_with_link`, `external_shared`, `domain` as
        appropriate);
      - the Sheets inventory `item_count == 3` with asset kind `table`, and the Drive and
        Docs asset kind is `file`;
      - the 404 and trashed files have no open issue;
      - the audit has no `police.result_rejected`;
      - worker stderr has no `"status":422`;
      - the fake token server saw `sub` values for the users and for the admin.
   7. Leak: the worker stdout and stderr contain none of the raw samples, the user
      e-mails, the permission e-mail, the file names or the access tokens.
   8. Teardown in `finally`: SIGTERM the worker and wait up to 30 s, then kill. Stop
      uvicorn (`should_exit`). Shut down the fake server. `world.close()`.

## Key Pitfalls

- FakeClock: the harness clock starts at 2026-10-01, and the worker uses deadline minus
  issued_at, so wall-clock skew is harmless. Do NOT advance the clock in big steps:
  60 s leases and `shard_timeout` would expire tasks. Read
  `src/telescreen/police/claims.py` for how the long-poll wait is timed, and keep the
  loop above responsive.
- Do not call `world.drain()` while waiting for police results. It returns when the
  engine is idle and raises after its round limit.
- Concurrency with SQLite: use `independent_sessions=True`. If "database is locked"
  appears, lower worker concurrency to 2 and record that.
- The `gdrive` kind does not match Google-native Docs or Sheets (`_matches`). That is
  why there are three configs.
- No literal secrets in test sources. Use `tests.support.samples` or fragments.
- Typelint: no `dict` bindings for env or JSON. Build the environment as a typed
  `list[tuple[str, str]]`, starting from the `PATH`, `HOME`, `GOPATH`, `GOCACHE` and
  `GOMODCACHE` values that exist in `os.environ`, plus the worker variables. Convert it
  inline at the call (`env=dict(pairs)`), as conventions section 3 allows.
- Every wait has a timeout. No orphan processes: kill in `finally`.
- Artifact boundary (run 3 block): the E2E binary goes only to
  `go/bin/telescreen-gdrive-police-e2e`. Never build into `tmp_path` or any other repository
  path. `tmp_path` holds only test data such as the generated service-account JSON.

## Tests (input -> expected)

- `go test ./cmd/...`: the four `run_test.go` cases above.
- E2E: delegated estate, three sweeps -> all succeed. Findings, exposures and inventories
  are applied as listed. No rejection. No leak in worker output.

## Verification (this plan is alone in its wave; module-wide gates are required here)

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
EV=tmp/telescreen-gdp-20261031-h5/gdp-p10-cmd-e2e/attempt-<n>; mkdir -p "$EV"
mise run go-lint > "$EV/golint.log" 2>&1; echo "exit=$?"
(cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -count=1 -v ./...) > "$EV/gotest-v.log" 2>&1; echo "exit=$?"
grep -c -- '--- PASS:' "$EV/gotest-v.log"; grep -c -- '--- FAIL:' "$EV/gotest-v.log"
mise run go-build > "$EV/gobuild.log" 2>&1; echo "exit=$?"
mise exec -- go version > "$EV/go-version.log" 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/go_police -rA > "$EV/pytest-go-police.log" 2>&1; echo "exit=$?"
shasum -a 256 go/bin/telescreen-gdrive-police go/bin/telescreen-gdrive-police-e2e > "$EV/gobin-sha256.log" 2>&1; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tests/go_police > "$EV/ruff-format.log" 2>&1; echo "exit=$?"
uv run --frozen ruff check --no-cache tests/go_police > "$EV/ruff-check.log" 2>&1; echo "exit=$?"
uv run --frozen ty check tests/go_police > "$EV/ty.log" 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > "$EV/typelint.log" 2>&1; echo "exit=$?"; ! grep -F 'tests/go_police/' "$EV/typelint.log"
wc -l tests/go_police/e2e_fakes.py tests/go_police/test_gdrive_police_e2e.py go/cmd/telescreen-gdrive-police/*.go > "$EV/wc.log"
test -z "$(git diff --name-only cae1af3 -- mise.toml)"; echo "exit=$?"
{ mise run fmt-check && mise run lint && mise run typelint && mise run typecheck && mise run go-lint && mise run go-test && mise run go-build && mise run test && uv build --out-dir /tmp/telescreen-gdp-p10-dist; } > "$EV/ci.log" 2>&1; echo "exit=$?"
mise exec -- gitleaks dir --config .gitleaks.toml --redact go/cmd/telescreen-gdrive-police > "$EV/gitleaks-cmd.log" 2>&1; echo "exit=$?"
mise exec -- gitleaks dir --config .gitleaks.toml --redact tests/go_police > "$EV/gitleaks-go-police.log" 2>&1; echo "exit=$?"
git status --porcelain > "$EV/git-status.log"; ! grep -E ' dist/' "$EV/git-status.log"
```

Run 5 (h5): all log paths are under `EV`. The h1..h4 evidence roots are never cited.

- Go counts follow the 2026-10-31 ruling in `impl-plans/conventions.md` section 7. Record
  `testsRun` as the `--- PASS:` count, which must be > 0 (owner baseline 88). Record
  `failureCount` as the `--- FAIL:` count, which must be 0. Never record any Go command
  with `testsRun: 0`. The `go-test` step inside `ci.log` is cited only with the counts
  from `gotest-v.log`.
- The pytest log must show `18 passed` with no `skipped`. `-rA` lists
  `test_gdrive_police_e2e` as PASSED.
- `gitleaks dir` runs from the repository root, on this plan's source paths only. Never run
  it on `.` or `go`, because that would scan the gitignored `go/bin` and `tmp/`.
  Serial finalization scans the full path list of design section 16.
- Every file in the `wc -l` output must be under 1000 lines. The `mise.toml` check must print nothing and exit 0. The typelint grep matches
absolute or relative paths, so a typelint violation in `tests/go_police` fails it. The last
line is the CI gate: do not run the literal `mise run ci` and do not run `uv build` without
`--out-dir /tmp/telescreen-gdp-p10-dist`. After the gates, `git status --porcelain` must list
only this plan's writePaths (the gitignored `go/bin`, `tmp/` and tool caches do not appear),
and in particular no `dist/` entry.

The `go/bin` artifact audit record goes in the Progress Log. It has the `go version` output
(`go1.27.3`) and the two build commands with their exit codes: `mise run go-build`, and the
E2E's `go build -trimpath` run by the pytest step. It also has the sha256 of
`go/bin/telescreen-gdrive-police` and `go/bin/telescreen-gdrive-police-e2e`. Both binaries
are rebuilt in this attempt; values from earlier runs are not reused.

Required results:

- Every command exits 0, and the typelint grep prints nothing.
- The `tests/go_police` pytest log shows the parity freshness, envelope contract and E2E
  tests PASSED, with zero skipped (`-rA` lists them).
- The CI-equivalent log (every `mise run ci` gate, with `uv build` redirected to
  `/tmp/telescreen-gdp-p10-dist`) ends with every gate green. If `ci` fails only in files owned by
  another plan, record a Reconciliation request with the log path; do not edit those
  files.

## Completion Criteria

- [ ] The binary builds via `mise run go-build`, and the `run` exit codes are tested.
- [ ] The E2E passes offline and meets every assertion in step 6 and the leak check.
- [ ] `go-lint`, `go-test`, the `tests/go_police` suite and the CI-equivalent sequence exit 0, with log
      paths recorded.
- [ ] Progress Log complete, including the uvicorn-or-fallback decision.
- [ ] Run 5: verbose Go run exit 0. `testsRun` (`--- PASS:` count) is > 0 and
      `failureCount` (`--- FAIL:` count) is 0, both from `$EV/gotest-v.log`.
- [ ] Run 5: `tests/go_police -rA` exit 0, 18 passed, 0 skipped.
- [ ] Run 5: `go/bin` audit recorded: `go1.27.3` and the sha256 of both binaries, rebuilt
      in this attempt.
- [ ] Run 5: CI-equivalent sequence exit 0. `mise.toml` is unchanged against `cae1af3`.
      There is no `dist/` entry.
- [ ] Run 5: `gitleaks dir --config .gitleaks.toml` exit 0 for
      `go/cmd/telescreen-gdrive-police` and `tests/go_police`.
- [ ] Run 5: a Progress Log session records every command with its exit status and log
      path, and the pre/post plan hashes. `priorVerification` is empty. Any minimal fix
      lists its failing log and the pre/post file hashes.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)

### Session: 2026-10-31
**Tasks Completed**: Added command wiring and focused exit-code test. `mise run go-build` produced the binary.
**Tasks In Progress**: Offline fake estate and three-sweep E2E, remaining verification, and the uvicorn-or-fallback decision.
**Blockers**: `mise run go-lint` exits 1 before running lint: mise rejects the `{{.Dir}}` task template in `mise.toml`.
**Reconciliation requests**: gdp-p01 owner: repair `mise.toml` task `go-lint` so mise accepts its Go template while preserving gofmt and go vet checks; rerun the required gates. This path is outside gdp-p10 writePaths.
**Hashes**: `go/cmd/telescreen-gdrive-police/main.go` before=absent after=dd3680b195a5e0fb1fed1eaf3ed932b461be915ca9511502d68e1958ef2eeff4; `run.go` before=absent after=b6f3a1f783f75ba1fecd50f1a53d6f11fd9ca2a7da4d59bc1a5922d7317a2a72; `run_test.go` before=absent after=a5f79f7434dfc7973273537c74de391dff4ecb4b0a250015e91cf7996eb7e8f1; plan before=abe74b1f3d2f1aa6a83af65df3889dab555879ae76aaaa69400d5989fc7b442f.
**Verification**: `cd go && GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -v ./cmd/telescreen-gdrive-police` exit 0 (log: `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-1/cmd-test.log`); `mise run go-test` exit 0 (log: `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-1/gotest.log`); `mise run go-lint` exit 1 (log: `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-1/golint.log`); `mise run go-build` exit 0 (log: `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-1/gobuild.log`).
**Notes**: Build audit: `mise exec -- go version` exit 0: `go version go1.27.3 darwin/arm64`; `shasum -a 256 go/bin/telescreen-gdrive-police` exit 0: `ccc82af02850b3de2db8f55d824e13786e5375fbe2c94abf258c17a49be1e499`. No tool installation was performed. Formal reviews and finalization remain downstream.

### Owner resolution (2026-10-31, run 2 -> run 3): go-lint task repaired

Run 2 blocked this plan because mise renders task scripts with Tera, and the Go template
`{{.Dir}}` in the `go-lint` task (owned by accepted gdp-p01) failed validation before lint
started. The owner replaced the file listing with
`find . -name '*.go' -not -path './vendor/*' -not -path './bin/*' -exec gofmt -l {} +`;
gofmt and `go vet ./...` are both preserved. Verified by the owner: `mise run go-lint` exit 0,
and a deliberately misformatted file made it exit 1 (file removed). Do not edit `mise.toml`.
gdp-p01..gdp-p09 are accepted dependencies at the run-3 checkpoint. Continue from the
partial run-2 session (the cmd package and its unit test exist): implement the offline fake
estate and the E2E, decide uvicorn-or-fallback and record the decision in this Progress
Log (prefer the real `uvicorn.Server` on port 0, which is a declared dependency), then run
every final gate, `mise run ci` included. Re-run all gates yourself and leave
`priorVerification` empty.

### Owner resolution (2026-10-31, run 3 -> run 4): artifact boundary for the E2E and CI

Run 3 blocked because the E2E built its binary under pytest `tmp_path` and `mise run ci`
runs `uv build` into `dist/`. Owner amendments (already applied above):

- The E2E builds `go/bin/telescreen-gdrive-police-e2e` with `-trimpath`, inside the
  declared artifact root `go/bin` (gitignored). Record its sha256 next to the
  `go/bin/telescreen-gdrive-police` audit record.
- The plan's CI gate is the exact `mise run ci` sequence with the final `mise run build`
  replaced by `uv build --out-dir /tmp/telescreen-gdp-p10-dist` (the 2026-10-08 ruling in
  `impl-plans/conventions.md` section 7). Do not run the literal `mise run ci` and do not
  edit `mise.toml`; the owner runs the literal `mise run ci` after the workflow.
- Gitignored tool caches that pytest, ruff, uv and Python create (`.pytest_cache`,
  `.ruff_cache`, `__pycache__`, `.venv`) and everything under `/tmp` or the user's home
  caches are not artifacts, need no `artifactRoots` entry and are never a blocker. The
  service-split runs used them the same way.
- pytest `tmp_path` stays the place for the generated service-account JSON and other test
  data; it is outside the repository and is not an artifact.

Re-run every gate yourself and leave `priorVerification` empty.


### Session: 2026-10-31 (run 4, partial)
**Tasks Completed**: Rechecked the existing command package. The focused exit-code test, Go lint, Go test and Go build passed.
**Tasks In Progress**: The fake estate and offline three-sweep E2E are not yet implemented. The uvicorn decision, E2E binary audit and remaining required gates are outstanding.
**Blockers**: None identified; implementation remains incomplete.
**Verification**: `mise run go-lint`, `mise run go-test`, `mise run go-build`, and `mise exec -- go version` exited 0; logs: `tmp/telescreen-gdp-20261031-h4/gdp-p10-cmd-e2e/attempt-1/{golint,gotest,gobuild,go-version}.log`. From `go/`, `GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -v ./cmd/telescreen-gdrive-police` exited 0 (one test; `attempt-1/cmd-test.log`). Go version: `go1.27.3 darwin/arm64`; built binary SHA-256: `8a1b8da700b8f42568fcbfcc7bfa5f9af7bf5956f1b87a71f0fae2f0340b0e55`.
**Hashes**: Plan before `8e066df37c091b0fa36c7b63d8087e37acd0c7a616ec3a5742297deeb773c64a`; post-edit hash recorded in `attempt-1/plan-after.sha256`.
**Notes**: Formal reviews, documentation finalization, archive and commit belong to downstream workflow steps.

### Owner resolution (2026-10-31, run 4 -> run 5): E2E authored by the owner; verify and finalize

Run 4 ended blocked-only again: the plan stopped after a partial session (cmd package
re-checked, fake estate and E2E not written), and the gate rejected `mise run go-test`
recorded with `testsRun: 0` (see the 2026-10-31 Go test-count ruling in
`impl-plans/conventions.md` section 7). Because a lone incomplete plan cannot be
redispatched within a run, the owner wrote deliverables 2 and 3:

- `tests/go_police/e2e_fakes.py` (fake token, Drive, Sheets and Admin server on one
  `ThreadingHTTPServer`, frozen `FakeEstate`, runtime RSA key, JWT verification of
  signature, audience, issuer, scope and subject, unexpected-request log).
- `tests/go_police/test_gdrive_police_e2e.py` (one async test, steps 1-8).

Decision: the real `uvicorn.Server` on port 0 serves `create_app(world.container)`, so the
fallback app is not used. `lifespan="off"` (the harness never runs the lifespan, and it
would close the container twice), `timeout_graceful_shutdown=5`.

Deviations from step 6, all stricter or required for determinism: the loop also waits for
an empty `inventory_outbox` (inventory delivery is asynchronous); the configs use
`trigger_mode: "event"` so no cron `detect` runs; exposures are checked on the issues of
resources that have findings (text file `anyone_with_link`, Doc `external_shared`, Sheet
`domain`); the 404 and trashed files have no issue at all; additional checks: one
succeeded shard per job with `resources_failed = 0`, no `failed_shards`, no audit
`dropped` counts, no `result rejected` in worker output, worker exit 0 on SIGTERM, no
unexpected fake requests.

Owner verification (2026-10-31): `uv run --frozen pytest -p no:cacheprovider tests/go_police -rA`
exit 0, 18 passed, 0 skipped, twice (E2E about 8-10 s; 13 consecutive E2E passes during
authoring); ruff format/check, ty and the typelint grep clean; gitleaks with
`--config .gitleaks.toml` on `tests/go_police` exit 0; verbose Go tests 88 passed, 0 failed.

Pre-existing Bureau defects found by the E2E are recorded in design section 15 as owner
follow-ups (police 204 responses with a `null` body; SQLite-only issue-number race). They
are outside this plan's writePaths: do not edit `src/`.

Run 5 instructions: do NOT rewrite the two E2E files unless a gate fails, and then only
the minimal fix. Re-run every final gate from the Verification section yourself with real
counts (Go via the verbose run, pytest with `-rA`), record the `go/bin` audit for both
binaries, the CI-equivalent sequence and gitleaks, complete the Completion Criteria and
this Progress Log, and leave `priorVerification` empty.
