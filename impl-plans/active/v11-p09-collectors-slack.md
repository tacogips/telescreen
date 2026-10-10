# V11-P09 Slack Collector: Exposure, Owner, Placement, Modified Time, Coverage

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 3 (Slack row), 4 (placement `slack_channel`, `modified_at`), 6 (coverage), 7 (owner identity: Slack `user`)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p09-collectors-slack`
- planPath: `impl-plans/active/v11-p09-collectors-slack.md`
- wave: 2
- dependsOn: `v11-p01-foundation`
- writePaths:
  - `impl-plans/active/v11-p09-collectors-slack.md`
  - `src/telescreen/collectors/slack`
  - `tests/collectors/slack/test_v11_context.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Fill the new transient `ResourceContent` fields (pinned by v11-p01 in
`telescreen/ports/collectors.py`) from Slack data, with **read-only** API calls. Raw user
ids and channel ids leave the adapter only inside `OwnerIdentity` / `PlacementRef`, both
`repr=False`. The scanner keys them (v11-p05). Nothing here persists anything.

Current code:

- `collectors/slack/client.py` (`SlackMessage` keeps only `ts`/`text`; `SlackChannel`
  only `id`; `SlackFile`);
- `collectors/slack/adapter.py:SlackAdapter.fetch` (builds `ResourceContent` at about
  lines 260 and 287);
- `collectors/slack/sweep.py` (builds `ResourceContent` at about lines 102-160).

## Non-goals

- No new Slack scopes beyond read scopes (`conversations.info` needs `channels:read`,
  `groups:read`, `im:read`, `mpim:read`). No write calls, and no call that changes
  sharing.
- No change to event verification, cursors or the sweep partitioning logic.
- Do not store channel names, user names, emails or file names anywhere new.

## Invariants

- Provider responses are parsed with `TypeAdapter.validate_json` into dataclasses, as the
  existing client does. Unknown fields are ignored.
- `SlackMessage.user`, `SlackFile.user` and channel ids appear only in transient objects.
  New dataclass fields that hold user ids use `field(repr=False)`.
- One `conversations.info` call per channel per scan task. Cache it inside the adapter
  call scope (a dict is forbidden by typelint; use a small dataclass holding a tuple of
  `(channel_id, info)` pairs, or pass the info down explicitly).

## Deliverables

1. **`client.py`**:
   - `SlackMessage` gains `user: str | None = field(default=None, repr=False)` and
     `edited: SlackEdited | None = None` (`SlackEdited(ts: str)`).
   - `SlackFile` gains `user: str | None = field(default=None, repr=False)`,
     `is_public: bool = False`, `public_url_shared: bool = False`, `updated: int | None
     = None`.
   - New `conversation_info(channel_id) -> SlackConversationInfo(id, is_private: bool =
     False, is_im: bool = False, is_mpim: bool = False, is_ext_shared: bool = False,
     connected_team_ids: tuple[str, ...] = ())` calling `conversations.info`
     (read-only), with the same error and rate-limit handling as the existing calls.
2. **`exposure.py`** (new, in `collectors/slack/`): `slack_exposure(info, own_team_id,
   file) -> Exposure`, with the mapping of design section 3:
   - `is_ext_shared` -> `external_shared` / `connect`, count = connected teams other
     than our own;
   - `is_im` -> `private` / `dm`;
   - `is_mpim` -> `internal` / `dm`;
   - `is_private` -> `internal` / `private`;
   - otherwise `domain` / `public`;
   - a file with `public_url_shared` -> `anyone_with_link` (wider wins).
3. **`adapter.py` and `sweep.py`**: every `ResourceContent` built for a message or file
   sets:
   - `exposure`;
   - `owner=OwnerIdentity(IdentityKind.SLACK_USER, user)` when `user` is present;
   - `placement=(PlacementRef(PlacementKind.SLACK_CHANNEL, channel_id),)` for messages
     (files: the channel from the event or ref when known, else `()`);
   - `modified_at` (message `edited.ts`, else `ts`, as UTC datetime; file `updated`,
     else `timestamp`);
   - `coverage`:
     - file larger than `max_file_bytes` (that is `TELESCREEN_SCAN_MAX_BYTES`; the
       existing `file.size <= self.max_file_bytes` check in `adapter.py`) ->
       `Coverage(CoverageState.TOO_LARGE)` with reason None (same mapping as v11-p11);
     - non-text file within the size limit -> `Coverage(UNSUPPORTED, IMAGE)` for an
       `image/*` mimetype, `UNSUPPORTED_TYPE` for other non-text mimetypes, and `BINARY`
       only when the mimetype is `application/octet-stream` or missing;
     - a provider 403 or `not_in_channel`-style access error on fetch ->
       `PERMISSION_DENIED`.

   On `conversations.info` failure, use exposure None (unknown); never fail the scan
   because of it.

