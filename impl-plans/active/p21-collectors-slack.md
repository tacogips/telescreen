# P21 Slack Collector Adapter Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Collector adapters, Resource identity, Per-kind behavior: slack), design-docs/specs/design-map-reduce-sweeps.md (Built-in sweep implementations: slack)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p21-collectors-slack`
- planPath: `impl-plans/active/p21-collectors-slack.md`
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-p21-collectors-slack-typelint.log 2>&1; grep -E '^(src/telescreen/collectors/slack|tests/collectors/slack)' /tmp/telescreen-p21-collectors-slack-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] SlackAdapter (all CollectorAdapter methods) and SlackSweep delivered with the exact names
- [ ] Event, schedule and sweep modes tested, with valid, invalid and old signatures
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
