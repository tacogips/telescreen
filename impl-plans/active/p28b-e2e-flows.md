# P28b End-to-End Flows (GitHub Remediation, Sweep, Push Ingestion, Chat) Implementation Plan

**Status**: In Progress
**Design Reference**: design-docs/specs/architecture.md section 8 (Run B2) and 9, design-docs/specs/design-collectors-and-ingestion.md (Per-kind behavior, Push ingestion API, Full sweeps, Assembly bindings), design-docs/specs/design-workflow-engine.md, design-docs/specs/design-map-reduce-sweeps.md, design-docs/specs/design-police-contract.md
**Created**: 2026-10-09 (run B2, session 342; split out of p28-assembly)
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `p28b-e2e-flows`
- planPath: `impl-plans/active/p28b-e2e-flows.md`
- wave: B3-2 in the s345 manifest (B2-2 in s342; parallel with `p28c-leak-matrix`; no shared files)
- dependsOn: `p28-assembly` (collector registry and webhook secrets bound), plus the 29
  accepted plans at cdf195d
- writePaths:
  - `impl-plans/active/p28b-e2e-flows.md`
  - `tests/e2e/harness.py`
  - `tests/e2e/test_flow_github_remediation.py`
  - `tests/e2e/test_flow_sweep.py`
  - `tests/e2e/test_flow_ingest.py`
  - `tests/e2e/test_flow_chat.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 3, 6, 7, 7a, 8).

## Intent and Context

The issue requires end-to-end proof, through the real assembled app, of four flows:

- the GitHub credential-leak multi-team remediation, with escalation and Police
  re-verification;
- a concurrent map-reduce sweep with removal reconciliation;
- push ingestion;
- the Assistant chat.

The flows run through `build_container` + `create_app` + `ASGITransport`, and every
external system is an `httpx.MockTransport`. This plan only adds tests. Product
behavior is accepted, and a defect found here goes to the owning plan as a
Reconciliation request.

Reference code to imitate:

- `tests/e2e/test_api_routes.py` (container, app, admin token, `_TestEnv`);
- `tests/support/db.py:make_sqlite_harness`;
- `tests/support/samples.py` (`aws_access_key_id`, `aws_secret_access_key`, `find_leaks`);
- `tests/collectors/github/test_github.py` (GitHub API JSON shapes: compare, trees,
  contents/blobs, webhook push payload and signature);
- `tests/detection/llm/test_llm.py:test_claude_client_extracts_text_and_sends_base64_image`
  (Anthropic messages response shape: `content: [{"type": "text", "text": "<json>"}]`);
- `tests/police_sdk/test_sdk.py` and `src/telescreen_police_sdk/worker.py`
  (`PullWorker`, `BureauClient`);
- `src/telescreen/assistant/chat.py` (JSON tool-loop steps
  `{"action":"tool","tool":"count_incidents","args":{...}}` and
  `{"action":"answer","answer":"..."}`).

## Non-goals

- No edits to `src/**`, `tests/support/**`, `tests/leak/**`, other `tests/e2e/*` files or
  examples. `examples/workflows/github-credential-leak.yaml` is read-only input.
- No real network, no SMTP: no flow may trigger an email delivery (see Pitfalls).
- No new dependency and no pytest plugin.

## Deliverables

### `tests/e2e/harness.py` (target < 400 lines)

- `PairEnv` (frozen dataclass, implements `EnvSource`):
  - `values: tuple[tuple[str, str], ...]`; `get` scans the tuple.
  - Never use a dict (typelint).
- `OutboundRecorder` (dataclass):
  - `requests: list[RecordedRequest]` (method, host, path, body bytes;
    `field(repr=False)` for body);
  - `handlers: list[tuple[str, HostHandler]]`, where `HostHandler` is a Protocol
    `(request: httpx.Request) -> httpx.Response`;
  - `transport() -> httpx.MockTransport`. It records every request, then dispatches by
    host. An unknown host -> 599 response plus a recorded failure that tests assert is
    absent.
- `E2EWorld` (dataclass):
  - `database: SqliteHarness`, `clock: FakeClock`, `container: Container`,
    `client: httpx.AsyncClient` (base_url `http://test`, ASGITransport over `create_app`),
    `outbound: OutboundRecorder`, `admin_token: str = field(repr=False)`, `tmp: Path`;
  - `async def drain(self, rounds: int = 50, step: timedelta = timedelta(seconds=5)) -> None`:
    alternates `container.worker.run_until_idle()` and `clock.advance(step)` until a
    round processes 0 tasks twice in a row; fails the test if `rounds` is exhausted;
  - `async def close(self) -> None`.
- `async def make_world(tmp: Path, extra_env: tuple[tuple[str, str], ...], outbound: OutboundRecorder) -> E2EWorld`:
  - FakeClock at `2026-10-01T00:00:00Z`;
  - settings from `load_settings(PairEnv(...))` with `TELESCREEN_DATABASE_URL=sqlite+aiosqlite:///:memory:`,
    `TELESCREEN_FINGERPRINT_KEY=env:E2E_FP` (the value is built at runtime, >= 32 bytes),
    `TELESCREEN_EVIDENCE_DIR=<tmp>/evidence`, and `TELESCREEN_GITHUB_API_URL=https://github.example.invalid/api/v3`;
  - the sessions come from `make_sqlite_harness()`;
  - the http client is `httpx.AsyncClient(transport=outbound.transport())`;
  - it creates an admin token with `TokenService` like `test_api_routes.py`.
- Small helpers:
  - `bearer(token)`;
  - `github_signature(secret: str, body: bytes) -> str`;
  - `async def admin_post(world, path, payload_json_text) -> httpx.Response`. Send JSON
    as `content=` text plus a content-type header so no dict variable is bound, or pass
    `json=json.loads(text)` inline.
- All secrets and tokens are built at runtime (for example `"e2e-" + "x" * 40`) or taken
  from `tests.support.samples`. Never write a literal token or key pattern.

### `test_flow_github_remediation.py`

Setup through the API with the admin token. Read each router first; adjust field names to
the real bodies.

1. Directory:
   - teams `secops-triage`, `secops-tier2`, `dev-remediation`, `dev-remediation-leads`
     and `github-enterprise-admins`, each with a `slack_channel` and NO email;
   - the members needed by the human-task routes. If completing a task requires the
     caller to be a member of the assignee team, bind the token's actor accordingly per
     `src/telescreen/api/routers/human_tasks.py` and `directory.py`.
2. Escalation policies (`POST /api/escalation-policies`):
   - TEST-LOCAL slack-only versions of `secops-standard` and `dev-remediation`, with
     the same names and tier timings as `examples/workflows/escalation-policies.json`
     but `channels: ["slack"]` and team recipients only;
   - reason: hermetic tests, no SMTP.
3. Workflow:
   - `POST /api/workflows` with the exact text of
     `examples/workflows/github-credential-leak.yaml`, then activate it.
4. Collector:
   - an enabled github config `e2e-github` (`trigger_mode` event, selector owner `acme`,
     repo `app`, branch `main`);
   - `credential_ref=env:E2E_GH_TOKEN`;
   - env `TELESCREEN_GITHUB_WEBHOOK_SECRET=env:E2E_GH_HOOK`.
5. Police:
   - env `TELESCREEN_POLICE_ENROLLMENT_TOKEN=env:E2E_ENROLL`;
   - register an SDK `PullWorker` (pull mode, task type `verify_remediation`, label
     `scope=github-enterprise`) whose `BureauClient` uses
     `httpx.AsyncClient(transport=ASGITransport(app))`;
   - drive it one claim/result at a time (see Pitfalls); its handler returns `failed`
     the first time and `passed` the second.
6. Mock hosts:
   - `github.example.invalid`: compare and contents/blob for `config/settings.py`,
     whose text contains the AWS sample pair;
   - `api.anthropic.com`: returns `{"is_live_credential": true, "summary": "masked summary"}`
     as text content;
   - `slack.com`: `chat.postMessage` -> `{"ok": true}`.
   - Set `TELESCREEN_ANTHROPIC_API_KEY=env:E2E_ANTHROPIC` and
     `TELESCREEN_SLACK_BOT_TOKEN=env:E2E_SLACK`.

Flow and assertions:

- A signed `push` webhook to `POST /api/collectors/github/webhook` returns 200. Then
  `drain()`. The issue list (`GET /api/issues?source=github`) has exactly one issue. Its
  short id is the first issue number (`TELE-1` when the serializer exposes it). A run of
  `github-credential-leak` exists for it (`GET /api/runs?issue_id=`).
- The run reaches the `first_line_triage` human task. Steps `rescan`,
  `verify_credential` and `live_check` succeeded.
- Advance the clock by PT4H plus 1 minute, then `drain()`:
  - the open triage task is escalated to `team:secops-tier2` (tier 1);
  - at least one recorded `slack.com` request exists;
  - its body has `find_leaks(body) == ()` and is masked.
- Complete triage with `remediation_required`:
  - the notify step produces a Slack delivery;
  - the `dev_remediation` evidence task opens;
  - completing it before any accepted upload -> 409 `evidence_required`.
- `PUT /api/evidence?issue_id=..&filename=proof.txt` with a body containing a sample ->
  rejected:
  - the status is the router's rejection status (422);
  - the response has rule classes only, and `find_leaks(response.text) == ()`;
  - no file exists under `<tmp>/evidence`.
- Clean evidence text -> accepted. Complete the task -> the `github_admin_review` task
  opens. Complete it with `verified` -> a `police_recheck` task is claimable.
- The worker returns `failed` -> the run records a `send_back` transition to
  `dev_remediation` (`GET /api/runs/{id}` transitions).
- Repeat clean evidence, completion and admin `verified`. The worker returns `passed`
  -> the run status is `succeeded` and the end step is `completed`.
- Final checks:
  - `GET /api/issues/{id}/history` has entries;
  - `GET /api/audit/verify` reports the chain valid;
  - no recorded outbound request targets an unknown host;
  - `find_leaks` is empty for every recorded outbound body and every API response text
    read in this test.

### `test_flow_sweep.py`

Setup:

- A github collector `e2e-sweep` with selector repos `a`, `b` and `c` (branch `main`).
- First, an incremental or schedule scan (`POST /api/collectors/{id}/run`, then `drain()`)
  sees `c` containing `old/secret.txt` with a sample. The result is one open issue on that
  resource.

Scenario 1 (success):

- The mock now lists `c` without `old/secret.txt`; `a` and `b` contain clean files.
- `POST /api/collectors/{id}/sweep` -> `{map_job_id}`. Then `drain()`.
- `GET /api/map-jobs/{id}` is `succeeded`, with shard counts covering 3 repos.
- The open issue on `old/secret.txt` is `resolved` (auto-resolve by reconciliation).

Scenario 2 (failure; a fresh world):

- Same seed, but one blob fetch in repo `b` returns 500.
- The map job ends `partial` (`MapJobStatus.PARTIAL`; design-map-reduce-sweeps.md: any
  `resources_failed > 0` makes the sweep partial).
- The `old/secret.txt` issue stays open: no reconciliation on a partial sweep.

Also:

- A second `POST .../sweep` while the first is active -> 409 `conflict`.

### `test_flow_ingest.py`

- Create a `custom` collector `e2e-crawler` and an API token with scope `ingest`.
- `POST /api/ingest/v1/scan` with
  `{source: "custom:e2e-crawler", resources: [{locator, complete: true, segments: [{locator, text: <sample sentence>}]}]}`
  -> 200:
  - `findings >= 1` and `issue_ids` is non-empty;
  - `GET /api/issues/{id}` shows a masked snippet;
  - `find_leaks` is empty for both bodies.
- `POST /api/ingest/v1/findings` whose `masked_snippet` holds a raw sample -> 422
  `raw_value_detected`; `find_leaks(response.text) == ()`.
- Unknown source `custom:nope` -> 404 `unknown_source`.
- `GET /api/ingest/v1/sources/e2e-crawler/config` -> the selector.

### `test_flow_chat.py`

- Seed two issues through ingest.
- The Anthropic mock answers in sequence:
  1. `{"action":"tool","tool":"count_incidents","args":{...}}` with args valid for
     `CountIncidentsArgs`;
  2. `{"action":"answer","answer":"There are 2 incidents."}`.
- `POST /api/chat` `{messages: [{role: "user", content: "How many incidents this month?"}]}`
  -> 200 with that answer.
- The second recorded Anthropic request body contains the tool result with count 2. This
  proves the tool ran against the DB.
- A user message containing a sample is never forwarded raw:
  `find_leaks(<every anthropic request body>) == ()`.

## Key Pitfalls

- Email: escalation tiers or notify steps with the `email` channel would open an SMTP
  connection. Use the slack-only test policies, and create teams without email. The
  workflow's `escalation_notice` step (email) is reached only after exhausted
  send-backs, which this flow never does. If an email delivery is attempted anyway,
  stop and write a Reconciliation request against `p28-assembly` (email transport seam).
  Do not patch product code.
- Do not run `PullWorker.run()` forever. Use the SDK's single-cycle API if it has one;
  otherwise run `run(stop)` as a task and set `stop` once the result is observed, with a
  timeout of 10 s. Keep the Bureau side progressing with `world.drain()`.
- FakeClock drives SLA, timers and map dispatch. Never `asyncio.sleep` for durations.
  Advance the clock instead.
- Signatures must cover the exact body bytes sent. Build the body once as bytes.
- Never bind a mapping to a variable (typelint). Build JSON with `json.dumps(...)`
  inline, or with dataclasses plus `dataclasses.asdict` inline.
- Import helpers as `from tests.e2e.harness import ...` (namespace package; there is no
  `__init__.py` to add).
- Each test file must stay < 1000 lines (target < 400). Split helpers into `harness.py`.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen pytest -p no:cacheprovider tests/e2e 2>&1 | tee /tmp/telescreen-p28b-e2e-flows-pytest.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tests/e2e 2>&1 | tee /tmp/telescreen-p28b-e2e-flows-format.log; echo "exit=$?"
uv run --frozen ruff check --no-cache tests/e2e 2>&1 | tee /tmp/telescreen-p28b-e2e-flows-ruff.log; echo "exit=$?"
uv run --frozen ty check tests/e2e 2>&1 | tee /tmp/telescreen-p28b-e2e-flows-ty.log; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-p28b-e2e-flows-typelint.log 2>&1; ! grep -E '^tests/e2e/(harness|test_flow_)' /tmp/telescreen-p28b-e2e-flows-typelint.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p28b-e2e-flows-pytest-all.log; echo "exit=$?"
test -z "$(find tests/e2e -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"; echo "exit=$?"
! grep -rnE 'AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[abp]-' tests/e2e; echo "exit=$?"
test "$(grep -c '^- \[ \]' impl-plans/active/p28b-e2e-flows.md)" -eq 0; echo "exit=$?"
```

Evidence required:

- every command exits 0;
- the `tests/e2e` run shows the four new test files passed (at least 6 tests: 1
  remediation, 2 sweep scenarios + conflict, ingest, chat) with 0 failed and 0 skipped;
- the full pytest run shows 0 failed.

## Completion Criteria

- [x] `tests/e2e/harness.py` delivered (PairEnv, OutboundRecorder, E2EWorld, make_world, drain)
- [ ] GitHub remediation flow passes end to end (escalation, masked Slack, evidence reject/accept, send-back, police pass, run succeeded, audit valid)
- [x] Sweep flow passes (succeeded with auto-resolve; partial without reconciliation; 409 conflict)
- [x] Push ingestion flow passes (scan -> issue; raw findings -> 422; unknown source -> 404)
- [x] Chat flow passes (tool executed against the DB; no raw value forwarded)
- [ ] Verification passes on the final source (logs recorded); Reconciliation requests (if any) listed
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-09 00:00
**Tasks Completed**: Plan created (run B2 split of p28-assembly)
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-10 Step 6 implementation
**Tasks Completed**: Added the shared SQLite/ASGI E2E harness and four flow test files. Sweep success verifies one completed root partition and three succeeded repository shards, automatic issue resolution and HTTP 409 on a concurrent start; the partial sweep preserves the unresolved issue. Push ingestion verifies masked issue output, rejection of raw pushed findings, and unknown-source handling. Chat verifies the DB-backed count tool and masked provider traffic.
**Tasks In Progress**: GitHub remediation reaches the `first_line_triage` step but does not create its human task; final behavioral verification remains incomplete.
**Blockers**: No dependency admission blocker. One required flow is unresolved pending the p24-owned workflow-engine repair described below.
**Reconciliation requests**: `p24-workflow-engine` (accepted dependency; files outside this plan's writePaths): the GitHub remediation E2E produces `first_line_triage` with status `ready`, but its `advance_step` queue task retries three times and remains ready at `2026-10-01T00:01:10`; `/api/human-tasks?status=open` returns an empty list. With pytest log capture enabled, `EngineWorker` reports three `advance_step` failures. See `/tmp/telescreen-p28b-remediation-errors.log` and `/tmp/telescreen-p28b-e2e-flows-pytest.log`. Repair and resume criterion: fix the p24-owned advance/human-task execution failure, then rerun the full p28b Verification block and confirm the task opens, SLA escalation, evidence reject/accept, police send-back/pass, successful run, and valid audit chain.
**Verification**: Final E2E suite -> exit 1, 16 passed, 1 failed (remediation only), log `/tmp/telescreen-p28b-e2e-flows-pytest-final.log`; final full suite -> exit 1, 381 passed, 4 expected offline integration skips, 1 failed (same remediation test), log `/tmp/telescreen-p28b-e2e-flows-pytest-all-final.log`. Format check -> exit 0, 9 files formatted, `/tmp/telescreen-p28b-e2e-flows-format-final.log`; Ruff -> exit 0, `/tmp/telescreen-p28b-e2e-flows-ruff-final.log`; ty -> exit 0, `/tmp/telescreen-p28b-e2e-flows-ty-final.log`; filtered typelint -> exit 0 with no p28b diagnostics, `/tmp/telescreen-p28b-e2e-flows-typelint-final.log`; Python line-limit and raw-token-pattern guards -> exit 0. Do not treat either failed aggregate as a passing gate.
**Hashes**: The `before:step6-implement` immutable snapshot records the five test/support source paths as missing; it records the plan SHA-256 as `ff5a0f83d2a3ca416f11af74e40a6f9dc8ceb9be70c948623b3ac33606637bf8`. Final source SHA-256: `tests/e2e/harness.py` `63b1c0fb64f9d69961301e02707c96d051c85f3ccfd4201b39cd6285f023b0b7`; `tests/e2e/test_flow_github_remediation.py` `5ffaf3dc1c4b0232e657cae24dde22b6eda639e5bb81042a39c35eee65da3dbb`; `tests/e2e/test_flow_sweep.py` `5a651597b142f54b08881083f49f970821aa17bdc4af9539e4726d6e57474217`; `tests/e2e/test_flow_ingest.py` `7a37df5985ae264c93bbf4f106c9813bf209ed1afad48e008591f1b6724caa73`; `tests/e2e/test_flow_chat.py` `6518067165b01a280de24b0b9cf3cfa4ba252099f5fcb7fdb96e663527321832`. Plan's final hash is captured in the Step 6 result.
**Notes**: Dependency readiness used the runtime-owned acceptedPlanIds list, which includes `p28-assembly`. Changed test files are only the declared p28b write paths. The four newly added flow files contribute 7 tests; 6 pass and the remediation test remains unresolved. No source changes were made outside the assigned test paths. Workflow mode `issue-resolution`; issue reference: `workflowInput` (no GitHub issue), Telescreen v1 final run B3, `opus-luna-design-and-implement-review-loop-session-345`; dispatch `comm-005811`, intake `comm-005812`, design accepted `comm-005814`, plans accepted `comm-005816`. Read-only codex-agent reference: `/root/p28b_api_survey`.
**Prior Attempts**: The sweep mock path matcher and response assertions were corrected; an intermediate run with the serialized SQLite worker reached success but exposed the plan's mistaken expectation of three root partitions instead of one root plus three shard items. The final E2E run passes all three sweep tests. The first format/Ruff check found one overlong sweep diagnostic line; it was reflowed and both final gates pass. Intermediate logs: `/tmp/telescreen-p28b-sweep-diag.log`, `/tmp/telescreen-p28b-sweep-concurrency-01.log`, `/tmp/telescreen-p28b-sweep-concurrency-02.log`. These are superseded by the final-source verification above; the remediation failure remains current.
