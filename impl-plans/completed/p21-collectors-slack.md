# P21 Slack Collector Adapter Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Collector adapters, Resource identity, Per-kind behavior: slack), design-docs/specs/design-map-reduce-sweeps.md (Built-in sweep implementations: slack)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p21-collectors-slack`
- planPath: `impl-plans/completed/p21-collectors-slack.md` (archived from `impl-plans/active/` after run A2 integration acceptance, session 340; writePaths below record the run-time location)
- wave: 3
- dependsOn: `p04-ports`
- writePaths:
  - `impl-plans/active/p21-collectors-slack.md`
  - `src/telescreen/collectors/slack`
  - `tests/collectors/slack`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. `src/telescreen/collectors/__init__.py` is owned
by p20 (wave 4). Python namespace-package import works without it, so do not create it.

## Intent and Context

The built-in Slack collector supports event triggers (Events API webhook with
signing-secret verification), schedule triggers (`conversations.history` and `files.list`
since a cursor) and whole-workspace sweeps (one shard per conversation). It implements
the `CollectorAdapter` and `SweepCapable` Protocols from `telescreen.ports`. Content is
returned transiently and never stored.

## Non-goals

- No notifications (p17 owns `chat.postMessage` for notifications). No registry or
  wiring (p20 and p28). No database access.

## Deliverables (`src/telescreen/collectors/slack/`)

- `__init__.py`: docstring.
- `client.py`: `SlackApi(http: httpx.AsyncClient, token: SecretValue, api_base: str = "https://slack.com/api")`
  with typed calls `conversations_history(channel, oldest, latest, cursor, limit)`,
  `conversations_list(cursor, types)`, `files_list(channel, ts_from, cursor)`,
  `files_info(file_id)` and `download(url_private) -> bytes`. Responses are parsed with
  `TypeAdapter(...).validate_json` into dataclasses holding only the fields used (ts,
  text, files: id, name, mimetype, size, url_private; channels: id; `response_metadata.next_cursor`).
  - HTTP 429 -> `RateLimited(Retry-After)`.
  - `ok:false` with `message_not_found` or `file_not_found` -> `ResourceGoneError`.
  - Any other `ok:false` or non-2xx -> `TelescreenError(fetch_failed)`.
- `adapter.py`: `SlackAdapter(api_factory: Callable[[CollectorConfig], SlackApi], max_file_bytes: int)`
  with `kind = CollectorKind.SLACK`.
  - `verify_event`:
    - read `X-Slack-Request-Timestamp` and `X-Slack-Signature`;
    - check `v0=` + hex HMAC-SHA256(signing_secret, `v0:{ts}:{body}`) with
      `hmac.compare_digest`;
    - the timestamp must be within 300 s of `event.received_at`, otherwise invalid;
    - a body of `type: url_verification` -> `challenge` with the challenge string as
      `response_body`.
  - `resources_from_event` (`event_callback`) handles only channels in
    `selector.channel_ids`:
    - `message` -> changed message ref;
    - `message_changed` -> ref of `message.ts`;
    - `message_deleted` -> removed ref of `deleted_ts`;
    - `file_shared` -> file ref, if `include_files`;
    - `file_deleted` -> removed file ref.
  - ref_json:
    - message: `{"type":"message","team":T,"channel":C,"ts":TS}`, raw_locator
      `slack:<team>/<channel>/<ts>`;
    - file: `{"type":"file","team":T,"file":F}`, raw_locator `slack:<team>/file/<file_id>`.
  - `ref_from_json` rebuilds the raw locator.
  - `fetch` returns `ResourceContent(source="slack", ...)`:
    - message: one segment with locator `message`, `complete=True`, display_title
      `#<channel> message`;
    - file: text-like mimetypes (`text/*`, json, csv, yaml) up to `max_file_bytes` give
      segments per line (`L<n>`, `complete=True`). Others give no segments and
      `complete=False`.
    - `sweep_covered=True` when the channel is selected.
  - `list_changed`: the cursor is canonical JSON `{channel_id: last_ts}`. For each
    selected channel, history newer than its last ts (paged); for files,
    `files.list ts_from`. The cursor advances to the max ts seen. `full_listing=False`.
  - `renew_watch` -> `None` (Slack has no channel watch).
