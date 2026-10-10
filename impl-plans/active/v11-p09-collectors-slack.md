# V11-P09 Slack Collector: Exposure, Owner, Placement, Modified Time, Coverage

**Status**: Ready
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

- [ ] Every Slack `ResourceContent` carries exposure, owner, placement, modified time and
      coverage as specified.
- [ ] Read-only call assertion test passes; scoped gates pass; the typelint grep is
      empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
