# P28 Assembly (Container, Collector Bindings, CLI, Serve) Implementation Plan

**Status**: Completed (run B2: p28a role)
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Assembly bindings, run B2), design-docs/specs/command.md, design-docs/specs/architecture.md sections 3, 4, 8 (Run B2), design-docs/specs/design-ui-sentry.md 8.6
**Created**: 2026-10-08
**Last Updated**: 2026-10-09 (run B2, session 342)

## Plan Metadata

- planId: `p28-assembly` (the "p28a" row of architecture.md section 8, Run B2)
- planPath: `impl-plans/active/p28-assembly.md`
- wave: B3-1 in the s345 manifest (runs alone; B2-1 in s342)
- dependsOn: the 29 accepted plans at cdf195d (see the s345 dispatch manifest)
- writePaths:
  - `impl-plans/active/p28-assembly.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_builders.py`
  - `src/telescreen/wiring_collectors.py` (new)
  - `src/telescreen/api/app.py`
  - `src/telescreen/cli.py`
  - `src/telescreen/__main__.py`
  - `tests/test_package.py`
  - `tests/e2e/test_serve_static.py`
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_wiring_collectors.py` (new)
  - `tests/e2e/test_wiring_cli.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 3, 6, 7, 7a, 8).

## Run B2 split

Run B2 splits the old p28 into three plans with disjoint write paths:

- this plan (container, collector bindings, CLI, serve);
- `p28b-e2e-flows` (E2E flows; owns `tests/e2e/harness.py` and `tests/e2e/test_flow_*.py`);
- `p28c-leak-matrix` (owns `tests/leak`).

Both p28b and p28c start after this plan. Do NOT create or edit `tests/leak/**`,
`tests/e2e/harness.py` or `tests/e2e/test_flow_*.py`. The E2E and leak-matrix criteria
moved to those plans.

## Run B3 (s345): re-verify only

The deliverables below are in the tree at cdf195d and pass all gates. In run B3:

- Do not edit source or tests. Fresh-read and record sha256 of every writePath first.
- Run the Verification block below once on the current tree, unchanged.
- Append one Progress Log session listing each command, its exit code and log path.
- Step result: `verification` lists ONLY those final passing commands; `priorVerification`
  is empty; no mutation run or superseded attempt appears outside the Progress Log;
  `addressedFeedback` lists only fixed findings; `findings`/`risks` only open items
  (conventions 7a).
- If a gate fails, fix only within this plan's writePaths, then re-run the whole block.

## Intent and Context

The in-tree work already provides:

- the Container with all 15 routers, workflow, police, evidence, map-reduce, scheduler
  and worker wiring;
- `create_app` with static UI serving (design-ui-sentry.md 8.6);
- the full argparse tree in `cli.py`;
- the static, route-smoke, CLI and UI-fixture tests.

See the Progress Log for details. Two defects remain:

1. `src/telescreen/wiring.py:214` builds an empty `CollectorRegistry()`. No built-in
   adapter is registered, so collector scans, webhooks and sweeps of slack, gdrive, gdocs,
   gsheets and github fail with `not_found`.
2. `src/telescreen/wiring.py:547-549` passes `CollectorSecrets(None, None)` for all three
   webhook kinds, so every real webhook is rejected (401).

This plan binds the adapters and secrets exactly as the "Assembly bindings" section of
design-collectors-and-ingestion.md specifies. It also adds behavior tests for the
bindings and the worker and collector CLI commands, so p28b and p28c can rely on them.

## Non-goals

- No feature-package edits (`src/telescreen/collectors/**`, `api/routers/**` and so on).
  If one is needed, write a Reconciliation request.
- No new setting, dependency, port or protocol. No `pyproject.toml` or `uv.lock` change.
- Do not fix `api/routers/escalation.py` (WorkflowValidationError -> 500). It is a known
  low follow-up outside this plan.
- No E2E flow tests and no leak matrix; p28b and p28c own those.
- Do not edit `src/telescreen/__init__.py`.

## Deliverables

### D1. `src/telescreen/wiring_builders.py`

- Add field `fingerprint_key: SecretValue = field(repr=False)` to `RuntimePrimitives`.
  Set it from the value that `build_primitives` already resolves. Do not resolve the key
  twice.

### D2. `src/telescreen/wiring_collectors.py` (new; target < 300 lines)

Public functions (these signatures are the contract with `wiring.py`):

