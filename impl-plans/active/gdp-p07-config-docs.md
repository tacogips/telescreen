# GDP-P07 Worker Configuration Package and Operator Documentation

**Status**: Ready
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 2, 6, 12, 13, 14
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p07-config-docs`
- planPath: `impl-plans/active/gdp-p07-config-docs.md`
- wave: 2
- dependsOn: `gdp-p01-foundation`
- writePaths:
  - `impl-plans/active/gdp-p07-config-docs.md`
  - `go/internal/config`
  - `go/README.md`
  - `examples/police/gdrive-go/README.md`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8.

## Intent and Context

Two independent deliverables:

1. The env-only configuration of the binary (design 2). Secrets come only from env vars
   or from files named by env vars, and messages never echo values.
2. The operator docs required by the acceptance criteria: `go/README.md` and the
   `examples/police` deployment note.

For the Python env names this mirrors, see `src/telescreen/core/config.py` (`SCAN_MAX_BYTES`,
`GOOGLE_DELEGATED_ADMIN`, `SWEEP_SHARD_SIZE`, `INTERNAL_DOMAINS`, `_comma_values` with
`lowercase=True`). For the example README style, see `examples/police/python/README.md`.

## Non-goals

- No parsing of the service-account JSON (gdp-p04) and no https check of its `token_uri`
  (gdp-p10 does that after parsing).
- No `main`. No edits to `design-docs/`, `mise.toml` or other examples.

## Deliverables

1. `go/internal/config/config.go` (+ `config_test.go`):

   ```go
   type Config struct {
       BureauURL string; EnrollmentToken string; FingerprintKey []byte
       ServiceAccountFile string; DelegatedAdmin *string; InternalDomains []string
       ScanMaxBytes int; SweepShardSize int; Name string; Labels []string; Concurrency int
       GoogleAPIBaseURL, SheetsBaseURL, AdminBaseURL string; AllowHTTP bool
   }
   type Error struct{ Variable string; Reason string } // Error() = "invalid configuration: <Variable>: <Reason>"; Reason is a static phrase
   func Load(lookup func(string) (string, bool), readFile func(string) ([]byte, error)) (Config, error)
   ```

   Rules (design 2 table):
   - `TELESCREEN_URL` is required.
   - `TELESCREEN_POLICE_ENROLLMENT_TOKEN` or `_FILE`: exactly one must be set. Setting
     both is an error, and so is setting neither.
   - `TELESCREEN_POLICE_FINGERPRINT_KEY` or `_FILE`: exactly one. The value must be at
     least 32 bytes (UTF-8 bytes; file content is used as-is with one trailing newline
     trimmed). The same newline trim applies to the token file.
   - `TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE` is required (a path; it is not read here).
   - `TELESCREEN_GOOGLE_DELEGATED_ADMIN`: optional; empty means unset.
   - `TELESCREEN_INTERNAL_DOMAINS`: comma-split, trimmed, lowercased (ASCII lower plus
     Go `unicode.ToLower` is acceptable here, because gdp-p06 applies the Python-exact
     `CaseFold`), empties dropped.
   - `TELESCREEN_SCAN_MAX_BYTES` (default 5242880, at least 1).
   - `TELESCREEN_SWEEP_SHARD_SIZE` (default 100, 1-1000).
   - `TELESCREEN_POLICE_NAME` (default `telescreen-gdrive-police`, 1-100 chars).
   - `TELESCREEN_POLICE_LABELS` (default `police=gdrive`). Each label must match
     `^[a-z0-9_.-]+=[A-Za-z0-9_.:/-]+$`.
   - `TELESCREEN_POLICE_CONCURRENCY` (default 4, 1-64).
   - Base URLs default to `https://www.googleapis.com`, `https://sheets.googleapis.com`
     and `https://admin.googleapis.com`.
   - `TELESCREEN_POLICE_ALLOW_HTTP` is `true` or unset; any other value is an error.
     Without it, `TELESCREEN_URL` and the three base URLs must have scheme `https`.
     Parse them with `net/url` and require a host.
