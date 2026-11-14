# SVC-P08 Verification and Deployment: Multi-Process Tests, Restricted Logins, Leak Matrix, Compose, Docs

**Status**: Completed (run 11 re-verify passed; archived to `impl-plans/completed/` by serial finalization, 2026-10-16)
**Design Reference**: design-docs/specs/design-service-split.md sections 6, 13-17; design-docs/specs/design-service-split-requirements.md section 7
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p08-verification-deploy`
- planPath: `impl-plans/active/svc-p08-verification-deploy.md`
- wave: F (manifest `telescreen-svc-20261016-g11`, the only dispatched wave)
- dependsOn: none open; svc-p01 to svc-p07 are accepted dependencies at `66531bd`
- writePaths:
  - `impl-plans/active/svc-p08-verification-deploy.md`
  - `tests/split`
  - `tests/inventory_service/test_process.py`
  - `tests/inventory_service/test_postgres.py`
  - `tests/e2e/test_inventory_runtime_grants.py`
  - `tests/leak`
  - `tests/examples/test_examples.py`
  - `examples/compose/docker-compose.services.yml`
  - `examples/compose/docker-compose.inventory.yml`
  - `examples/compose/.env.example`
  - `examples/compose/README.md`
  - `design-docs/specs/design-service-split.md`
  - `design-docs/specs/design-service-split-verification.md`
  - `design-docs/specs/command.md`
  - `design-docs/specs/design-inventory-microservice.md`
- sharedPaths: none
- artifactRoots: none (build outputs go to `/tmp`)

## Intent and Context

This plan proves R1-R8 end to end and ships the deployment artifacts: three uvicorn
processes over TCP on three SQLite files, restricted-login grants, the leak matrix, the
compose overlay and the runbook docs. Runs 1-10 delivered all of it. At `66531bd` every
gate was green in run 10: the process suite passed 7 tests 3 times, the scoped suite
passed 180, `mise run ci` passed 1402 with 35 skipped, and ddl-check, web-ci and
`uv build` passed.

The workflow progress gate still blocked run 10 (owner resolution 4, Progress Log below).
Its verification list contained the no-skip-marker grep with the literal test-runner
word in it. The gate classifies any command containing that word as a test run, and a
grep has no passed count, so it was counted as a zero-count test run. This was a false
positive, not a product or test defect.

Run 11 re-runs the final gates on the unchanged checkpoint source. The skip-marker grep
is phrased without the test-runner word. The verification doc is refreshed with the g11
evidence. No code changes.

## Non-goals

- No change to `src/**`, `db/**`, any test module, compose file, `command.md`,
  `design-inventory-microservice.md` or `design-service-split.md`. The design status
  line and section 17 were already updated by the run 11 design step, and serial
  finalization sets the final status. The only file edits in run 11 are
  `design-docs/specs/design-service-split-verification.md` (R11.2) and this plan's
  Status line, Completion Criteria and Progress Log (R11.3).
- No new tests, no longer timeouts, no retries, and no weakening of any assertion or
  gate.
- No live-service tests in verification. The owner runs PostgreSQL restricted-login
  and Mailpit/Keycloak checks with `examples/compose/dev-services.yml`. Offline-skipped
  integration tests are neither verification nor risks (conventions 7a).
- No plan archiving, no manifest writes, and no edits to `impl-plans/README.md`,
  `README.md`, `impl-plans/logs/**` or the Nodetto document. Serial finalization does
  that.
- No git checkout, restore, stash, reset, clean, add, commit or push.

## Deliverables

Deliverables of runs 1-10 are archived in
[`../logs/svc-p08-verification-deploy-2026-10-16.md`](../logs/svc-p08-verification-deploy-2026-10-16.md)
(runs 1-7),
[`../logs/svc-p08-verification-deploy-2026-10-16-run9.md`](../logs/svc-p08-verification-deploy-2026-10-16-run9.md)
(Run 9 Scope and the run 8-9 Progress Log) and
[`../logs/svc-p08-verification-deploy-2026-10-16-run10.md`](../logs/svc-p08-verification-deploy-2026-10-16-run10.md)
(Run 10 Scope R10.1-R10.4 and the run 10 Progress Log, including the final run 10
gates). Run 11 delivers only R11.1-R11.3.

## Key Pitfalls

- Do not list any non-test command whose text contains the test-runner word. Use the
  skip-marker grep exactly as in Verification item 10, and never the run 10 form. Every
  real test command must report its passed count.
- `priorVerification` stays empty. A failed attempt is not verification. If a gate fails
  and is then fixed, the fixed failure goes into the Progress Log, and only the final
  passing run is listed (conventions 7a).
- Run commands one at a time in the foreground. Never run the process suite
  concurrently with `mise run ci`: both start uvicorn processes and share the 32 GB host.
- The process suite takes about 560 s per iteration (about 28 min for 3). Use a tool
  timeout that covers it, or run the three iterations as three separate foreground
  commands with one log each. Do not background them.
- Directory-stream GAP/`conflict` retries in process logs are expected behavior (owner
  resolution 2). They are neither a failure nor a risk.
- After the process loop, check that no service process survived (Verification item 6)
  before starting `mise run ci`.

## Run 11 Scope (manifest g11, base 66531bd) - authoritative

### R11.1 Final gates (no code change)

Run Verification items 1-12 below on the checkpoint source, in order. Write each log to
`/tmp/telescreen-svc-p08-verification-deploy-g11-<name>.log`, where `<name>` is the item
label. Record the exit code, the passed/skipped counts for test commands and the full
log path for every item.

If any item fails:

1. Keep the failing log. Reproduce the failure with the single failing test id or
   command, and record test id, log path and observed state in the Progress Log.
2. Owner diagnosis rule: find the root cause. If the cause is inside this plan's
   writePaths (for example a test timing assumption in `tests/split/**`), fix it there
   at the root, without a blind timeout increase or retry, and record pre/post SHA-256
   hashes. If the cause is in `src/**`, `db/**` or another plan's files, write a
   "Reconciliation request" (test id, log path, observed row state, suspected owner
   path) and stop. Do not edit those files.
3. After any fix, re-run the whole Verification list from item 1, including all three
   process iterations from scratch. Only that final passing run is verification.

### R11.2 Verification doc

In `design-docs/specs/design-service-split-verification.md`, change only these parts:

- the introduction: "continuation run 10" -> "continuation run 11", and the sentence
  that says which source the results come from (the run 11 re-verify of `66531bd`);
- in the requirement table, the R1, R3 and R5 rows' g10 process log paths -> the g11
  paths. Keep the test ids, wording and claims unchanged unless a run 11 result differs;
- in the "Commands and results" table, every `-g10-` log path -> `-g11-`, and every count
  and duration -> the run 11 values (format file count, process 7 passed per run and
  durations, scoped suite passed/skipped, `mise run ci` passed/skipped/warnings, ddl test
  count, web test count);
- the skip-marker row: name the command as the no-skip-marker grep
  `! grep -nE 'mark\.skip|\.skip\(|skipif' <process modules>`, without the test-runner
  word.

The doc must not claim results for the skipped PostgreSQL/live checks. Leave the R6 and
R8 rows unchanged.

### R11.3 Plan bookkeeping

After every item exits 0, set Status to `Completed (run 11 re-verify passed; serial
archive/index/commit/push remain downstream)`, tick the run 11 Completion Criteria, and
append a "Run 11 re-verify" Progress Log entry. The entry lists each final command with
its exit code, counts and log path, plus the R11.2 SHA-256 pre/post hashes. Keep the plan
under 400 lines.

## Invariants

- `git diff --quiet HEAD -- src db tests examples` exits 0 at the end. Run 11 changes
  no code, test or example, unless R11.1 step 2 fixed a root cause in this plan's test
  paths; in that case, record it and this item lists only those paths.
- The only changed files are
  `design-docs/specs/design-service-split-verification.md` and this plan, plus any R11.1
  root-cause fix. This is checked with `git status --porcelain`, ignoring files changed
  by the run 11 planning commit.
- Every file under `tests/split` and `tests/leak` stays under 1000 lines. This plan
  stays under 400.
- The process modules contain no skip markers.
- Raw samples never enter any table, log, outbox, replica, wire body, notification or
  export (the leak matrix proves it).
- Per-edit discipline: read each file fresh before editing. Record pre/post SHA-256 and
  an intent snapshot under
  `tmp/telescreen-svc-20261016-g11/svc-p08-verification-deploy/attempt-<n>/`. On
  unexpected drift, write a Reconciliation request and do not overwrite.

## Verification (run 11; logs `/tmp/telescreen-svc-p08-verification-deploy-g11-<name>.log`)

Prefix every command with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1;`. Each must
exit 0 and show the stated evidence:

1. format: `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` -> "files already formatted".
2. ruff: `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` -> "All checks passed".
3. ty: `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` -> "All checks passed".
4. typelint: `uv run --frozen python tools/typelint.py > <log> 2>&1; ! grep -E "^($(pwd)/)?tests/(split|leak|inventory_service)" <log>` -> exit 0, no p08 diagnostics.
5. process-1, process-2, process-3: `uv run --frozen pytest -p no:cacheprovider -q tests/split/test_replay_process.py tests/split/test_stopped_telescreen_process.py tests/split/test_cutover_process.py tests/leak/test_leak_matrix_split.py tests/inventory_service/test_process.py > <log-i> 2>&1`, run three times in sequence -> each "7 passed", 0 skipped.
6. pgrep-final: `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'` -> no surviving service process.
7. scoped: `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e` -> at least 180 passed; skips are only the environment-gated integration tests.
8. ci: `mise run ci` -> at least 1402 passed (the run 10 count at `66531bd`), with format, ruff, typelint, ty and build passing.
9. ddl: `mise run ddl-check` -> exports canonical and the schema tests pass. web: `mise run web-ci` -> typecheck, vitest and build pass.
10. skips: `! grep -nE 'mark\.skip|\.skip\(|skipif' tests/split/test_replay_process.py tests/split/test_stopped_telescreen_process.py tests/split/test_cutover_process.py tests/leak/test_leak_matrix_split.py tests/inventory_service/test_process.py` -> no match.
11. build: `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g11-dist` -> sdist and wheel built.
12. source-diff and line-limits: `git diff --quiet HEAD -- src db tests examples`; `for f in tests/split/*.py tests/leak/*.py; do test "$(wc -l < "$f")" -lt 1000 || exit 1; done`; `test "$(wc -l < impl-plans/active/svc-p08-verification-deploy.md)" -lt 400`.

## Completion Criteria

- [x] R11.1: Verification items 1-12 exit 0 on checkpoint `66531bd`; exits, counts and full g11 log paths are recorded below. The process suite passed 3 times with 7 passed and 0 skipped.
- [x] R11.2: the verification doc cites run 11 and the g11 logs and counts. The skip-marker row has no test-runner word.
- [x] R11.3: Status is Completed, `priorVerification` is empty, `addressedFeedback` lists only fixed findings, and only final passing commands are listed (conventions 7a).
- [x] Runs 1-10: compose topology, process and PostgreSQL tests, docs and design corrections, shared harness, stopped-Telescreen, replay and cutover flows, and the leak matrix with detected-ingest non-vacuity (archived logs).

## Progress Log

Earlier entries are archived in the three log files named under Deliverables. Append
run 11 sessions below, following conventions section 8.

### Owner resolution 4: 2026-10-16 (after session 27)

Run 10 finished all p08 work with every gate green (process suite 3x 7 passed, scoped
suite 180 passed, `mise run ci` 1402 passed / 35 skipped, ddl-check, web-ci, build).
The workflow progress gate still blocked it because the verification list contained the
no-skip-marker check with the test-runner word in its pattern. Any command containing
that word is classified as a behavioral test run, and a grep has no positive test count,
so it was counted as a zero-count test. This is a false positive. Re-run the final gates
on the checkpoint and report the skip-marker check without the literal word, for example
`! grep -nE 'mark\.skip|\.skip\(|skipif' <files>`. Keep every real test command with its
passed count. Keep `priorVerification` empty and change no code (conventions 7a).

### Run 11 planning (2026-10-16)

Manifest g11 supersedes g10 with the same writePaths. svc-p01 to svc-p07 are accepted
dependencies at `66531bd`. The Run 10 Scope and the run 10 entries (owner resolution 3,
run 10 planning, run 10 implementation and gates) moved unchanged to the `-run10` log.
Owner resolution 4 is restated as R11.1-R11.3. The new skip grep returned 0 matches
in all five process modules at planning time (`grep -cE` count check). That is a
planning check, not verification.

### Run 11 re-verify (2026-10-16)

All gates passed on checkpoint `66531bd`; no production, DDL, test or example files
changed. The verification doc changed from SHA-256
`e2da80e79034d4f3a28b0fd80cca0e55bef25a55ec2d934732739ccad8740725` to
`9343c279134bfa537b8156465131297b5b2c9327b95f818e8b6905f7a66d1744`.

- Format, exit 0: 72 files already formatted. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-format.log`.
- Ruff, exit 0: all checks passed. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-ruff.log`.
- `ty`, exit 0: all checks passed. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-ty.log`.
- Typelint and p08 path filter, exit 0: 790 files clean, no p08 diagnostics. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-typelint.log`.
- Process run 1, exit 0: 7 passed, 0 skipped, 569.62s. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-process-1.log`.
- Process run 2, exit 0: 7 passed, 0 skipped, 565.35s. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-process-2.log`.
- Process run 3, exit 0: 7 passed, 0 skipped, 565.72s. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-process-3.log`.
- Final service-process check, exit 0: no service process survived. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-pgrep-final.log`.
- Scoped suite, exit 0: 180 passed, 4 environment-gated skips, 651.86s. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-scoped.log`.
- `mise run ci`, exit 0: 1402 passed, 35 skipped, 1 warning, 773.50s. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-ci.log`.
- `mise run ddl-check`, exit 0: canonical exports and 10 schema tests passed. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-ddl.log`.
- `mise run web-ci`, exit 0: typecheck, 262 tests and production build passed. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-web.log`.
- Skip-marker grep, exit 0: no matches. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-skips.log`.
- `uv build`, exit 0: sdist and wheel built. Log:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-build.log`.
- Source-diff and line-limit checks, exit 0: no source/DDL/test/example changes; Python
  files under 1000 lines and this plan under 400 lines. Logs:
  `/tmp/telescreen-svc-p08-verification-deploy-g11-source-diff.log` and
  `/tmp/telescreen-svc-p08-verification-deploy-g11-line-limits.log`.

`priorVerification` remains empty; only passing final commands are listed. PostgreSQL
restricted-login and live Mailpit/Keycloak checks remain owner-run. Serial archive,
index, README/status finalization, commit and push are downstream workflow work.