```python
def drive_channel_key(fingerprint_key: SecretValue) -> bytes
def build_webhook_secrets(settings: TelescreenSettings, env: EnvSource, channel_key: bytes) -> WebhookSecrets
def build_collector_registry(settings: TelescreenSettings, env: EnvSource, http: httpx.AsyncClient, clock: Clock, channel_key: bytes) -> CollectorRegistry
def github_web_host(api_url: str) -> str
```

`drive_channel_key`:

- Compute `hmac.new(key, b"telescreen:gdrive-channel-token:v1", hashlib.sha256).digest()`.
- `key` must be the same bytes `Fingerprinter.from_secret` uses (`secret.value.encode("utf-8")`;
  see `src/telescreen/masking/fingerprint.py:Fingerprinter.from_secret`).

`build_webhook_secrets`:

- slack: `CollectorSecrets(<resolved TELESCREEN_SLACK_SIGNING_SECRET value or None>, None)`;
- github: `CollectorSecrets(<resolved TELESCREEN_GITHUB_WEBHOOK_SECRET value or None>, None)`;
- gdrive: `CollectorSecrets(None, channel_key)`.
- Unset ref -> `None`. The adapters already fail closed with 401.
- A ref that is set but does not resolve: let the `ConfigError` from `resolve_secret`
  propagate (exit 3 in the CLI).

`build_collector_registry` registers five kinds, each with `registry.register(adapter, sweep)`:

- slack:
  - `SlackAdapter(slack_factory, settings.scan_max_bytes)` and
    `SlackSweep(slack_factory, settings.scan_max_bytes)`.
  - `slack_factory` is a frozen-dataclass callable `(config: CollectorConfig) -> SlackApi`.
  - It returns `SlackApi(http, token)`. The token is `resolve_secret(config.credential_ref, env)`
    when `credential_ref` is set, else the global bot token resolved once at build time
    from `settings.slack_bot_token_ref`.
  - With neither -> `ConfigError("slack credential is not configured")`.
- github:
  - `GitHubAdapter(github_factory, github_web_host(settings.github_api_url), settings.scan_max_bytes)`
    and `GitHubSweep(<same factory>, <same host>, settings.scan_max_bytes)`.
  - The factory returns `GitHubApi(http, settings.github_api_url, token)` from
    `config.credential_ref` only (no global GitHub token exists).
  - No `credential_ref` -> `ConfigError("github credential is not configured")`.
- gdrive, gdocs, gsheets:
  - `build_google_adapters(drive_factory, sheets_factory, clock, channel_key, settings.public_base_url, settings.scan_max_bytes)`.
  - Register each returned adapter with `GoogleSweep(kind, google_clients, admin, settings.sweep_shard_size)`.
  - `google_clients` is one dataclass that implements both `DriveFactory`/`SheetsFactory`
    call shapes (or two small callables) and the `DriveAdapterFactory` protocol of
    `src/telescreen/collectors/google/sweep.py` (`drive()` and `adapter(kind)`, which
    returns the matching registered adapter).
  - When `settings.google_service_account_ref` is set, resolve it and parse it with
    `parse_service_account_key` at build time, then build one shared `GoogleAuth(http, key, clock)`.
    An unresolvable or invalid ref raises `ConfigError("google service account is invalid")`
    with a static detail. Never include the parse error text.
  - When the ref is unset, the factories raise `ConfigError("google credential is not configured")`
    on use.
  - `admin` is `AdminApi(http, auth, settings.google_delegated_admin)` only when both the
    delegated admin and the service account are set. Delegated admin without a service
    account -> `ConfigError` at build time. Otherwise `admin` is `None`.
  - Google kinds have config-free factories (accepted p22 API), so per-config
    `credential_ref` is not used for them. This is a documented limitation, not a defect
    to fix here.

`github_web_host`:

- Return the hostname of the API URL.
- Exception: `api.github.com` -> `github.com`.
- Example: `https://ghe.example.invalid/api/v3` -> `ghe.example.invalid`.
- Source of the rule: p23 plan, "host for raw locators".

Imitate:

- `tests/collectors/google/support.py:DriveClientFactory` for frozen callable factories
  (no lambdas: typelint needs annotated parameters);
- `src/telescreen/collectors/google/adapter.py:build_google_adapters`;
- `src/telescreen/police/signing.py:derive_push_secret` for the HMAC idiom.

### D3. `src/telescreen/wiring.py`

