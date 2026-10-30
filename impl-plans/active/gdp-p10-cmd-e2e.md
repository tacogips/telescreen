# GDP-P10 Binary Wiring (cmd/telescreen-gdrive-police) and Offline End-to-End Sweep Test

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p10-cmd-e2e/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker; `go/bin` is this plan's only artifact root.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 2, 3, 8.1, 10, 11, 15, 16
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p10-cmd-e2e`
- planPath: `impl-plans/active/gdp-p10-cmd-e2e.md`
- wave: 4
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
      never a skip. Then `subprocess.run(["go", "build", "-o", <tmp>/telescreen-gdrive-police,
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

## Tests (input -> expected)

- `go test ./cmd/...`: the four `run_test.go` cases above.
- E2E: delegated estate, three sweeps -> all succeed. Findings, exposures and inventories
  are applied as listed. No rejection. No leak in worker output.

## Verification (this plan is alone in its wave; module-wide gates are required here)

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
mise run go-lint > /tmp/telescreen-gdp-p10-golint.log 2>&1; echo "exit=$?"
mise run go-test > /tmp/telescreen-gdp-p10-gotest.log 2>&1; echo "exit=$?"
mise run go-build > /tmp/telescreen-gdp-p10-gobuild.log 2>&1; echo "exit=$?"; ls -l go/bin/telescreen-gdrive-police
mise exec -- go version
shasum -a 256 go/bin/telescreen-gdrive-police
mise exec -- uv run --frozen pytest -p no:cacheprovider tests/go_police -rA 2>&1 | tee /tmp/telescreen-gdp-p10-pytest.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tests/go_police
uv run --frozen ruff check --no-cache tests/go_police
uv run --frozen ty check tests/go_police
uv run --frozen python tools/typelint.py > /tmp/telescreen-gdp-p10-typelint.log 2>&1; grep -E '^tests/go_police' /tmp/telescreen-gdp-p10-typelint.log
mise run ci > /tmp/telescreen-gdp-p10-ci.log 2>&1; echo "exit=$?"
```

The `go/bin` artifact audit record goes in the Progress Log: the `go version` output, the
build command (`mise run go-build`) with its exit code, and the sha256 of
`go/bin/telescreen-gdrive-police`.

Required results:

- Every command exits 0, and the typelint grep prints nothing.
- The `tests/go_police` pytest log shows the parity freshness, envelope contract and E2E
  tests PASSED, with zero skipped (`-rA` lists them).
- The `mise run ci` log ends with every gate green. If `ci` fails only in files owned by
  another plan, record a Reconciliation request with the log path; do not edit those
  files.

## Completion Criteria

- [ ] The binary builds via `mise run go-build`, and the `run` exit codes are tested.
- [ ] The E2E passes offline and meets every assertion in step 6 and the leak check.
- [ ] `go-lint`, `go-test`, the `tests/go_police` suite and `mise run ci` exit 0, with log
      paths recorded.
- [ ] Progress Log complete, including the uvicorn-or-fallback decision.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