2. `go/README.md`:
   - Purpose and status: optional, and the Python collector remains the default.
   - Build and test commands: `mise run go-build`, `go-test`, `go-lint`.
   - Run command and exit codes (0 graceful, 2 config, 3 unauthorized).
   - The full env table from design 2.
   - The selector document (design 6).
   - Logging guarantees.
   - Parity guarantees and limits: rule detector only; ML and LLM are out of scope;
     divergences V1-V11 in short form. V1 is withdrawn: 410 is a fetch failure, like
     Python.
   - Contract gaps G1-G5, including the shard-wide consequence of `resources_failed > 0`
     (design 8.1).
   - Links to the design doc and the user-qa file.
3. `examples/police/gdrive-go/README.md`:
   - Enrollment: the Bureau `TELESCREEN_POLICE_ENROLLMENT_TOKEN` secret ref and the
     worker env.
   - A `police` kind collector config JSON with `labels: ["police=gdrive"]` and the
     `input` selector `{"kind":"gdrive","drive_ids":[...]}`, using placeholder ids such as
     `0AExampleDriveId`.
   - Starting a sweep with `POST /api/collectors/{id}/sweep`.
   - A workflow `map` step with `via: police` (`task_type: partition` / `scan_shard`),
     following design-map-reduce-sweeps.md.
   - Scaling: replicas with the same labels, `TELESCREEN_POLICE_CONCURRENCY` and
     `max_concurrency`.
   - The note that findings appear under `police:<config>`, so running it alongside a
     built-in `gdrive` collector double-reports.
   - No real credentials, tenant ids or e-mails (use `example.com`).

## Key Pitfalls

- Error messages name the variable only, never its value. This also applies to labels
  and URLs, since a URL may carry credentials in userinfo.
- `lookup` is injected. Never call `os.Getenv` inside `Load`, so tests stay hermetic.
- No package-level mutable state. Compile the label regexp inside `Load`, or use a
  hand-written check.
- Docs must not contradict the design. Copy the env table and the gap list, and note
  that `[REDACTED]`/`raw_value_guard` behaviors exist.

## Tests (config_test.go; input -> expected)

- A minimal valid env -> defaults applied (5242880, 100, 4, `police=gdrive`, Google base
  URLs).
- Both `TELESCREEN_POLICE_ENROLLMENT_TOKEN` and `_FILE` set ->
  `Error{Variable:"TELESCREEN_POLICE_ENROLLMENT_TOKEN"}`, and the message does not
  contain the token value.
- A fingerprint key of 31 bytes -> error; a file-based key with a trailing newline of
  32+1 bytes -> 32-byte key.
- `http://` Bureau URL without allow -> error; with `TELESCREEN_POLICE_ALLOW_HTTP=true`
  -> ok.
- `TELESCREEN_POLICE_ALLOW_HTTP=yes` -> error.
- Label `Bad Label` -> error naming `TELESCREEN_POLICE_LABELS`.
- Shard size 0 and 1001 -> errors; concurrency 65 -> error.
- `TELESCREEN_INTERNAL_DOMAINS=" Example.COM, ,b.org"` -> `["example.com","b.org"]`.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -race -count=1 ./internal/config/... 2>&1 | tee /tmp/telescreen-gdp-p07-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/config/... 2>&1 | tee /tmp/telescreen-gdp-p07-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/config   # must print nothing
cd .. && grep -nE 'Mode|TELESCREEN_|G1|V3|police:<config>' go/README.md examples/police/gdrive-go/README.md | wc -l   # non-zero
```

The Go commands exit 0. Both READMEs exist, contain the env names, the gaps and the
`police:<config>` note, and contain no emojis.

## Completion Criteria

- [ ] `config.Load` implements every rule above, and the tests pass with `-race`.
- [ ] Both READMEs are written with the sections listed and are consistent with the
      design (including the V1 owner decision).
- [ ] `vet` and `gofmt` are clean. Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