- Replace `CollectorRegistry()` with `build_collector_registry(...)`.
- Replace the `WebhookSecrets(...)` placeholders with `build_webhook_secrets(...)`.
- Compute `channel_key` once from `primitives.fingerprint_key`.
- Add the field `collector_registry: CollectorRegistry` to `Container`. Update the
  positional `Container(...)` construction in the same order.
- Keep `wiring.py` < 1000 lines (it is 614 now; the new logic lives in D2).

### D4. `cli.py` (verify; change only on a failing test)

The `worker`, `collector run/--watch` and `collector sweep` paths exist. Make the tests
in D5 pass without changing the documented behavior in command.md:

- `collector sweep` prints `collector=<name> sweep=<job id>`;
- an active sweep -> exit 1 with the masked `conflict` detail;
- an unknown collector name -> exit 3 (current `ConfigError`).

## Tests

All tests are offline. Use `tests.support.db.make_sqlite_harness` and the `_TestEnv`
pattern of `tests/e2e/test_api_routes.py`. For the 32-byte keys, use test secrets built
at runtime, for example `"t" * 32`. Never commit a literal token.

`tests/e2e/test_wiring_collectors.py`:

- No collector settings -> `container.collector_registry.adapter(kind)` succeeds and
  `.sweeper(kind)` is not None, for each of slack, gdrive, gdocs, gsheets and github.
- `drive_channel_key(SecretValue("t"*32))` is deterministic, 32 bytes, and different
  from the raw key bytes. The gdrive adapter's `channel_token_key` equals the
  `WebhookSecrets.gdrive.channel_token_key` value.
- Webhook through `create_app` + `ASGITransport` with `TELESCREEN_GITHUB_WEBHOOK_SECRET` unset ->
  `POST /api/collectors/github/webhook` with an `X-Hub-Signature-256` header -> 401.
- Secret set (env ref) -> a correctly signed `ping` or `push` request -> 200, and a
  wrongly signed one -> 401.
  - Build the signature in the test with `hmac` over the exact body bytes.
  - For `push`, first create an enabled github collector config through
    `POST /api/collectors`, so the event maps to a config. If the service needs fields
    you cannot satisfy, use `ping`.
- Slack signing secret set -> signed `url_verification` -> 200 with the challenge body.
  Unsigned -> 401.
- `TELESCREEN_GITHUB_WEBHOOK_SECRET="env:MISSING"` (the ref does not resolve) ->
  `build_container` raises `ConfigError`.
- github config without `credential_ref`:
  - calling the registered adapter's `api_factory(config)` raises `ConfigError`;
  - the static detail contains neither the ref target nor any secret.
- `github_web_host`:
  - `https://api.github.com` -> `github.com`;
  - `https://ghe.example.invalid/api/v3` -> `ghe.example.invalid`.

`tests/e2e/test_wiring_cli.py`:

- Setup:
  - use a file-backed SQLite URL under `tmp_path`;
  - create the tables with `Base.metadata.create_all`;
  - seed `CounterRow` and `AuditChainHeadRow` like `tests/support/db.py`;
  - set the env with `monkeypatch.setenv`.
- `main(["worker", "--once"])` on an empty queue -> 0.
- `main(["collector", "sweep", "nope"])` -> 3.
- With a github collector row `repo-sweep` inserted -> `main(["collector", "sweep", "repo-sweep"])`
  -> 0, stdout matches `sweep=`, and a map job row exists.
- A second identical call while the first is not terminal -> 1.
- `main(["token", "create", "--label", "x", "--scopes", "read"])` -> 0 and prints one
  line (keep the existing assertions if already covered).

Keep `tests/test_package.py`, `tests/e2e/test_serve_static.py` and
`tests/e2e/test_api_routes.py` green. Do not weaken their assertions.

## Key Pitfalls

- Never put a secret, token, ref target or parse error text into an exception detail, a
  log record, `repr` or stdout. All factory dataclasses holding secrets use
  `field(repr=False)`.
- Resolve global refs at build time. Resolve per-config `credential_ref` lazily inside
  the factory call. Do not resolve per-config refs at build time: the configs live in the
  DB.
- Do not change the adapter fail-closed behavior. `None` secrets must stay `None`; never
  default to an empty string, which would make the HMAC verifiable.
- The same `channel_key` bytes must reach both `build_google_adapters` and
  `WebhookSecrets.gdrive`.