## Pitfalls

- Do not add the channel name or user name to `display_title` or to the locator. They
  stay as in v1.
- Timestamps: Slack `ts` is a string of epoch seconds with decimals. Convert with
  `datetime.fromtimestamp(float(ts), UTC)`.
- An `is_ext_shared` channel can also be private. External sharing wins (wider).
- Keep each file under 1000 lines (`adapter.py` is 424 lines; put the new helpers in
  `exposure.py`).

## Tests (`tests/collectors/slack/test_v11_context.py`, httpx MockTransport)

Imitate `tests/collectors/slack/test_adapter.py`.

- public channel -> visibility `domain`, channel_kind `public`; private -> `internal`; im
  -> `private`/`dm`; ext_shared with 2 connected teams besides our own -> count 2;
- a message with `user` -> `owner.kind == slack_user`, and `repr(content)` does not
  contain the user id;
- placement contains a `slack_channel` ref with the channel id (value checked directly,
  not via repr);
- an edited message -> `modified_at` from `edited.ts`;
- an image file -> coverage `unsupported/image`; an `application/octet-stream` file ->
  `unsupported/binary`; a text file over `max_file_bytes` -> `too_large`;
- `conversations.info` HTTP 500 -> exposure None and the scan still succeeds;
- the request log of the mock transport contains only GET or read endpoints
  (`conversations.history`, `conversations.info`, `files.info`, and so on). Assert no
  `chat.`, `files.delete` or other write method was called.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/collectors/slack` and `<tests>` =
`tests/collectors/slack`. Existing Slack tests must still pass.

## Completion Criteria

- [x] Every Slack `ResourceContent` carries exposure, owner, placement, modified time and
      coverage as specified.
- [x] Read-only call assertion test passes; scoped gates pass; the typelint grep is
      empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:09
**Tasks Completed**: Added typed Slack conversation exposure metadata, transient exposure mapping, owner and placement references, UTC modified timestamps, explicit file coverage, and permission-denied coverage to adapter and sweep results. Added MockTransport tests for exposure mappings, owner repr privacy, placement, timestamps, unsupported and oversized files, info failures, access denial, and read-only calls. All Completion Criteria are checked.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/slack/client.py` before=c6e0293d670eb2f5d886b7121fd31647ce3321daa82f8ffaca28ee1bff47de4d after=b1640b99f7fa1aed1ac6d21fda16c818ace62c4038cfbc82816bce889d6fe8fa; `src/telescreen/collectors/slack/adapter.py` before=074dafb59b3767e82f80e76a09dc715f9b806ddc584a6615dc3de741c5c2d1fc after=a6970243ee4f67e447284f713e75a1f2d8746e85d82a1f3047a10cadfbf2536b; `src/telescreen/collectors/slack/sweep.py` before=5d2b3c4efaf39772beea44b8b72a3d0a1908c26aa3e44cadc13dfc869f422575 after=bdf2dfaf7e168106245a375c287a7a0b185c6df7c9aac11c5fe317b854ead87e; `src/telescreen/collectors/slack/exposure.py` before=absent after=47f55b74a5d79a9ba8dfd3ab24e46b3117643a0589092a6f4675396f7581073f; `tests/collectors/slack/test_v11_context.py` before=absent after=60d7d46d2cf4d0426700d6eec8c88611a031c70f7204e3928535a318bd2f2099; this plan before=c82bca7fa70f4a41ce6a4d4c0da4bc5c941ef66c3486635a2ef2f5775a9616e2.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-v11-p09-collectors-slack-format.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-v11-p09-collectors-slack-ruff.log`); `uv run --frozen ty check src/telescreen/collectors/slack tests/collectors/slack` -> exit 0 (log: `/tmp/telescreen-v11-p09-collectors-slack-ty.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/slack 2>&1 | tee /tmp/telescreen-v11-p09-collectors-slack-pytest.log` -> exit 0, 36 passed (log: `/tmp/telescreen-v11-p09-collectors-slack-pytest.log`); whole-repository `tools/typelint.py` output was filtered against both the manifest relative prefixes and the actual absolute-path prefixes, with zero Slack-path matches (log: `/tmp/telescreen-v11-p09-collectors-slack-typelint.log`).
**Notes**: The first new-test run had 12 passed and one failure because its sweep mock omitted the read-only `files.list` response; the mock was corrected, and the final complete Slack suite passed. An initial lint pass also found import ordering and test annotations, which were corrected. The whole-repository typelint command reports concurrent sibling-path diagnostics; none are in the Slack collector or Slack test paths. Plan-local edit intent is recorded at `tmp/telescreen-v11-20261010-c1/v11-p09-collectors-slack/step6-attempt-1/edit-intentions.md`.