- `sweep.py`: `SlackSweep` implements `SweepCapable` (the adapter class may implement
  both).
  - `partition`: corpus `""` pages `conversations.list` (public and private channels;
    when `selector.channel_ids` is non-empty, keep only those).
    - shard per conversation;
    - `shard_key` = sha256 hex of `slack:<team>/<channel>`;
    - descriptor `{"channel": C}`;
    - `next_cursor` from `response_metadata`.
  - `shard_resources` pages the full history of the channel (and its files if
    `include_files`):
    - each message -> a `fetched` entry with transient content;
    - each file -> `fetched`, `removed` or `failed` per `files.info` and download
      outcome.

## Key Pitfalls

- Signature input is the exact raw body bytes. Never re-serialize.
- Never log message text, file names or tokens.
- Slack ts values are strings such as `"1700000000.000100"`. Compare them as Decimal or
  as tuples of ints, never as floats.
- The team id comes from the event envelope (`team_id`). For schedule and sweep, call
  `auth.test` once per api instance and cache the team id.

## Tests (`tests/collectors/slack/`)

All with `httpx.MockTransport`; sample secrets from `tests.support.samples`.

- Valid signature -> valid; tampered body -> invalid; timestamp 301 s old -> invalid;
  `url_verification` -> challenge with that string only.
- Message events in a selected channel -> a changed ref with the right raw locator;
  unselected channel -> nothing; `message_deleted` -> removed ref; `file_shared` with
  `include_files=False` -> nothing.
- Fetching a message containing a sample token -> a segment holds the text; the
  `repr(content)` does not.
- Fetching a deleted message -> `ResourceGoneError`. 429 -> `RateLimited(30)`.
- `list_changed` across 2 history pages advances the cursor.
- Sweep partition over 2 `conversations.list` pages -> shards with deterministic keys;
  `shard_resources` yields one entry per message; a file download 500 -> `failed` entry.
- `ref_from_json` round trip.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack
uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack
uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack
uv run --frozen pytest -p no:cacheprovider tests/collectors/slack 2>&1 | tee /tmp/telescreen-p21-collectors-slack-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p21-collectors-slack-typelint.log 2>&1; ! grep -E '(^|/)(src/telescreen/collectors/slack/|tests/collectors/slack/)' /tmp/telescreen-p21-collectors-slack-typelint.log   # prints nothing for this plan
```

## Completion Criteria

- [x] SlackAdapter (all CollectorAdapter methods) and SlackSweep delivered with the exact names
- [x] Event, schedule and sweep modes tested, with valid, invalid and old signatures
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated
- [x] Slack integer file timestamps parse in schedule/fetch/sweep paths; schedule cursor stores the file timestamp as a string
- [x] Event thread replies retain optional `thread_ts`, fetch through `conversations.replies`, preserve `raw_locator`, and round-trip with legacy refs

## Run A Resume (2026-10-09, session 338)

State: passed per-branch test-integrity and adversarial review at s319; integration review
pending. Dispatch: `impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A1.
Verify only: the static gap review found every Tests bullet covered and no in-scope
defect. Do not edit source unless a gate fails.

- [x] R0 Run the full Verification block on the untouched tree; log exit codes to
  `/tmp/telescreen-p21-collectors-slack-runA-*.log`.
- [x] R1 If any command fails, fix only the failing cause inside this plan's writePaths,
  then re-run the whole block. Otherwise record "no change".
- [x] R2 Record these accepted deviations in the Progress Log (no code change):
  `SlackApi.download` takes `max_bytes`; `conversations_replies` was added; HTTP 404 maps to
  `ResourceGoneError`; `list_changed` returns all history pages of one call in a single
  `ChangePage` with `has_more=False` (Slack history pages newest-first, so a
  `{channel_id: last_ts}` cursor cannot hold partial progress); `file_deleted` is not
  filtered by channel; thread replies are not enumerated by schedule or sweep.

## Run A2 Verify (2026-10-09, session 340)