- `Container` is constructed positionally. Insert the new field and argument
  consistently, or switch that single call to keyword arguments. Do not reorder
  existing fields used elsewhere.
- Only `wiring*.py` and `api/app.py` may import across L3 packages.

## Verification (full repository; all must pass)

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_wiring_collectors.py tests/e2e/test_wiring_cli.py tests/e2e/test_api_routes.py tests/e2e/test_serve_static.py tests/test_package.py 2>&1 | tee /tmp/telescreen-p28-assembly-focused.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache . 2>&1 | tee /tmp/telescreen-p28-assembly-format.log; echo "exit=$?"
uv run --frozen ruff check --no-cache . 2>&1 | tee /tmp/telescreen-p28-assembly-ruff.log; echo "exit=$?"
uv run --frozen python tools/typelint.py 2>&1 | tee /tmp/telescreen-p28-assembly-typelint.log; echo "exit=$?"
uv run --frozen ty check 2>&1 | tee /tmp/telescreen-p28-assembly-ty.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p28-assembly-pytest.log; echo "exit=$?"
uv build --out-dir /tmp/telescreen-p28-assembly-dist 2>&1 | tee /tmp/telescreen-p28-assembly-build.log; echo "exit=$?"
test -z "$(find src tests -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"; echo "exit=$?"
git diff --quiet -- pyproject.toml uv.lock; echo "exit=$?"
! grep -n "CollectorSecrets(None, None)" src/telescreen/wiring.py; echo "exit=$?"
test "$(grep -c '^- \[ \]' impl-plans/active/p28-assembly.md)" -eq 0; echo "exit=$?"
```

Expected results:

- Every command exits 0.
- The focused run shows the new tests passed and none skipped.
- The full pytest run shows 0 failed. Offline `integration` skips are expected.

## Completion Criteria

- [x] `RuntimePrimitives.fingerprint_key` added; `wiring_collectors.py` provides the four functions
- [x] All five built-in kinds registered with sweeps; `Container.collector_registry` exposed
- [x] Webhook secrets resolved per design (unset fails closed with 401; an unresolvable ref raises `ConfigError`); one Drive channel key shared by watch creation and verification
- [x] `tests/e2e/test_wiring_collectors.py` and `tests/e2e/test_wiring_cli.py` pass; existing assembly tests stay green
- [x] `telescreen serve` serves the built UI per design-ui-sentry.md 8.6 (static tests pass; missing index.html exits 3)
- [x] UI fixture leak test passes (file now owned by p28c)
- [x] Full-repository verification above passes on the final source (logs recorded)
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 18:10
**Tasks Completed**: Added shared runtime primitives and API composition for health, directory, audit, search and assistant chat; added optional built-UI static serving with safe SPA fallback, API 404 behavior, path containment and missing-index validation; implemented CLI parsing, offline workflow validation, migrate, serve, MCP, token creation, police listing and search reindex; added static serving, CLI, and UI fixture leak tests.
**Tasks In Progress**: Full composition of issue ingestion, collectors, workflow, evidence and Police services; worker and collector CLI commands; remediation/sweep/ingestion/chat E2E and full sink leak matrix.
**Blockers**: None external. Implementation remains incomplete because assigned service wiring and E2E/leak criteria are not yet delivered.
**Reconciliation requests**: None. The remaining work is within p28 writePaths.
**Hashes**: `src/telescreen/cli.py` before=48199c7f4e506f05a0f92bade49596086f5a7cc498f44492402792db385eeb05 after=a48aeae1e9dc689de745744e13493daf02a0cff50320de5fad1b6f21c1353b4d; `src/telescreen/wiring.py` before=absent after=d52b0d853617b8a8b98108ff3503560c3c52f175d62aeae114a261c7e3aa03bb; `src/telescreen/wiring_builders.py` before=absent after=1ebcffb4e8de8b03a868b4389f67fde9e101830b915df70b3241b2b466d57096; `src/telescreen/api/app.py` before=absent after=f19472e4582a51b6c7683d38be2bbef0f320ffc99c1f0179d195c809c5e71407; `tests/test_package.py` before=23c60338dccf5285b920361cc619db517c0975bcfbe0bea206816da97cbb467f after=20b7b020da7b5f79d62f43292211dc97fd26719b62859f794ede510ab64e415e; `tests/e2e/test_serve_static.py` before=absent after=3edf4db626baa2baf781834757e8f2f25945f5dee7e0b4d64f955a84b2dc5620; `tests/leak/test_ui_fixtures.py` before=absent after=2bd5e05a79396651a8129873acd8378f6c3cdfd9fd442191a43e17f77ce7f5e5; plan before=ccc7201649f418877919f3a33519c1aab13fc6e17e923508b4304558d1adb8d3 after=recorded in `tmp/telescreen-v1-20261009-s341/p28-assembly/final-source-sha256.json`.
**Verification**: Final-source full repository format -> exit 0 (log `/tmp/telescreen-p28-assembly-format-final2.log`); ruff -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-final2.log`); typelint -> exit 0 (`/tmp/telescreen-p28-assembly-typelint-final2.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-ty-final2.log`); pytest -> exit 0, 363 passed/4 offline skips (`/tmp/telescreen-p28-assembly-pytest-final2.log`); wheel/sdist build -> exit 0 (`/tmp/telescreen-p28-assembly-build-final2.log`); 1000-line gate -> exit 0, no matches (`/tmp/telescreen-p28-assembly-line-limit-final2.log`); dependency diff -> exit 0 (`/tmp/telescreen-p28-assembly-dependencies-final2.log`); `git diff --check` -> exit 0.
**Notes**: The whole-repository Python gates pass on this shared source tree. The complete planned Container/router/worker assembly, all-command CLI behavior, remediation/sweep E2E and raw-value sink matrix remain outstanding; therefore Status stays In Progress and their completion criteria stay unchecked. The separate focused static/CLI/fixture run passed 8 tests before the final shutdown-lifecycle adjustment; final-source counts are covered by the full pytest run.

