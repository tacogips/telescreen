# SVC-P03 Telescreen Publishers: Change Markers, Relay, Resync, Notification Outbox Adapters and Clients

**Status**: Completed
**Design Reference**: design-docs/specs/design-service-split.md sections 4.1-4.4, 5, 6, 10, 16
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p03-publishers`
- planPath: `impl-plans/active/svc-p03-publishers.md`
- wave: 3
- dependsOn: `svc-p01-foundation`, `svc-p02-db`
- writePaths:
  - `impl-plans/active/svc-p03-publishers.md`
  - `src/telescreen/projections`
  - `src/telescreen/integrations/notification_outbox.py`
  - `src/telescreen/integrations/notification_delivery.py`
  - `src/telescreen/integrations/notification_publish.py`
  - `src/telescreen/integrations/notification_client.py`
  - `src/telescreen/collectors/inventory_publish.py`
  - `src/telescreen/directory/identities.py`
  - `src/telescreen/directory/repository.py`
  - `tests/projections`
  - `tests/notification_outbox`
  - `tests/directory/test_orm_write_gate.py`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

Everything Telescreen needs to *publish* in split mode lives here. Wiring is svc-p06 and is
not done here. The components are:

1. The flush-marker listener.
2. The serialized relay that turns markers into revisioned `DirectoryEntity` and
   `IssueLinkState` commands.
3. The resync function.
4. Outbox adapters implementing `NotificationSender`/`MemberNotifier`, with redaction-hit
   counting.
5. The delivery handler and recovery provider for `notification_outbox`.
6. HTTP clients for the 4.4 reads and `/commands`.

Imitate:

- `collectors/inventory_publish.py:InventoryPublisher._emit` (outbox row plus engine task
  in the caller transaction);
- `integrations/inventory_outbox.py` and `integrations/inventory_delivery.py`
  (`InventoryDeliveryHandler`, `InventoryRecoveryProvider`);
- `inventory_service/clients.py:InventoryClient` (typed `ServiceHttp.post`);
- `directory/actors.py`, `directory/resolver.py` for reading directory rows.

## Non-goals

- No wiring, CLI registration or settings parsing (svc-p06).
- No consumer-side code (svc-p04/p05/p07).
- Do not change single-process behavior: the listener is installed only by an explicit
  call that svc-p06 makes in split mode.

## Deliverables and File-Level Changes

1. **ORM-only writes.**
   - `directory/identities.py:196`: replace `delete(MemberIdentityRow)` with
     `await session.delete(row)`.
   - `directory/repository.py:72`: replace the Core delete of `TeamMembershipRow` with
     loading the rows and calling `session.delete` for each. Behavior is unchanged.
   - `tests/directory/test_orm_write_gate.py`: an AST scan of `src/telescreen` that fails
     on Core `update(X)`, `delete(X)` or `insert(X)` for X in the watched models (`MemberRow`,
     `TeamRow`, `TeamMembershipRow`, `MemberIdentityRow`, `ApiTokenRow`, `OwnershipRuleRow`,
     `CollectorConfigRow`, `IssueRow`). Exclude `src/telescreen/db/**` (frozen migrations
     use raw `text()` SQL). The g2 sweep confirmed that the two lines above are the only Core
     writes on watched models today.
2. **`projections/markers.py`.**
   - `install_projection_markers(sessionmaker: async_sessionmaker[AsyncSession], clock: Clock) -> None`
     (svc-p06 calls it with exactly these two arguments) registers a `before_flush`
     listener on the sync session class used by that factory. Verify which mechanism works
     with the repository's `create_sessionmaker`. The listener must affect only sessions
     from that factory. Tests must uninstall in `finally`, so other tests' sessions are not
     marked.
   - `uninstall_projection_markers(...)` is for tests.
   - The listener adds `ProjectionChangeRow(id=new_id(), kind, entity_ref, recorded_at)`
     for every new, deleted or dirty watched instance. For dirty instances it checks
     whether a projected attribute changed, using `sqlalchemy.inspect(obj).attrs[name].history.has_changes()`.
   - Projected attributes:
     - `IssueRow`: `resource_key`, `number`, `status`, `owner_ref`;
     - `MemberRow`: `handle`, `display_name`, `active`, `role`, `email`, `slack_user_id`;
     - all columns of the other watched models.
   - `kind` is `issue_link` for `IssueRow` and `directory` otherwise. `entity_ref` is
     `"<entity kind>:<id>"`:
     - a team membership marks `team:<team_id>`;
     - an ownership rule marks `ownership_rules:rules`;
     - a collector config marks `collector:<id>`.
   - Use the clock injected at install time.
3. **`projections/entities.py`.** Pure async readers from main tables:
   - `read_directory_entity(session, kind, entity_id, revision) -> DirectoryEntity`
     returns `present=False` (payload None) when the row is gone. A member whose `active`
     is false stays present with `active=False`.
   - Use the svc-p01 field names exactly. `IdentityEntity.identity_kind` carries the
     `MemberIdentityRow` kind value (accepted p01 constraint). `ApiTokenEntity` includes
     `label`. Build payloads positionally or by keyword; extra fields are rejected by the
     codec.
   - `read_issue_link(session, issue_id) -> IssueLinkState`. `short_id` comes from
     `issues.keys.short_id(number)`.
4. **`projections/relay.py`.**
   - `ProjectionRelayProvider(ScheduleProvider)` enqueues `EngineTaskKind.PROJECTION_RELAY`
     with idempotency key `projection-relay:<minute>` when markers exist.
   - `ProjectionRelayHandler(TaskHandler)`. **Ordering rule (design section 5, DR-2)**:
     one transaction, executed in this exact order:
     1. `SELECT ... FOR UPDATE` on `counters` row `directory_revision`. On SQLite the
        transaction begins with a write (for example `UPDATE counters SET value=value WHERE
        name='directory_revision'`), so it holds the database write lock.
     2. Select up to 200 markers ordered by `recorded_at, id`, and keep their ids.
     3. Deduplicate them by (kind, entity_ref).
     4. Read the current state of each entity.
     5. Allocate `revision = counter + 1 ...` for each directory entity.
     6. Write `DirectoryUpdate` (with `for_inventory(entity)`) through
        `InventoryPublisher.emit_operation(session, "directory", op)`.
     7. Write `IssueLinkState` through `emit_operation(session, f"issue-link:{issue_id}", op)`.
     8. Write one `NotificationCommand` per batch, containing `DirectoryOperation`s with
        full entities, through `NotificationOutboxPublisher`. Split the command into
        commands of at most 8 operations, and include only member, team, identity and
        api_token kinds.
     9. Update the counter.
     10. `DELETE FROM projection_changes WHERE id IN (<kept ids>)`, by marker id.

     Return `TaskResult.done()` and re-enqueue if more markers remain.
   - Test seam (F4): the handler has an optional field
     `after_read: Callable[[AsyncSession], Awaitable[None]] | None = None`, invoked with the
     run's own session immediately after step 4 (state read) and before step 5. Production
     wiring never sets it.
5. **`projections/resync.py`.**
   `resync(sessions, clock, batch=500) -> int` inserts one marker per directory entity
   (members, teams, identities, tokens, the rules singleton, collectors) and per issue,
   committing in batches. It returns the count. svc-p06 registers the CLI.
6. **`collectors/inventory_publish.py`.** Add a public
   `emit_operation(session, stream_id, operation) -> None` that delegates to `_emit`.
   Existing methods are unchanged.
7. **`integrations/notification_outbox.py`.** `NotificationOutbox` has
   `append(session, command)`, `pending_ids`, `get_pending` and `acknowledge`. It stores
   `encode_notification_command` output in `NotificationOutboxRow`. Imitate
   `inventory/retention_outbox.py:RetentionOutbox`.
8. **`integrations/notification_publish.py`.** `OutboxNotificationSender` and
   `OutboxMemberNotifier` implement the ports. Each call does the following:
   1. **Recipient check.** Use the injected `RecipientResolver`. Raise
      `recipient_unknown` exactly where `NotificationService._check_recipients` would:
      when `not record_unreachable` and no category filter applies.
      `OutboxMemberNotifier` checks that the member id exists through
      `OwnerDirectory.member_ref`; if it does not, raise `recipient_unknown`.
   2. **Redaction.** Use the injected `Redactor`. Redact the inline subject and body, and
      every `str` field of the context (rebuild with `dataclasses.replace`). Record
      `ProducerRedactionHits(subject, body, context=((field, count), ...))` with only
      non-zero entries.
   3. **Publish.** Build `NotificationCommand(event_id=new_id(), occurred_at=clock.now(),
      operations=(op,))`, append it through `NotificationOutbox`, and enqueue
      `TaskSpec(NOTIFICATION_COMMAND_DELIVERY, event_id, json_null(), now,
      f"notification-command:{event_id}")`.
   4. **Receipt.** Return `NotificationReceipt(notification_ids, DlpVerdict.PASS,
      queued=True, skipped=(), pending=True)`. The ids are
      `notification_row_id(operation_seed(event_id, 0), i)` for each recipient index
      (members: index 0).

   `NotificationOutboxPublisher` (the same module) is used by the relay.
9. **`integrations/notification_delivery.py`.**
   - `NotificationCommandDeliveryHandler(TaskHandler, kind=NOTIFICATION_COMMAND_DELIVERY)`
     posts to `/commands`. On `applied` or `duplicate` it acknowledges. On `deferred` it
     retries after 30 s. On a transport error it retries with backoff, mirroring
     `InventoryDeliveryHandler`.
   - `NotificationRecoveryProvider` re-enqueues pending rows.
   - `NotificationDeliveryNoop(TaskHandler, kind=NOTIFICATION_DELIVERY)` returns
     `TaskResult.done()`. It completes legacy delivery tasks in split mode (design 10).
10. **`integrations/notification_client.py`.** Each class uses
    `ServiceHttp(prefix=NOTIFICATION_PREFIX)`:
    - `NotificationCommandClient.receive(command) -> NotificationCommandReceipt`;
    - `TemplateCatalogClient.lookup(session, name) -> TemplateInfo | None`, implementing the
      `TemplateCatalog` protocol; the session is ignored;
    - `RemoteNotificationTester.send_test(channel, member_id) -> TestOutcome`;
    - `ChannelStatusClient.status() -> ChannelStatus`.

    Transport errors become `TelescreenError(SERVICE_UNAVAILABLE or the existing code used
    by the inventory proxy)`. Do not invent a new `ErrorCode`.

## Key Pitfalls

- Never read entity state before taking the counter lock. Never delete markers by entity
  ref (DR-2).
- The relay is the only writer of the `directory` and `issue-link:*` streams.
- Never put raw text into a command. Inline text and context strings are always redacted
  first. Hits are counts only.
- The listener must not run SQL, and must not use `session.execute` or `merge` in
  `before_flush`. Adding objects is the only allowed action.
- Bulk `session.delete` of team memberships must keep the `put team members` semantics
  (`directory/repository.py`).
- Do not import `telescreen.notifications.*` or `telescreen.inventory.*` in these modules.
  The R7 gate checks them in svc-p08.

## Tests (`tests/projections`, `tests/notification_outbox`, `tests/directory`)

Markers and relay:

- Updating a member's `display_name` adds 1 marker. Updating only `last_sign_in_at` adds 0
  markers. Updating only `IssueRow.last_seen` adds 0. Changing the issue status adds 1.
- A rolled-back transaction leaves no markers.
- The relay drains markers and publishes inventory (without email) and notification (with
  email) payloads with increasing revisions, then deletes the markers.
- **Relay plus revoke (DR-2), deterministic SQLite schedule (F4).** SQLite allows one
  writer and `tests/support/db.py` uses one shared in-memory connection, so never await a
  second writer while run A holds the write lock. Instead:
  1. With markers installed, create an API token and let the marker commit.
  2. Run A with `after_read` set. The hook revokes the token through the ORM **in run A's
     own session**, which adds a new marker after step 2's id snapshot.
  3. Assert that run A publishes the token as not revoked at revision N, deletes only the
     pre-snapshot marker (by id), keeps the hook-inserted marker, and commits.
  4. Run B without a hook publishes revoked at revision N+1 and deletes the remaining marker.
  5. Apply both commands in both orders (A then B, B then A) to a small in-test
     only-if-newer state holder: a frozen dataclass plus a `revision` comparison. svc-p07's
     `DirectoryReplica` is built in parallel in wave 3, so it cannot be used here. The final
     state is revoked in both orders.
  6. Assert that no markers remain.
- The same schedule for an `IssueLinkState` status change: the hook changes the issue
  status in run A's session. The final applied status equals the latest, and stream
  sequences are A < B.
- A PostgreSQL variant marked `integration` runs true two-connection concurrency: run A
  blocks on the counter row lock while run B holds it, and the revision/state freshness
  assertions are the same.
- Never weaken these assertions. If the hook schedule cannot be expressed, write a
  Reconciliation request instead.
- `resync` on 3 members and 2 issues gives the expected marker count.

Outbox adapters, delivery and clients:

- An outbox adapter request with context `issue_title` containing a runtime-assembled
  sample secret (`tests.support.samples`) gives an outbox payload without the raw value,
  and `redaction.context` contains `("issue_title", 1)`.
- An unknown ref without `record_unreachable` -> `recipient_unknown` and nothing written.
- Receipt ids equal `notification_row_id(...)`, and `pending=True`.
- Delivery handler with an `httpx.MockTransport`:
  - `applied` -> acknowledged;
  - `deferred` -> retry;
  - 500 -> retry;
  - malformed body -> retry, with no content in the log.
- `tests/directory/test_orm_write_gate.py` passes after the two conversions.

## Verification

```bash
set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/projections src/telescreen/integrations src/telescreen/collectors/inventory_publish.py src/telescreen/directory tests/projections tests/notification_outbox tests/directory > /tmp/telescreen-svc-p03-format-final-r2.log 2>&1
set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/projections src/telescreen/integrations src/telescreen/collectors/inventory_publish.py src/telescreen/directory tests/projections tests/notification_outbox tests/directory > /tmp/telescreen-svc-p03-ruff-final-r2.log 2>&1
set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/projections src/telescreen/integrations src/telescreen/collectors/inventory_publish.py src/telescreen/directory tests/projections tests/notification_outbox tests/directory > /tmp/telescreen-svc-p03-ty-final-r2.log 2>&1
set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/projections tests/notification_outbox tests/directory tests/collectors tests/inventory 2>&1 | tee /tmp/telescreen-svc-p03-pytest-scoped-final.log
export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p03-typelint-clean-check.log 2>&1
! grep -rn 'telescreen.notifications\|telescreen.inventory\.' src/telescreen/projections src/telescreen/integrations/notification_outbox.py src/telescreen/integrations/notification_delivery.py src/telescreen/integrations/notification_publish.py src/telescreen/integrations/notification_client.py > /tmp/telescreen-svc-p03-import-gate-final.log 2>&1
set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p03-publishers-pytest-all-final.log 2>&1
```

Evidence: all commands above exit 0. Scoped tests pass 218 tests; the full suite passes
1,333 tests. Wiring remains assigned to downstream plans.

## Completion Criteria

- [x] Deliverables 1-10 are present. The DR-2 concurrency tests pass.
- [x] `grep -rn "telescreen.notifications\|telescreen.inventory\." src/telescreen/projections src/telescreen/integrations/notification_*.py` prints nothing.
- [x] Verification exits 0. The Progress Log has hashes.
- [x] Run 5 re-verification passed every g5 gate on checkpoint `47e933b4e8a1ad516203f92ae5b3a53c2e548167`; final hashes are in `tmp/telescreen-svc-20261016-g5/svc-p03-publishers/owned-file-sha256.txt`.

## Run 6 Re-verify Scope (manifest `telescreen-svc-20261016-g6`, wave A)

The code is complete at `8b09786` and unchanged at `077b71a`. Run 5 re-verified it and it
passed test-integrity and adversarial review with no finding; an agent exit then
interrupted branch evidence before the integration review.
- Do not re-implement. Run the g6 manifest verification commands (logs
  `/tmp/telescreen-svc-p03-publishers-g6-*.log`) on the planning-commit tree unchanged.
- Edit source or tests only to fix a failing gate or a review finding, at its root cause
  inside this plan's writePaths. Never weaken a relay or redaction assertion.
- Record the final SHA-256 of every owned file changed since `c3ed4bc` in
  `tmp/telescreen-svc-20261016-g6/svc-p03-publishers/owned-file-sha256.txt`.
- Append one Progress Log entry (at most 12 lines). Keep Status Completed and
  priorVerification empty (conventions 7a).

## Progress Log

(append sessions per conventions section 8)

### Session 1 — 2026-10-16

- Implemented deliverables 1-10 in this plan's writePaths, including ORM-only directory
  writes, scoped flush markers, entity readers, lock-before-read relay, resync, notification
  outbox adapters, redaction-hit counting, delivery recovery/no-op, and HTTP clients.
- Added deterministic SQLite relay/revoke and issue-status schedules, an integration-marked
  PostgreSQL concurrency variant, notification redaction/delivery tests, and the ORM write AST
  gate. The sample secret is absent from outbox payloads; the dependency grep is empty.
- Final scoped suite: 218 passed, 10 skipped. Final full suite: 1,333 passed, 37 skipped.
  The initial full-suite attempt exposed missing local annotations in the new tests; these
  were added, and the final full-suite rerun passed. Final format, Ruff, ty and typelint gates
  pass. See the complete command logs in Verification above.
- Source/test SHA-256 values (before -> after):
  - `src/telescreen/collectors/inventory_publish.py`: `b83c05d99008b90df7ddc34472a68121c5bc354a87d416a576df95a56628ed1e` -> `25245c9ecd7a7c23a44f513d2f6f536a81aff16d3c7c053d35a1b4292cfbca69`
  - `src/telescreen/directory/identities.py`: `6f2d71c4ebc72710f39ea62b73084c4624ead37c5366ab6a26ab386d1d284cab` -> `79275b0dd3c13c596e5e24c7dcc6a34eb8a24370a3deb8fadce53ff7149e9146`
  - `src/telescreen/directory/repository.py`: `014aaf12873d4bb34517d6b599f06c83cd6a3fc70e47eae9139031362a0413ba` -> `c9870f4110de5dd21435acd179de1037c00a0b8610ae5390dafaf3427ad6dbf4`
  - `src/telescreen/integrations/notification_client.py`: absent -> `f4f58961e76ed703d54ed188ba502964280036cc2608a662f56e455c5a44e44f`
  - `src/telescreen/integrations/notification_delivery.py`: absent -> `a3bc8b686a8c781c746dbbd85868d0adf3b30d951128aa93ad7b22c439281f02`
  - `src/telescreen/integrations/notification_outbox.py`: absent -> `36ab72c8540e8a48fa1928f14f531bdf69c9fb38954727146b82647906a6d44f`
  - `src/telescreen/integrations/notification_publish.py`: absent -> `57ea2d8bfe78d5511f2c6b05ce628d5f3ec8d6bb7a8c591ab56ef1d44f6d3cb9`
  - `src/telescreen/projections/entities.py`: absent -> `24e2ea060cdd75b0c7fdf8465ed737a88df308602602b2a2192276d912a2dc9a`
  - `src/telescreen/projections/markers.py`: absent -> `a0bc9accd6e65bdd098a2fdea2ea6c8bce5113c91834eb7bb9da0f296cf88554`
  - `src/telescreen/projections/relay.py`: absent -> `b50eef403438f806f869c7c8d0744d0fec1eb986239cd3dc04e7d97e97b673c0`
  - `src/telescreen/projections/resync.py`: absent -> `d58a0ab6bce5089bc84ffb95e876f082a48cf0f200d3c8bbf67678d2ea4572ab`
  - `tests/directory/test_orm_write_gate.py`: absent -> `fefe440ac786e0a5fa9b2b4431931e0e0a3b1d85e869520e7f1db8a2bba8447f`
  - `tests/notification_outbox/test_delivery.py`: absent -> `1669526d57bb3ecf9872921b6c7417b3815997b1b7a02a1a977ae61b245e413b`
  - `tests/notification_outbox/test_publish.py`: absent -> `cce8fc126acd4a5c4f71a3ea96c42de68a120cf4104f5353654d6e7e569cf537`
  - `tests/projections/test_markers.py`: absent -> `f78fea6e5757a95e7970d018f62c0457c6eff06302d41f63bb679a71ca0872d4`
  - `tests/projections/test_relay.py`: absent -> `d5618f155f0d142464c524c3d9fd95d275cd62a1ccd28f2492df07811c6cac1f`
  - `tests/projections/test_relay_postgres.py`: absent -> `d747631d96988ae12f3caae0a5eb81b0ae416c96c6ea265f75ead86c3f597664`
- The plan SHA-256 before this progress update was
  `bbc3c1f88549ef128136743c51dcd1b3d544ddf2ab427dba327442ea7f4eae4a`; final plan hash is
  recorded in `tmp/telescreen-svc-20261015-g4/svc-p03-publishers/file-hashes-final.txt`.
- No p03 review findings or repair requests were supplied at implementation entry. Formal
  integrity/adversarial and serial integration reviews, commit, and push remain downstream.

### Session 2 — 2026-10-16 (g5 re-verification)

- Re-verified checkpoint `47e933b4e8a1ad516203f92ae5b3a53c2e548167`; no source or test files changed. Review feedback and findings were empty; priorVerification remains empty.
- Scoped pytest passed 218 tests (10 integration skips); full pytest passed 1,333 tests (37 skips). Logs: `/tmp/telescreen-svc-p03-publishers-g5-pytest.log` and `/tmp/telescreen-svc-p03-publishers-g5-pytest-all.log`.
- Format, Ruff, ty, typelint path filter, import gate, diffstat and plan line-count gates exited 0; logs: `/tmp/telescreen-svc-p03-publishers-g5-{format,ruff,ty,typelint,import-gate,diffstat}.log`.
- Owned-file SHA-256 values for all files changed since `c3ed4bc` are recorded in `tmp/telescreen-svc-20261016-g5/svc-p03-publishers/owned-file-sha256.txt`; plan remains under 400 lines.
- Formal test-integrity/adversarial and integration review, commit, and push are downstream workflow steps.

### Session 3 — 2026-10-16 (g6 re-verification)

- Re-verified planning checkpoint `f0a34727fb479c141a8e503ffa23318ffe7a8504`; source and test files are unchanged from `077b71a`, with no review findings supplied.
- Scoped suite: 218 passed, 10 skipped; full offline suite: 1,333 passed, 37 skipped. Complete logs are `/tmp/telescreen-svc-p03-publishers-g6-pytest.log` and `/tmp/telescreen-svc-p03-publishers-g6-pytest-all.log`.
- Format, Ruff, ty, typelint, import gate, diffstat, and plan line-count gates exited 0. SHA-256 values for all owned files changed since `c3ed4bc` are in `tmp/telescreen-svc-20261016-g6/svc-p03-publishers/owned-file-sha256.txt`.
- `priorVerification` remains empty. Formal integrity/adversarial and serial integration reviews, commit, and push remain downstream.