Code is complete; this pass verifies it against the current tree. Resume rule: fix only
real gaps or failing causes inside this plan's writePaths; never rewrite working code.

- [x] V0 Run the Verification block on the current tree before any edit; log every
  command, exit status and `/tmp/telescreen-p21-collectors-slack-a2-*.log` path.
- [x] V1 Re-check Deliverables, Tests and Completion Criteria against the current source;
  list any gap in the Progress Log and fix it.
- [x] V2 Event-mode signature tests cover: valid -> accepted; wrong signature -> rejected;
  timestamp older than 300 s -> rejected; `url_verification` challenge handled. A raw
  message-text sample never appears in `ResourceRef`, cursors, returned errors or caplog.
  Add any missing case; all tests use fakes (no network).
- [x] V3 Re-run the Verification block on the final tree; every command exits 0. Report
  fixed items only in `addressedFeedback` (conventions 7a).

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:23 JST
**Tasks Completed**: Implemented typed Slack Web API calls, exact-body HMAC event verification and challenge handling, selected-channel event refs, transient message/text-file fetches, paged schedule cursor handling, canonical ref round trips, and deterministic conversation sweep shards with fetched/removed/failed file outcomes. Added MockTransport coverage, including successful text-file segmentation and repr redaction, and updated all completion criteria.
**Tasks In Progress**: None; formal integrity/adversarial and combined-tree integration reviews are downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/slack/__init__.py` before=absent after=e20925e2; `client.py` before=absent after=8a9c1115; `adapter.py` before=absent after=42bb9226; `sweep.py` before=absent after=3c7c3ae2; `tests/collectors/slack/test_adapter.py` before=absent after=57b015bd; `test_sweep.py` before=absent after=95f6c29e. Plan before=e0a68ba1.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0, 6 files formatted (log: `/tmp/telescreen-p21-collectors-slack-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-collectors-slack-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-collectors-slack-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack 2>&1 | tee /tmp/telescreen-p21-collectors-slack-pytest.log` -> exit 0, 13 passed, 0 failed (complete final log: `/tmp/telescreen-p21-collectors-slack-pytest.log`).
- Earlier run after adding the file success test exited 1 because the fixture used unsupported `httpx.URL.hostname`; this was fixed by checking `request.url.host`. Recovered failure records are at `tmp/telescreen-v1-20261008-s319/p21-collectors-slack/logs/failed-text-file-test-pytest.log` and `failed-text-file-test-ty.log`; the final source-matched gates above pass.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-p21-collectors-slack-typelint.log 2>&1; ! grep -E '(^|/)(src/telescreen/collectors/slack/|tests/collectors/slack/)' /tmp/telescreen-p21-collectors-slack-typelint.log` -> scoped gate exit 0, no p21 diagnostics. Whole-tree typelint itself exits 1 with 48 diagnostics across 129 files, all outside p21 (complete log: `/tmp/telescreen-p21-collectors-slack-typelint.log`).
**Notes**: Slack file downloads send the bot token only to HTTPS `files.slack.com`; upstream response bodies, content, file names and credentials are not logged. The shared-tree typelint result is not claimed as a whole-repository pass.