### Session: 2026-10-09 continuation 2
**Tasks Completed**: Moved `workflow validate` dispatch before runtime settings loading so the documented offline validator does not require unrelated service configuration. Extended the CLI test to prove validation succeeds with an invalid Codex-provider configuration and no database settings.
**Tasks In Progress**: Complete service/router composition and command execution for worker and collector commands; GitHub remediation, sweep, ingestion and chat E2E; full raw-value sink leak matrix.
**Blockers**: None external. The remaining work is assigned to p28 and stays within its writePaths.
**Verification**: CLI behavior `uv run --frozen pytest -p no:cacheprovider tests/test_package.py` -> exit 0, 5 passed (`/tmp/telescreen-p28-assembly-cli-attempt2.log`); full `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 363 passed/4 expected offline skips (`/tmp/telescreen-p28-assembly-pytest-attempt2.log`); format, ruff, typelint, ty and `uv build --out-dir /tmp/telescreen-p28-assembly-dist-attempt2` -> exit 0 (`/tmp/telescreen-p28-assembly-{format,ruff,typelint,ty,build}-attempt2.log`); 1000-line, dependency and diff checks -> exit 0. Prior continuation-1 full-gate results are not reused as final-source verification after this edit.
**Hashes**: Continuation-2 `src/telescreen/cli.py` SHA-256 `657b4778179518163f4bb9c1368235fdf58b34637a09a6a68aa109501372c94e`; `tests/test_package.py` SHA-256 `8a01540627d6bf93e0137bc458f56e5d67f5e106d380e3e942b950a23a949ab2`; source and plan hash inventory: `tmp/telescreen-v1-20261009-s341/p28-assembly/final-source-sha256.json`.
**Review Decisions**: None for p28; test-integrity, adversarial and integration reviews remain downstream.

### Session: 2026-10-09 integration repair attempt 3
**Tasks Completed**: Expanded `Container.routers` to include all 15 required routers; assembled issue ingestion, workflow/run services, evidence, escalation, police, map-reduce, worker/scheduler handlers, and connected deferred issue events. Added `tests/e2e/test_api_routes.py`, which authenticates as admin and verifies 200 responses from issues, workflows, runs, police workers, collectors, escalation policies and map jobs. Replaced the unavailable worker and collector CLI paths with worker run-once/continuous, collector run/watch and sweep dispatch. Existing token and police commands use their assembled services.
**Tasks In Progress**: Register the built-in GitHub, Slack and Google adapters/sweepers in `CollectorRegistry`; the runtime collector run/watch and sweep commands depend on those registrations. Implement the planned GitHub remediation, collector sweep, push-ingestion and chat end-to-end flows, plus the design-masking sink matrix across database text columns, logs, search documents, outbound bodies, evidence files, API responses and forced tracebacks.
**Blockers**: None external. The remaining work is assigned to p28 and remains within its writePaths.
**Verification**: Focused static/API/CLI/UI-fixture tests -> exit 0, 9 passed (`/tmp/telescreen-p28-assembly-focused-attempt3.log`); final-source full pytest -> exit 0, 364 passed and 4 expected offline integration skips (`/tmp/telescreen-p28-assembly-pytest-attempt3-final.log`); repository format -> exit 0 (`/tmp/telescreen-p28-assembly-format-attempt3-final.log`); ruff -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-attempt3-final.log`); typelint -> exit 0, 275 files clean (`/tmp/telescreen-p28-assembly-typelint-attempt3-final.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-ty-attempt3-final.log`); package build -> exit 0 (`/tmp/telescreen-p28-assembly-build-attempt3-final.log`); Python file line-limit (`/tmp/telescreen-p28-assembly-line-limit-attempt3-final.log`), dependency (`/tmp/telescreen-p28-assembly-dependencies-attempt3-final.log`), and whitespace (`/tmp/telescreen-p28-assembly-diff-check-attempt3-final.log`) checks -> exit 0.
**Hashes**: Current `src/telescreen/wiring.py` `ed9bcad5c8bc659a874780f773e138b2e87e7a889f2d9598d36314d8829ef9fa`; `src/telescreen/cli.py` `852a9e5bac0f5cf280ef87454710a35dd484ef46e1424a739e8ce685528bfc02`; `tests/e2e/test_api_routes.py` `245283ddb6e325cd861e58dcef0d0b8efef5c197d8cf649dc2c488e642dc90f1`. Additional modified source and test hashes are in the attempt 3 evidence inventory.
**Notes**: Container wiring and the required list routes are now covered. The adapter registry and required behavior-level E2E and leak-matrix tests remain incomplete. Do not check the remaining completion criteria or mark the plan complete.

