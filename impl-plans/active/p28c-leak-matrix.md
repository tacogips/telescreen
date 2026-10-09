# P28c Raw-Value Leak Matrix Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-masking.md (Leak test matrix, including the run B2 paragraph on the traces sink and the harness seams), design-docs/specs/architecture.md sections 7, 8 (Run B2), 9, design-docs/specs/design-ui-sentry.md 8.3 (fixture leak check)
**Created**: 2026-10-09 (run B2, session 342; split out of p28-assembly)
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p28c-leak-matrix`
- planPath: `impl-plans/active/p28c-leak-matrix.md`
- wave: B2-2 (parallel with `p28b-e2e-flows`; no shared files)
- dependsOn: `p28-assembly` (collector registry and webhook secrets bound), plus the 28
  accepted plans at d6cffb3
- writePaths:
  - `impl-plans/active/p28c-leak-matrix.md`
  - `tests/leak` (directory; includes the existing `tests/leak/test_ui_fixtures.py`)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 3, 6, 7, 7a, 8).

## Intent and Context

The core guarantee of the issue: raw detected sensitive values are never persisted,
logged, indexed, notified, returned, traced or shown in exceptions. This plan proves it
with one matrix test. It drives every flow through the real `build_container` +
`create_app`, then scans every sink for every sample secret (and its separator-stripped
variant) with `tests.support.samples.find_leaks`.

Per design-masking.md (run B2 paragraph), the only seams are:

- SQLite sessions (`tests.support.db.make_sqlite_harness`);
- the in-memory search backend (Meilisearch unset);
- an `httpx.MockTransport` for all outbound HTTP, including canned Anthropic responses;
- `FakeClock`.

Do not stub any service. The image-scanner cases are expressed through the HTTP seam:

- "scanner unavailable" = a second world built without `TELESCREEN_ANTHROPIC_API_KEY`;
- "free-text output" = the Anthropic mock returns non-JSON text.

`tests/leak/test_ui_fixtures.py` already exists and passes (the 8.3 fixture check). Keep
it unchanged unless it breaks.

Reference code to imitate:

- `tests/e2e/test_api_routes.py` (container/app/token);
- `tests/support/samples.py` (`all_samples`, `find_leaks`, `separator_stripped`);
- `tests/evidence/**` and `src/telescreen/evidence/{service,image,strip}.py` (evidence
  API and image scanner JSON contract);
- `tests/police/**` and `src/telescreen/api/routers/police.py` (pull register, claim and
  result protocol);
- `src/telescreen/masking/log_masking.py:configure_logging` (it adopts the first root
  `StreamHandler` and sets `MaskingFormatter`) and `install_excepthook`;
- `src/telescreen/search/memory.py:InMemorySearchBackend.documents`.

## Non-goals

- No edits outside `tests/leak` and this plan. Do not edit `tests/e2e/**`, so do not
  import `tests/e2e/harness.py` either: it is written in parallel by p28b. Build a small
  own harness.
- No product code changes. A real leak found is a Reconciliation request naming the
  owning plan and file (for example `p17-notifications` for a notification body). It is
  not worked around in the test.
- No SMTP and no email-channel flows (no seam exists). Notifications are covered through
  Slack.

## Deliverables (each file < 400 lines)

- `tests/leak/harness.py`:
  - `PairEnv` (tuple-of-pairs `EnvSource`).
  - `Recorder`: outbound requests (bytes), API response texts and the canned Anthropic
    response queue (`list[str]`, consumed FIFO; empty queue -> 500 so missing setups
    fail loudly).
  - `LeakWorld`: harness, clock, container, client, recorder, `tmp`, `log_buffer: io.StringIO`
    and `drain()` (same semantics as p28b: alternate `run_until_idle` and
    `clock.advance`, bounded).
  - `make_leak_world(tmp, with_llm: bool) -> LeakWorld`. Env settings:
    - `TELESCREEN_EVIDENCE_DIR=<tmp>/evidence`;
    - `TELESCREEN_SLACK_BOT_TOKEN`, `TELESCREEN_POLICE_ENROLLMENT_TOKEN` and (if
      `with_llm`) `TELESCREEN_ANTHROPIC_API_KEY` as `env:` refs to runtime-built values;
    - `TELESCREEN_GITHUB_WEBHOOK_SECRET` for the collector scan flow.
  - The httpx client sends every API call through an `event_hooks` response hook. The hook
    `await response.aread()` and records `response.text`.
- `tests/leak/images.py`: build minimal valid PNG and JPEG bytes at runtime:
  - PNG: IHDR + `tEXt` chunk holding a sample + IDAT + IEND (CRCs via `zlib.crc32`);
  - JPEG: SOI + APP1 segment with an `Exif\0\0` header whose payload holds a sample, plus
    a COM segment, a minimal valid frame and EOI.
  - Only stdlib; no Pillow.
- `tests/leak/flows.py`: one async function per flow, each taking a `LeakWorld` (below).
- `tests/leak/sinks.py`: one collector function per sink (below), each returning
  `tuple[tuple[str, str], ...]` of (sink label, text).
- `tests/leak/test_leak_matrix.py`: the matrix test (below), plus small focused tests.

## Flows (all must run, in this order, in one world unless noted)

1. Push ingestion:
   - a `custom` collector and an ingest token;
   - `/api/ingest/v1/scan` with segments that contain EVERY sample from `all_samples()`
     (one segment each);
   - `/api/ingest/v1/findings` with a raw sample -> 422.
2. Collector scan:
   - a github collector with `credential_ref`;
   - a signed push webhook;
   - the GitHub mock serves a file containing the AWS pair, the PEM key and the JWT;
   - `drain()`.
3. Issue listing and detail: `GET /api/issues`, `GET /api/issues/{id}`, `/events` and
   `/history` for every issue.
4. Search: `GET /api/search?q=` (the only search route) for a masked token and for a raw
   sample string. The query itself must not be echoed raw. `GET /api/audit?q=` likewise.
5. Workflow notify:
   - upload and activate a test-local YAML workflow: trigger `issue_created` for source
     `custom`, a `notify` step on channel slack to `team:leak-team` whose body references
     the issue title, then `end`;
   - the team has a `slack_channel` and no email;
   - ingest one more resource, then `drain()`;
   - at least one `slack.com` request is recorded.
6. Text evidence: one upload with a sample -> rejected; one clean upload -> accepted.
7. Image evidence (world with LLM):
   - the scanner reports `contains_sensitive` -> rejected and nothing stored;
   - the scanner reports clean for the PNG and the JPEG whose metadata holds samples ->
     accepted, and the stored bytes contain no sample and no `tEXt`/`Exif` payload;
   - the scanner returns free text -> rejected.
8. Image evidence (second world, `with_llm=False`): upload -> 503
   `evidence_scanner_unavailable` and nothing stored.
9. Police results:
   - register a pull police (enrollment token) and start a `police_verify` or `detect`
     police task via a test workflow or collector;
   - claim it;
   - submit a result whose free-text field holds a raw sample -> rejected per the router;
   - submit a clean result -> accepted.
10. Chat:
    - the user message contains a sample;
    - the Anthropic mock returns one tool step and then an answer;
    - `POST /api/chat` -> 200.
11. Forced exception:
    - (a) add a test-only route to the app instance (`app.add_api_route("/api/leak-boom", ...)`)
      that raises `RuntimeError` with a sample in its message. Call it with an
      ASGITransport created with `raise_app_exceptions=False` -> 500 body;
    - (b) Install the production hook first:
      - `monkeypatch.setattr(sys, "excepthook", sys.excepthook)`, so teardown restores
        the original;
      - then `install_excepthook(world.container.redactor)` (from
        `telescreen.masking.log_masking`; `Container.redactor` is at
        `src/telescreen/wiring.py:149`).
      - Only then `try: raise RuntimeError(<sample message>)` and, in the `except`, call
        `sys.excepthook(*sys.exc_info())`.
      - The hook logs the redacted traceback as `Uncaught exception: ...` to the root
        logger (`log_masking.py:install_excepthook`), so the text lands in `log_buffer`.
      - Also call `logging.getLogger("telescreen.leaktest").exception("boom %s", <sample>)`.
      - `capsys` stays active to capture stderr.

## Sinks (assert `find_leaks(text) == ()` for each item; the failure message names the sink label, never the text)

- DB: for every table in `Base.metadata.sorted_tables`, `SELECT *`, then every value
  that is `str` (and `bytes` decoded with `errors="replace"`). Do this for both worlds.
- Logs:
  - the `log_buffer` handler, installed at index 0 of the root handlers BEFORE
    `build_container` (assert its formatter is `MaskingFormatter` after build);
  - `caplog.text`;
  - remove the handler on teardown.
- Search: `dump_json`-serialized documents of `container.search` when it is an
  `InMemorySearchBackend`. Assert that it is.
- Outbound HTTP: every recorded request body (Slack, police push if any, Anthropic,
  GitHub).
- Evidence files: every file under `<tmp>/evidence`, as raw bytes. Check both the bytes
  decoded with `latin-1` and `utf-8` (`errors="replace"`).
- API responses: every recorded response text.
- Traces and exceptions:
  - the 500 body;
  - the `log_buffer` text written by the installed excepthook call. Non-empty guard: it
    contains `Uncaught exception`. This is the traceback sink;
  - the log text of the `.exception` call;
  - `capsys` stderr, as an additional must-be-clean sink. It may be empty and is NOT a
    non-empty guard.

## Tests

- `test_leak_matrix`: runs flows 1-11 and then checks all sinks.
  - Guard against a silent pass. Each sink must be non-empty: at least 1 DB text value,
    1 log line, 1 search document, 1 Slack request, 1 Anthropic request, 1 evidence file,
    20 API responses, and a `log_buffer` text containing `Uncaught exception`.
  - Each issue created must have `times_seen >= 1`.
- Focused tests (cheap, independent worlds):
  - `test_image_metadata_is_stripped_before_storage`;
  - `test_scanner_unavailable_returns_503_and_stores_nothing`;
  - `test_raw_findings_push_is_rejected_without_echo`.
- Keep `test_ui_fixtures.py`.

## Key Pitfalls

- Samples:
  - always come from `tests.support.samples`;
  - never write literal secrets, card numbers, My Numbers, JWTs or PEM bodies in source
    (gitleaks);
  - the image builders insert the sample at runtime.
- `find_leaks` also checks separator-stripped variants. Never log or print the matched
  text in assertion messages: `assert hits == (), sink_label` only.
- The forced-exception route is added to the app object in the test, not to product
  code.
- `build_container` calls `configure_logging` but NOT `install_excepthook`; only
  `cli.main` installs it (`src/telescreen/cli.py`).
  - Install it in the test as in flow 11(b).
  - Raw stderr from Python's default hook is not a product leak and must not be filed as
    a Reconciliation request.
  - Do not move `install_excepthook` into product code.
- Use `raise_app_exceptions=False` only for that call.
- pytest's own `LogCaptureHandler` is a `StreamHandler`. Insert the buffer handler at
  index 0 so `configure_logging` adopts it.
- `FakeClock` drives all waits. No sleeps.
- Never bind a mapping to a variable (typelint). Build JSON inline with
  `json.dumps`/`json.loads`.
- Workflow sources and team names must not trigger the Redactor (the upload API rejects
  raw values): no emails in workflow text.
- Do not import `tests.e2e.harness`.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen pytest -p no:cacheprovider tests/leak 2>&1 | tee /tmp/telescreen-p28c-leak-matrix-pytest.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tests/leak 2>&1 | tee /tmp/telescreen-p28c-leak-matrix-format.log; echo "exit=$?"
uv run --frozen ruff check --no-cache tests/leak 2>&1 | tee /tmp/telescreen-p28c-leak-matrix-ruff.log; echo "exit=$?"
uv run --frozen ty check tests/leak 2>&1 | tee /tmp/telescreen-p28c-leak-matrix-ty.log; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-p28c-leak-matrix-typelint.log 2>&1; ! grep -E '^tests/leak/' /tmp/telescreen-p28c-leak-matrix-typelint.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p28c-leak-matrix-pytest-all.log; echo "exit=$?"
test -z "$(find tests/leak -name '*.py' -exec wc -l {} + | awk '$1 >= 1000 && $2 != "total"')"; echo "exit=$?"
! grep -rnE 'AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[abp]-|BEGIN [A-Z ]*PRIVATE KEY' tests/leak; echo "exit=$?"
test "$(grep -c '^- \[ \]' impl-plans/active/p28c-leak-matrix.md)" -eq 0; echo "exit=$?"
```

Evidence required:

- every command exits 0;
- the `tests/leak` run shows `test_leak_matrix`, the three focused tests and
  `test_ui_fixtures` passed, with 0 failed and 0 skipped;
- the full pytest run shows 0 failed.

Mutation sanity (record in the Progress Log only, never as a verification entry,
conventions 7a):

- temporarily make one sink collector append a raw sample and confirm the matrix fails
  with that sink label;
- restore the collector and re-run.

## Completion Criteria

- [ ] Harness, image builders, flows and sink collectors delivered under `tests/leak`
- [ ] All 11 flows run; every sink is non-empty and leak-free in `test_leak_matrix` (the traceback sink is `log_buffer` with `Uncaught exception` after `install_excepthook(world.container.redactor)`)
- [ ] Focused image-strip, scanner-unavailable and raw-findings tests pass; `test_ui_fixtures.py` still passes
- [ ] Mutation sanity recorded in the Progress Log
- [ ] Verification passes on the final source (logs recorded); Reconciliation requests (if any) listed
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-09 00:00
**Tasks Completed**: Plan created (run B2 split of p28-assembly)
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