### Session: 2026-10-09 review redispatch
**Tasks Completed**: Rechecked the accepted collector and sweep design against the delivered Slack adapter, preserved the in-tree implementation, and reran all p21 scoped verification on the redispatched shared tree. The implementation acceptance criteria remain complete.
**Tasks In Progress**: Test-integrity, adversarial and branch-evidence reviews must produce their typed decisions through the later native join.
**Blockers**: None for p21 implementation or scoped verification.
**Reconciliation requests**: Serial reconciliation should write the lint filter command and its output into `tmp/telescreen-v1-20261008-s319/serial-reconcile-wave3/lint-diag-owner-attribution.log` and `accepted-paths-typelint-filter.log`. These paths are outside p21 `writePaths`; current full typelint emits 69 diagnostics, all outside p21 paths, while the p21 scoped filter is empty.
**Hashes**: Plan before=f3df629fe8aa303a720f608ad7c41f0cf98524bf5a2aa0e940460da9f87cb276; p21 source/test hashes unchanged from the fanout snapshot. Final plan hash recorded in the Step 6 evidence.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-review-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-review-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-review-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 13 passed, 0 failed (complete log: `/tmp/telescreen-p21-review-pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-p21-review-typelint-full.log 2>&1; ! grep -E '^(src/telescreen/collectors/slack|tests/collectors/slack)' /tmp/telescreen-p21-review-typelint-full.log` -> scoped filter exit 0 with no p21 diagnostics; whole-tree typelint exit 1 with 69 diagnostics outside p21. Complete log: `/tmp/telescreen-p21-review-typelint-full.log`; filter output: `/tmp/telescreen-p21-review-typelint-scope.log`.
**Notes**: The missing typed review decisions remain downstream workflow work and are not claimed complete here. No Slack source or test file changed during this redispatch.

### Session: 2026-10-09 test-integrity repairs
**Tasks Completed**: Fixed P21-TI-1 by adding optional `inclusive` to `SlackApi.conversations_history` and using `inclusive=True` with limit 1 for exact-message fetches. Fixed P21-TI-2 by parsing Slack `subtype` and `channel_id`, mapping real `message_changed`/`message_deleted` payloads, and filtering `file_shared` to selected channels. Tightened MockTransport coverage to enforce Slack query semantics and rewrote fixtures to real event shapes. Fixed P21-TI-3 by recording and running the plan's `(^|/)` typelint filter.
**Tasks In Progress**: Independent adversarial review of these corrections is downstream.
**Blockers**: None.
**Reconciliation requests**: None for P21-TI-1, P21-TI-2, or P21-TI-3. The earlier serial-reconciliation request for the two wave-3 attribution logs remains outside p21 `writePaths`.
**Hashes**: `src/telescreen/collectors/slack/client.py` before=8a9c11151aca991c271640d6f0eae7f67c94ba3cbdbe7ca6c2567757f28af64c after=e24cffa8a01ce82e88fcdc3ab54f3615882b93303a215df0c0a1eb6153326934; `src/telescreen/collectors/slack/adapter.py` before=42bb9226fafc8153b2d307f25efc1308d3531da6079e2879edfb7bdecaa60a2f after=9871bba6221e386cea87054401ea41a6929d29624196fff285aeadac871e2100; `tests/collectors/slack/test_adapter.py` before=57b015bd4317ace4dcd89e7796a882090bedf9cd5949eb13e597350bca4f5a8d after=6c270039ea553770a2dbe3a7e69bc0cc2b68c577084718e51e8da258e7e8faff; plan before=b9b7e60689b422e9c890aaa8d3b66044bb06775cfbf044e66ab9bc17ce548bb5.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> final source exit 0, 6 files formatted (complete log: `/tmp/telescreen-p21-collectors-slack-format-final-TI.log`). A pre-format correction attempt exited 1 and is preserved at `/tmp/telescreen-p21-collectors-slack-format-failed-TI.log`; the reported expression was formatted and the final gate passed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-collectors-slack-ruff-final-TI.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-collectors-slack-ty-final-TI.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack 2>&1 | tee /tmp/telescreen-p21-collectors-slack-pytest-final-TI.log` -> exit 0, 14 passed, 0 failed (complete log: `/tmp/telescreen-p21-collectors-slack-pytest-final-TI.log`). The test-only run also passed 12 tests (log: `/tmp/telescreen-p21-tic-regression-pytest-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-p21-collectors-slack-typelint-final-TI.log 2>&1; ! grep -E '(^|/)(src/telescreen/collectors/slack/|tests/collectors/slack/)' /tmp/telescreen-p21-collectors-slack-typelint-final-TI.log` -> full run exit 0, scoped filter exit 0 with no p21 diagnostics (complete log: `/tmp/telescreen-p21-collectors-slack-typelint-final-TI.log`; filter output: `/tmp/telescreen-p21-collectors-slack-typelint-scope-final-TI.log`).
**Notes**: All three test-integrity findings are implemented and verified on final source. The initial zsh wrapper attempt had a shell `status` variable error after pytest passed; the successful rerun is recorded above. Formal independent review remains pending and is not claimed accepted.

### Session: 2026-10-09 Step 6 final verification
**Tasks Completed**: Revalidated the p21 event, schedule and sweep implementation on the current shared tree. The accepted P21-TI-1 and P21-TI-2 corrections remain present: exact message lookup uses Slack's inclusive history query, and event parsing handles message edits/deletions plus channel-filtered file shares. The plan's event tests cover valid, tampered and 301-second-old signatures.
**Tasks In Progress**: Independent adversarial and combined-tree integration reviews are downstream workflow steps.
**Blockers**: None for p21 implementation or required verification.
**Reconciliation requests**: None owned by p21. The earlier wave-3 lint attribution evidence paths are outside p21 `writePaths` and remain with serial reconciliation.
**Hashes**: Before verification, plan=9a8170068973a363d3c672e5ee8fd565b72f60b5bf71aab38e2d28dc1480018d; current p21 source/test hashes are recorded in `/tmp/telescreen-p21-step6-rerun/source-hashes-before.txt`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0, 6 files formatted (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ty-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 14 passed, 0 failed (complete log: `/tmp/telescreen-p21-step6-rerun/pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 0, 134 files clean; p21 filter found no diagnostics (complete logs: `/tmp/telescreen-p21-step6-rerun/typelint-full.log` and `/tmp/telescreen-p21-step6-rerun/typelint-scope-filter.log`).
**Notes**: Scoped verification and full-tree typelint pass on the current shared tree. Formal review acceptance is not claimed in this implementation entry.

### Session: 2026-10-09 Step 6 self-check repairs
**Tasks Completed**: Bounded Slack file downloads in `SlackApi.download` to `max_file_bytes + 1` streamed bytes, so one sentinel byte detects overflow without buffering the full response. File resources now use the generic `Slack file` display title instead of forwarding the untrusted Slack filename. Added a byte-cap regression test and asserted the generic title while preserving the existing file-content checks.
**Tasks In Progress**: Independent adversarial and combined-tree integration reviews are downstream workflow steps.
**Blockers**: None for p21 implementation or required scoped verification.
**Reconciliation requests**: The shared-tree full typelint gate currently exits 1 with 187 diagnostics in non-p21 paths. The p21 path filter is empty; aggregate diagnostics are owned by their respective plans and serial integration.
**Hashes**: `src/telescreen/collectors/slack/client.py` before=e24cffa8a01ce82e88fcdc3ab54f3615882b93303a215df0c0a1eb6153326934 after=b657d81c630116346d8a3832293d9b20397a0bc666b7cfd86aeed1333084eb58; `src/telescreen/collectors/slack/adapter.py` before=9871bba6221e386cea87054401ea41a6929d29624196fff285aeadac871e2100 after=6df5deeb7c406ea1c1700a21d626abc64202e4c2fb0fe081185ce788d208d1d9; `src/telescreen/collectors/slack/sweep.py` before=3c7c3ae239c21a5f7a7ba6a65d5a59b0d4783fe5476c4870610e3700d43d99f5 after=5d2b3c4efaf39772beea44b8b72a3d0a1908c26aa3e44cadc13dfc869f422575; `tests/collectors/slack/test_adapter.py` before=6c270039ea553770a2dbe3a7e69bc0cc2b68c577084718e51e8da258e7e8faff after=7624d2430c95c4e62b05382d6e3aba8b4cd819436f0ca20da04c6c15adacb65f.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0, 6 files formatted (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-format-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-check-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ty-check-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 15 passed, 0 failed (complete log: `/tmp/telescreen-p21-step6-rerun/pytest-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 1, 187 diagnostics across 230 files, all outside p21 (complete log: `/tmp/telescreen-p21-step6-rerun/typelint-final.log`); `! grep -E '(^|/)(src/telescreen/collectors/slack/|tests/collectors/slack/)' /tmp/telescreen-p21-step6-rerun/typelint-final.log` -> exit 0, no p21 diagnostics (complete filter log: `/tmp/telescreen-p21-step6-rerun/typelint-scope-final.log`).
**Notes**: This entry supersedes the prior Step 6 final-verification entry's whole-tree typelint observation, which was captured before later shared-tree changes. P21's scoped gates pass on the repaired source; the non-p21 aggregate failure remains visible for serial integration.

### Session: 2026-10-09 Step 6 final source rerun
**Tasks Completed**: Tightened `SlackApi.download` stream chunk size to at most `max_bytes + 1`; converted the tracking stream test fixture to a typed dataclass. The regression confirms only two source chunks are consumed to produce the five-byte overflow sentinel for a four-byte cap. Current final source hashes are recorded below.
**Tasks In Progress**: Independent adversarial and combined-tree integration reviews are downstream workflow steps.
**Blockers**: None for p21 implementation or required behavioral verification.
**Reconciliation requests**: Whole-tree typelint exits 1 with 126 diagnostics in non-p21 paths (241 files); the p21 filter is empty. Serial integration owns the aggregate outcome.
**Hashes**: `src/telescreen/collectors/slack/client.py` before=b657d81c630116346d8a3832293d9b20397a0bc666b7cfd86aeed1333084eb58 after=6415391db41fb008104c9406eea6b7843443c792991246750e0452ffb240c8ee; `src/telescreen/collectors/slack/adapter.py` before=9871bba6221e386cea87054401ea41a6929d29624196fff285aeadac871e2100 after=6df5deeb7c406ea1c1700a21d626abc64202e4c2fb0fe081185ce788d208d1d9; `src/telescreen/collectors/slack/sweep.py` before=3c7c3ae239c21a5f7a7ba6a65d5a59b0d4783fe5476c4870610e3700d43d99f5 after=5d2b3c4efaf39772beea44b8b72a3d0a1908c26aa3e44cadc13dfc869f422575; `tests/collectors/slack/test_adapter.py` before=7624d2430c95c4e62b05382d6e3aba8b4cd819436f0ca20da04c6c15adacb65f after=6da9a54f0c8f7f5205857a4b20e60423c326d6bdf9c2b01253e4657d1b4f1b7d.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0, 6 files formatted (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-format-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ruff-check-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-step6-rerun/ty-check-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 15 passed, 0 failed (complete log: `/tmp/telescreen-p21-step6-rerun/pytest-final3.log`). An earlier attempt exited 2 during collection while p20's untracked `collectors/__init__.py` imported `RateLimited` from `ports.sweeps`; the subsequent current-tree run passed after p20's shared-tree correction. Failed log: `/tmp/telescreen-p21-step6-rerun/pytest-final2.log`.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 1, 126 diagnostics across 241 files, all outside p21 (complete log: `/tmp/telescreen-p21-step6-rerun/typelint-final3.log`); p21 filter -> exit 0, no diagnostics (complete log: `/tmp/telescreen-p21-step6-rerun/typelint-scope-final3.log`).
**Notes**: The earlier p20 import issue is resolved in the current shared tree. P21 scoped verification passes; the repository-wide typelint failure remains an aggregate result for serial integration and is not reported as a pass.

### Session: 2026-10-09 adversarial repair
**Tasks Completed**: Resolved `P21-ADV-FILE-TIMESTAMP-INT`: `SlackFile.timestamp` is now `int | None`, cursor watermark conversion is explicit, and file fixtures use integer `created` and `timestamp` values. Resolved `P21-ADV-THREAD-REPLY-GONE`: message events preserve optional thread roots in `ref_json`, replies fetch through `conversations.replies`, and `raw_locator` remains unchanged. Added schedule cursor, event mapping, thread fetch, empty-replies, and legacy/threaded round-trip assertions.
**Tasks In Progress**: Independent test-integrity/adversarial review and combined-tree integration review remain downstream.
**Blockers**: None for p21 implementation or required verification.
**Reconciliation requests**: Schedule and sweep still enumerate `conversations.history` only; thread replies are fetched when events identify them, but are not independently enumerated by schedule or sweep. This coverage boundary is retained per repair scope for design/serial reconciliation.
**Hashes**: `client.py` before=6415391db41fb008104c9406eea6b7843443c792991246750e0452ffb240c8ee after=c6e0293d670eb2f5d886b7121fd31647ce3321daa82f8ffaca28ee1bff47de4d; `adapter.py` before=6df5deeb7c406ea1c1700a21d626abc64202e4c2fb0fe081185ce788d208d1d9 after=62b8e9d28b03117e96de6885b1f945671912f47cd841706fa56fc45b1df6a662; `test_adapter.py` before=6da9a54f0c8f7f5205857a4b20e60423c326d6bdf9c2b01253e4657d1b4f1b7d after=0dcabbda9677e4d493962c90ff74f9b8c20042ea916a8fbc155450bc30f306e6; `test_sweep.py` before=95f6c29e66effb99cd26c45596ad48731360dbb6794f2467f3f337ff98627828 after=eb2f0ea55960a542e37e8aa3396a4dd287f7d04b8e478927a96520730c45e1b2. `sweep.py` unchanged at 5d2b3c4efaf39772beea44b8b72a3d0a1908c26aa3e44cadc13dfc869f422575.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --no-cache src/telescreen/collectors/slack/client.py src/telescreen/collectors/slack/adapter.py tests/collectors/slack/test_adapter.py tests/collectors/slack/test_sweep.py` -> exit 0 (log: `/tmp/telescreen-p21-adv-repair/ruff-format-write.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-adv-repair/ruff-format-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-adv-repair/ruff-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-p21-adv-repair/ty-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 19 passed, 0 failed (complete log: `/tmp/telescreen-p21-adv-repair/pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 0, 249 files clean; p21 filter empty (complete log: `/tmp/telescreen-p21-adv-repair/typelint-full.log`; filter output: `/tmp/telescreen-p21-adv-repair/typelint-p21-filter.log`).
**Notes**: The test suite verifies integer `created`/`timestamp` for `files.list` and `files.info`, including the schedule cursor and sweep fetch paths. No existing assertion was removed. Formal review decisions are pending downstream.

### Session: 2026-10-09 Run A resume verification
**Tasks Completed**: Ran the complete scoped verification block against the current shared tree. No source or test changes were needed. Recorded the Run A Slack API deviations: `SlackApi.download` accepts `max_bytes`; `conversations_replies` supports event-identified threads; HTTP 404 maps to `ResourceGoneError`; one `list_changed` call consumes all history pages and returns `has_more=False` because Slack history is newest-first; `file_deleted` is not channel-filtered; and schedule/sweep history does not independently enumerate thread replies.
**Tasks In Progress**: Independent test-integrity, adversarial and combined-tree integration review decisions remain downstream workflow work.
**Blockers**: None for p21 implementation or required verification.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/p21-collectors-slack.md` before=efa63e12328f10303558a3003b471e9b03391ff740fa1576d4be5b04ed43e6e5 after=3afa4792f1d05d501b39681f28077d7930f4400803cb248d4320e4eaa3bee6e3.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0, 6 files already formatted (complete log: `/tmp/telescreen-p21-collectors-slack-runA-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-collectors-slack-runA-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-collectors-slack-runA-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 19 passed, 0 failed (complete log: `/tmp/telescreen-p21-collectors-slack-runA-pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 0, 249 files clean (complete log: `/tmp/telescreen-p21-collectors-slack-runA-typelint.log`); scoped `grep -E '^(src/telescreen/collectors/slack|tests/collectors/slack)'` produced no diagnostics (empty output: `/tmp/telescreen-p21-collectors-slack-runA-typelint-scope.log`).
**Notes**: All three Run A resume items are complete. Formal review is not claimed accepted here.

### Session: 2026-10-09 adversarial repair P21-ADV-THREAD-REPLY-PARENT-FIRST
**Tasks Completed**: Increased the `conversations.replies` limit to 2 while retaining exact `ts` matching. Updated the thread-reply fake to return the parent before the requested reply and assert reply text plus repr hiding. Added a parent-only regression that still reports the requested reply gone; preserved the empty-replies regression.
**Tasks In Progress**: Independent adversarial re-review and combined-tree integration review remain downstream.
**Blockers**: None for p21 implementation or scoped verification.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/slack/adapter.py` before=62b8e9d28b03117e96de6885b1f945671912f47cd841706fa56fc45b1df6a662 after=074dafb59b3767e82f80e76a09dc715f9b806ddc584a6615dc3de741c5c2d1fc; `tests/collectors/slack/test_adapter.py` before=0dcabbda9677e4d493962c90ff74f9b8c20042ea916a8fbc155450bc30f306e6 after=81d3509fe11bd3dc3622efd4c6eb683f7021adf4382f83cb22d3c16dc260abdf; plan before=b695ef919b2fb605c15c4d8c8aea788f5d22c6fcfc19a4a58a3499d8ecc3518a; final after hash: `/tmp/telescreen-p21-parent-first/plan-final.sha256`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-parent-first/ruff-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-parent-first/ruff-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (complete log: `/tmp/telescreen-p21-parent-first/ty-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> exit 0, 20 passed, 0 failed (complete log: `/tmp/telescreen-p21-parent-first/pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 0, 250 files clean; scoped filter has no p21 diagnostics (complete log: `/tmp/telescreen-p21-parent-first/typelint.log`; filter output: `/tmp/telescreen-p21-parent-first/typelint-scope.log`).
- `test "$(grep -c '^- \[ \]' impl-plans/active/p21-collectors-slack.md)" -eq 0` -> exit 0; no incomplete checklist items.
**Notes**: `P21-ADV-THREAD-REPLY-PARENT-FIRST` is implemented and locally verified; independent re-review is pending and not claimed accepted.

### Session: 2026-10-09 Run A2 verification
**Tasks Completed**: Rechecked the Slack adapter and sweep deliverables against the accepted design and plan. Added a MockTransport regression proving that sample message text does not enter event refs, schedule cursors, validation errors, or captured logs. The existing signature tests cover valid, tampered, 301-second-old, and URL challenge cases; timestamp and thread-reply regressions remain present.
**Tasks In Progress**: Independent test-integrity/adversarial and combined-tree integration review are downstream workflow steps.
**Blockers**: None for p21 implementation or required verification.
**Reconciliation requests**: None.
**Hashes**: `tests/collectors/slack/test_adapter.py` before=81d3509fe11bd3dc3622efd4c6eb683f7021adf4382f83cb22d3c16dc260abdf after=b1ba70f63fe3186cf8d5d8c7fa14ecf8c5b6ce331e4dd775b8b963a3e1f6d62a; plan before=48ff2305bdb8cc311d441ad89001bdca732a391f30a4a99b0c43932640706c2b.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> initial exit 0 (log: `/tmp/telescreen-p21-collectors-slack-a2-format.log`); final exit 0 (log: `/tmp/telescreen-p21-collectors-slack-a2-v3-format.log`). The first final attempt before formatting exited 1; the reported concatenation was formatted, then the final check passed (log: `/tmp/telescreen-p21-collectors-slack-a2-final-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> initial and final exit 0 (logs: `/tmp/telescreen-p21-collectors-slack-a2-ruff.log`, `/tmp/telescreen-p21-collectors-slack-a2-v3-ruff.log`).
- `uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> initial and final exit 0 (logs: `/tmp/telescreen-p21-collectors-slack-a2-ty.log`, `/tmp/telescreen-p21-collectors-slack-a2-v3-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/collectors/slack` -> initial exit 0, 20 passed; final exit 0, 21 passed, 0 failed (logs: `/tmp/telescreen-p21-collectors-slack-a2-pytest.log`, `/tmp/telescreen-p21-collectors-slack-a2-v3-pytest.log`).
- `uv run --frozen python tools/typelint.py` -> initial and final exit 0, 254 files clean; p21 grep filter empty (logs: `/tmp/telescreen-p21-collectors-slack-a2-typelint.log`, `/tmp/telescreen-p21-collectors-slack-a2-v3-typelint.log`; empty final scoped output: `/tmp/telescreen-p21-collectors-slack-a2-v3-typelint-scope.log`).
**Notes**: No source implementation changes were needed. Independent review and its decisions are not claimed here.