**Owner note (2026-10-09)**: Remaining scope is real work (register built-in Slack, Google
and GitHub adapters and sweepers in the container; worker and collector commands; GitHub
remediation, sweep, ingestion and chat E2E; full raw-value leak matrix). Continue it. When
reporting, list only genuinely open items in `risks`/`findings`; fixed review findings go
only in `addressedFeedback` (the gate ignores status; conventions 7a).

**Plan revision (2026-10-09, run B2 s342)**: Split per architecture.md section 8 Run B2.
This plan keeps the container, collector bindings, CLI and serve. The E2E flows moved to
`impl-plans/active/p28b-e2e-flows.md` and the leak matrix (including
`tests/leak/test_ui_fixtures.py`) to `impl-plans/active/p28c-leak-matrix.md`.

### Session: 2026-10-09 run B2 implementation
**Tasks Completed**: Added the once-resolved fingerprint key to runtime primitives without exposing it in repr; implemented `wiring_collectors.py` with a shared HMAC-derived Drive channel key, fail-closed webhook secrets, lazy per-config Slack/GitHub credentials, shared Google auth, host normalization, and Slack/Google/GitHub adapter plus sweeper registration; exposed `Container.collector_registry`; added webhook, registry, key-sharing, lazy-credential, host, worker and collector-sweep CLI tests.
**Tasks In Progress**: None within p28-assembly. GitHub remediation, sweep, ingestion and chat E2E flows and the complete sink leak matrix are assigned to downstream p28b and p28c.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/wiring.py` before=`ed9bcad5c8bc659a874780f773e138b2e87e7a889f2d9598d36314d8829ef9fa`; `src/telescreen/wiring_builders.py` before=`1ebcffb4e8de8b03a868b4389f67fde9e101830b915df70b3241b2b466d57096`; `src/telescreen/wiring_collectors.py` before=absent; `tests/e2e/test_wiring_collectors.py` before=absent; `tests/e2e/test_wiring_cli.py` before=absent; this plan's before hash and all final hashes are recorded in `tmp/telescreen-v1-20261009-s342/p28-assembly/final-source-sha256.txt`.
**Verification**: `uv run --frozen pytest -p no:cacheprovider tests/e2e/test_wiring_collectors.py tests/e2e/test_wiring_cli.py tests/e2e/test_api_routes.py tests/e2e/test_serve_static.py tests/test_package.py` -> exit 0, 16 passed, 0 skipped (`/tmp/telescreen-p28-assembly-focused-final.log`); `uv run --frozen ruff format --check --no-cache .` -> exit 0 (`/tmp/telescreen-p28-assembly-format-final.log`); `uv run --frozen ruff check --no-cache .` -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-final.log`); `uv run --frozen python tools/typelint.py` -> exit 0, 278 files clean (`/tmp/telescreen-p28-assembly-typelint-final.log`); `uv run --frozen ty check` -> exit 0 (`/tmp/telescreen-p28-assembly-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 372 passed/4 offline integration skips (`/tmp/telescreen-p28-assembly-pytest-final.log`); `uv build --out-dir /tmp/telescreen-p28-assembly-dist` -> exit 0, wheel and sdist built (`/tmp/telescreen-p28-assembly-build-final.log`); 1000-line check, no dependency changes, no `CollectorSecrets(None, None)` placeholder and `git diff --check` -> exit 0 (`/tmp/telescreen-p28-assembly-line-limit-final.log`, `/tmp/telescreen-p28-assembly-dependencies-final.log`, `/tmp/telescreen-p28-assembly-placeholder-final.log`, `/tmp/telescreen-p28-assembly-diff-check-final.log`).
**Notes**: The final implementation follows the accepted assembly design. `ty` initially identified the frozen GitHub adapter's writable protocol mismatch and strict tests identified unannotated bindings; the composition boundary uses an explicit typed cast and all test bindings are typed. Earlier local format, import-order and type-lint failures were corrected before final passing gates. Unrelated pending user QA files and parallel p13 UI page edits were preserved.

### Session: 2026-10-10 run B2 final CLI assertion
**Tasks Completed**: Added an assertion that a repeated active collector sweep returns exit 1 and prints the static `collector sweep already active` conflict detail to stderr.
**Tasks In Progress**: None within p28-assembly.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/e2e/test_wiring_cli.py` before=`48b50abc31f1958e8b26fec477d844480bc2ccb176b5975f7f7711c4b5a7408d`; final hashes for all plan-owned source/tests and this plan are in `tmp/telescreen-v1-20261009-s342/p28-assembly/final-source-sha256.txt`.
**Verification**: Focused assembly tests -> exit 0, 16 passed/0 skipped (`/tmp/telescreen-p28-assembly-focused-final2.log`); full pytest -> exit 0, 372 passed/4 expected offline integration skips (`/tmp/telescreen-p28-assembly-pytest-final2.log`); format -> exit 0 (`/tmp/telescreen-p28-assembly-format-final2.log`); Ruff -> exit 0 (`/tmp/telescreen-p28-assembly-ruff-final2.log`); typelint -> exit 0, 278 files clean (`/tmp/telescreen-p28-assembly-typelint-final2.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-ty-final2.log`); `uv build --out-dir /tmp/telescreen-p28-assembly-dist-final2` -> exit 0 (`/tmp/telescreen-p28-assembly-build-final2.log`).
**Notes**: This follow-up replaces the earlier focused and full-suite evidence with runs against the final test source; p28-assembly remains complete. Downstream p28b/p28c own the flow E2E suite and comprehensive sink leak matrix.

### Session: 2026-10-10 reconciliation of RECON-B2-1-P28-FAILED
**Tasks Completed**: Re-read `tests/e2e/test_wiring_collectors.py` and retained the P28A-TI-01 regression coverage for all five adapter/sweeper registrations, deterministic Drive-key derivation and sharing, GitHub/Slack webhook signature outcomes, unresolved webhook refs, safe missing GitHub credentials, and GitHub host mapping. Verified that injecting a mismatched Drive verification key makes the real-container webhook test fail at its expected 200 assertion.
**Tasks In Progress**: None within p28-assembly. Step 7 must independently rerun test-integrity and adversarial review on this final source before downstream waves are released.
**Blockers**: None.
**Review Decision**: `RECON-B2-1-P28-FAILED` implementation criteria are met; formal review remains downstream and is not claimed here.
**Verification**: Focused assembly suite -> exit 0, 16 passed/0 skipped (`/tmp/telescreen-p28-assembly-reconcile-focused.log`); deliberate Drive-key mutation check -> expected exit 1 at the webhook status assertion, 1 failed (`/tmp/telescreen-p28-assembly-reconcile-mutation.log`); Ruff format -> exit 0 (`/tmp/telescreen-p28-assembly-reconcile-format.log`); Ruff -> exit 0 (`/tmp/telescreen-p28-assembly-reconcile-ruff.log`); typelint -> exit 0, 278 files clean (`/tmp/telescreen-p28-assembly-reconcile-typelint.log`); ty -> exit 0 (`/tmp/telescreen-p28-assembly-reconcile-ty.log`); full pytest -> exit 0, 372 passed/4 expected offline skips (`/tmp/telescreen-p28-assembly-reconcile-pytest.log`); package build -> exit 0 (`/tmp/telescreen-p28-assembly-reconcile-build.log`); Python line limit, dependency diff and placeholder checks -> exit 0 (`/tmp/telescreen-p28-assembly-reconcile-line-limit.log`, command `git diff --quiet -- pyproject.toml uv.lock`, and command `! grep -n 'CollectorSecrets(None, None)' src/telescreen/wiring.py`).
**Hashes**: Reconciliation inventory is `tmp/telescreen-v1-20261009-s342/p28-assembly/final-source-sha256-reconcile-b2-1.txt`; this is a new file so the earlier failed inventory remains preserved.

**Owner resolution (2026-10-10)**: The reconciled p28 source passes all gates (372 tests,
ruff, typelint, ty, build). The previous dispatch was blocked ONLY because the step result
listed (a) the expected-failure Drive key-mismatch mutation run and (b) the superseded
hash-inventory check in `verification` / `priorVerification`. The gate treats any non-zero
exit there as a failed test. In your next step result: list ONLY final passing commands in
`verification`; leave `priorVerification` empty or with passing entries only; record mutation
evidence and superseded attempts in this Progress Log only (conventions 7a).

### Session: 2026-10-10 run B3 re-verification
**Tasks Completed**: Re-ran the assigned Verification block against the unchanged current source. Focused assembly tests passed; repository formatting, lint, typelint, type checking, full offline pytest, package build, Python file line limit, dependency diff, collector-secret placeholder, and completion-criteria checks all passed. No source or test files changed.
**Tasks In Progress**: None within p28-assembly. Review gates and downstream p28b/p28c/p29 remain owned by later workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/p28-assembly.md` before=`bec1e564156f81bb27218a356c1ab3eee384d4e19de43e792a7ab12e37b4bcba`; before/after SHA-256 for every writePath is recorded in `tmp/telescreen-v1-20261010-s345/p28-assembly/writepath-sha256-before.txt` and `tmp/telescreen-v1-20261010-s345/p28-assembly/writepath-sha256-after.txt`.
**Verification**: `uv run --frozen pytest -p no:cacheprovider tests/e2e/test_wiring_collectors.py tests/e2e/test_wiring_cli.py tests/e2e/test_api_routes.py tests/e2e/test_serve_static.py tests/test_package.py` -> exit 0, 16 passed/0 skipped (`/tmp/telescreen-p28-assembly-focused.log`); `uv run --frozen ruff format --check --no-cache .` -> exit 0 (`/tmp/telescreen-p28-assembly-format.log`); `uv run --frozen ruff check --no-cache .` -> exit 0 (`/tmp/telescreen-p28-assembly-ruff.log`); `uv run --frozen python tools/typelint.py` -> exit 0, 278 files clean (`/tmp/telescreen-p28-assembly-typelint.log`); `uv run --frozen ty check` -> exit 0 (`/tmp/telescreen-p28-assembly-ty.log`); `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 372 passed/4 expected offline integration skips (`/tmp/telescreen-p28-assembly-pytest.log`); `uv build --out-dir /tmp/telescreen-p28-assembly-dist` -> exit 0 (`/tmp/telescreen-p28-assembly-build.log`); `test -z "$(find src tests -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"` -> exit 0 (`/tmp/telescreen-p28-assembly-line-limit.log`); `git diff --quiet -- pyproject.toml uv.lock` -> exit 0 (`/tmp/telescreen-p28-assembly-dependencies.log`); `! grep -n 'CollectorSecrets(None, None)' src/telescreen/wiring.py` -> exit 0 (`/tmp/telescreen-p28-assembly-placeholder.log`); `test "$(grep -c '^- \[ \]' impl-plans/active/p28-assembly.md)" -eq 0` -> exit 0 (`/tmp/telescreen-p28-assembly-completion.log`).
**Notes**: The first attempted 1000-line guard had incorrect shell quoting and printed an awk syntax error; it is superseded by the corrected command above, which passed. The failed invocation is not reported as verification. Current source remains unchanged from the pre-run SHA-256 inventory. Earlier mutation evidence remains in the preceding session only.
