# Service split: Telescreen, Inventory and Notification

Status: IMPLEMENTED (run 11 final re-verify passed on `66531bd`; all eight svc plans and the g1-g11 manifests are archived under `impl-plans/completed/`; the section 12 Nodetto layout-only fix was applied by the owner after run 11: the 12 tables were raised above their frames in z-order, because Tidy layout counts a table as a frame member only when it lies inside the frame and above it), 2026-10-16
Requirements: [design-service-split-requirements.md](design-service-split-requirements.md)
(R1-R8, owner decisions D1-D5). Builds on [inventory microservice](design-inventory-microservice.md),
[inventory/alert boundaries](design-inventory-alert-boundaries.md) and the
[inventory store cutover](design-inventory-store-cutover.md).

## Overview

Telescreen, Inventory and Notification become three independently deployable services,
each with its own database. Inventory and Notification never call Telescreen. Notification
never calls Inventory. Telescreen pushes identity data and issue link state to the other
two services through durable outboxes. Every notification producer publishes versioned
commands through a producer-local outbox. The single-process composition stays the
default, and its behavior does not change.

The design reuses the patterns from the inventory extraction: versioned codecs in
`telescreen.ports`, producer outbox plus idempotent consumer inbox, recovery providers,
shared-secret service credentials, fixed-origin HTTP clients with finite timeouts and
bounded bodies, `db/ownership.py` stores, owner migration histories with adoption,
isolated-process import gates, multi-process TCP tests and restricted-login PostgreSQL tests.

## 1. Scope and phase boundary

All of R1-R8 are in scope for this run. The work maps onto the eight plans in section 17,
so no continuation phase is defined. Out of scope (requirements section 6): splitting
collection from issues, a broker or mesh, and executing a production migration. This
design adds no notification-history API (see correction C6).

## 2. Corrections to the requirements evidence

| # | Requirement text | Verified behavior (code at `aa3804b`) | Design consequence |
| --- | --- | --- | --- |
| C1 | `clients.py` lists six Inventory -> Telescreen calls | `clients.py` (108 lines) also defines `InventoryClient.receive` (`/commands`, :72) and `InventoryClient.status` (`/retention-status`, :78). Both are **Telescreen -> Inventory** clients. `status` is called by the Bureau's `RetentionNotificationReceiver` (`notifications/retention.py:63`). Today the eligibility check therefore runs Bureau -> Inventory during notification. | `/commands` stays. `/retention-status` and `InventoryClient.status` are removed: Inventory checks eligibility locally in the same transaction that commits the notification command (section 8.4). |
| C2 | Owner fallback "from issue assignee" via `/assignee` | `/assignee` (`assignee_of`) validates asset owner refs (`inventory/values.py:134`). Owner fallback is `IssueAssetOwnerResolver` (`issues/asset_owner.py`). It is called inside the Bureau retention receiver: (1) the oldest reconciled-status issue on the resource with an `owner_ref`, mapped to an active member through member identities, then (2) the first matching enabled ownership rule, which gives its team. | The issue link projection carries `status` and `owner_ref`. The directory projection carries identities, teams and ownership rules (`match_rule` moves to a pure module). Severity is not needed and is not projected. |
| C3 | `/actors` returns member/team display names | `InventoryActorDirectory` also resolves `collector:<id>` refs to collector config names, and falls back to "Telescreen". | The directory projection also carries collector display names (section 4.1). |
| C4 | Authentication delegates OIDC | `/authenticate` resolves **API tokens** (unkeyed SHA-256 `token_hash`, scopes, member binding, revocation) as well as OIDC bearers. The OIDC member lookup uses the keyed identity HMAC (`identity/signin.py`), so it needs the identity key. | D1 is extended to API tokens. Token verifiers are projected, and each service needs the identity key (section 7). |
| C5 | Weekly digest is notification-owned | `notifications/weekly.py` reads issues (`IssueReportSource`) and members, then calls `MemberNotifier`. It is a Telescreen **producer**. | `weekly.py` moves to `telescreen/issues/weekly_report.py` and stays in Telescreen. `engine_tasks` stays MAIN. |
| C6 | "Notification history UI/API" | No history list endpoint or page exists. Notification rows are visible only through workflow step output ids, alert `notification_count`, the CLI test-send output and audit records. | R4 history means: rows and outcomes stay queryable in the Notification store. Notification audit export (section 11) is the operator view. `src/web` does not change. |
| C7 | Template rendering imports `workflow.dsl` | `render.py` and `validate.py` import only `Template`/`TemplatePart` (`workflow/dsl/model.py:64-71`) and `parse_template` (`workflow/dsl/templates.py`). These are pure. | They move to `telescreen/core/templating.py`. `workflow.dsl` re-exports them. |
| C8 | Telescreen-side template use | Workflow upload (`workflow/engine/definitions.py:173`), alert rule validation (`alerts/validate.py:66`) and the CLI test send look up templates through `TemplateCatalog`. Integration status reads SMTP/Slack configuration in Telescreen (`integrations/tests.py`). | In split mode these use synchronous Telescreen -> Notification reads (section 4.4). |
| C9 | `bde11e7` inventory-only remote mode | It is released on `main`. It depends on the six Bureau callbacks, and retention handoff goes to the Bureau-hosted notification. | Under R1 a remote Inventory cannot reach in-process notification. The supported topologies become *single process* and *full split* (section 3). |

## 3. Topology and deployment modes

| Mode | Selected by | Processes | Stores |
| --- | --- | --- | --- |
| Single process (default, unchanged) | No service URLs | `telescreen serve`/`worker` | Main URL (inventory URL optional, as today). Notification tables are always in the main database. |
| Full split | `TELESCREEN_INVENTORY_SERVICE_URL` **and** `TELESCREEN_NOTIFICATION_SERVICE_URL` | Telescreen API + engine, `telescreen-inventory serve`, `telescreen-notification serve` | Three databases |

Setting only one service URL is a `ConfigError` at startup. In single-process mode,
`TELESCREEN_NOTIFICATION_DATABASE_URL` is rejected unless it equals the main URL, because
in-process notification queues in the caller transaction. In split mode Telescreen never
opens the inventory or notification store, and never constructs their business services.

Allowed runtime calls (all HTTP, fixed origin, service credential):

- Telescreen -> Inventory: `/internal/inventory/v1/commands` (observations, reconciliation, stop-tracking, issue links, directory), plus the public `/api/data/*` proxy.
- Telescreen -> Notification: `/internal/notification/v1/commands` (sends, directory), `/test`, `/templates/lookup`, `/channels`, plus the public proxy for notification settings and templates.
- Inventory -> Notification: `/internal/notification/v1/commands` (retention sends only).

## 4. Contracts (`telescreen.ports`)

All contracts are frozen dataclasses or enums. Each has an encode/decode codec that
validates strictly on both publish and consume, rejects unknown versions, enforces
`require_payload_size` and never echoes content in errors.

### 4.1 Directory projection (`ports/directory_projection.py`)

`DirectoryEntity(kind, id, revision: int >= 1, present: bool, payload | None)`. `revision`
comes from a main counter (`counters.directory_revision`) and increases for every
published entity state. A consumer applies an entity only when its `revision` is greater
than the stored revision for `(kind, id)`. Absent entities are stored as tombstones, so
delivery is order-insensitive and idempotent.

| Kind | Payload fields | Inventory | Notification |
| --- | --- | --- | --- |
| `member` | id, handle, display_name, active, role, email, slack_user_id | without email/slack | full |
| `team` | id, slug, name, lead_member_id, member_ids, email, slack_channel | without email/slack | full |
| `identity` | id, member_id, kind, identity_key (existing keyed HMAC hex) | yes | yes |
| `api_token` | id, label, token_hash (existing SHA-256), scopes, member_id, revoked | yes | yes |
| `ownership_rules` | single id `rules`: ordered (id, position, source, pattern, team_id, enabled) | yes | no |
| `collector` | id, name | yes | no |

Labels, creation times, sign-in times and raw OIDC issuer/subject are never projected.
Delivery addresses go only to Notification (data minimization).

### 4.2 Issue link state (new `InventoryOperation`)

`IssueLinkState(issue_id, resource_key, short_id, status: IssueStatus, owner_ref | None,
present)` with `kind = issue_link`. It travels on the existing ordered inventory
outbox/inbox (D3) on stream `issue-link:<issue_id>`. The consumer keeps the latest state
per issue. The same envelope also gains `DirectoryUpdate(entity)` (`kind = directory`,
stream `directory`). Both are additive kinds in `InventoryCommand` schema version 1.
Inventory must be upgraded before Telescreen starts emitting them (section 13).

### 4.3 Notification command (`ports/notification_commands.py`)

`NotificationCommand(event_id <= 36, occurred_at, operations: 1..8, schema_version = 1)`.
The operations are applied atomically in one Notification transaction:

- `SendOperation`: the `NotificationRequest` fields (channel, recipients <= 64 refs, subject, body, actor_ref, run_id, issue_id, event, template_name, context, inline, set_name, target_index, record_unreachable, category), plus `producer_redaction_hits`.
- `MemberSendOperation`: the `MemberNotification` fields, plus `producer_redaction_hits`.
- `DirectoryOperation(entity)`: accepted only from the Telescreen credential.

`producer_redaction_hits` is a count-only `ProducerRedactionHits(subject: int >= 0,
body: int >= 0, context: tuple[ContextFieldHits, ...])` with
`ContextFieldHits(field, count >= 1)`. It counts the `Redactor` hits the producer
removed before persistence (section 10).

The codec rejects a `field` that is not a string field of `NotificationContext`, as well
as duplicate fields and more entries than there are context fields. `MemberSendOperation`
must have `subject = body = 0`. The field carries no value, span, offset or rule
identifier.

Notification row ids are deterministic: `uuid5(NAMESPACE_URL,
"telescreen:notification:<event_id>:<op>:<recipient>")`. Producers and the Notification
store therefore agree on ids without a reply. `NotificationRequest` gains an optional
`id_seed`. The in-process path leaves it unset and keeps `new_id()`.

The receipt is `NotificationCommandReceipt` = `applied` | `duplicate` | `deferred`. Only
`applied` and `duplicate` acknowledge the outbox row. The consumer stores
`payload_sha256` per `event_id`. A reused id with a different digest is a `conflict`
error, so the row stays pending and an alert is logged, as in the existing retention receipts.

### 4.4 Synchronous Telescreen -> Notification reads

These carry no producer transaction. A failure maps to the existing safe
service-unavailable error.

| Path | Request -> response | Telescreen caller |
| --- | --- | --- |
| `POST /test` | `{channel, member_id}` -> `TestOutcome` | `IntegrationTester` (Slack/email test) |
| `POST /templates/lookup` | name -> `TemplateInfo` or null | `TemplateCatalog` (workflow upload, alert rule validation) |
| `GET /channels` | -> `{email: bool, slack: bool}` configured flags | `IntegrationStatusService` |
| `GET /health` | -> liveness plus replica `directory_revision` watermark | operators/tests |

Inventory `GET /internal/inventory/v1/health` also reports its replica watermark.

## 5. Projection publication in Telescreen (split mode only)

1. **Change markers.** A SQLAlchemy `before_flush` listener, installed only by the split
   composition, adds a `projection_changes(id, kind, entity_ref, recorded_at)` row for
   every insert, update or delete of `MemberRow`, `TeamRow`, `TeamMembershipRow`,
   `MemberIdentityRow`, `ApiTokenRow`, `OwnershipRuleRow`, `CollectorConfigRow` and
   `IssueRow`. Updates add a marker only when a projected attribute changed, judged from
   SQLAlchemy attribute history. For example, scan-time `IssueRow.last_seen` or sign-in
   time updates add no marker. It only adds ORM objects, so it runs no SQL in the flush. To make it a
   single choke point, every write to the watched models must be an ORM unit-of-work
   write. Two Core deletes exist today: `directory/identities.py:196` (`delete(MemberIdentityRow)`)
   and `directory/repository.py:72` (`delete(TeamMembershipRow)`). Both become ORM
   `session.delete` calls. A static test forbids Core `update(...)`/`delete(...)` on the
   watched models. Markers commit or roll back with the change.
2. **Relay.** A schedule provider and handler in the Telescreen engine drains markers in
   bounded batches. Relay runs are **single-flight and lock-before-read**. Each run is
   one main transaction (READ COMMITTED on PostgreSQL; `BEGIN IMMEDIATE` on SQLite), in
   this order:
   1. Lock the `counters` row `directory_revision` (`SELECT ... FOR UPDATE` on
      PostgreSQL; the immediate write lock on SQLite). A concurrent or lease-expired
      duplicate run waits here.
   2. Select a bounded batch of markers and remember their marker ids.
   3. Read the current entity and issue state. Each statement sees every commit up to
      that point, including the previous relay run.
   4. Allocate directory revisions from the locked counter, and IssueLinkState stream
      sequences through `InventoryPublisher` in the same transaction. The relay is the
      only writer of `issue-link:*` and `directory` streams.
   5. Write `DirectoryUpdate`/`IssueLinkState` into the inventory outbox and
      `DirectoryOperation` commands into `notification_outbox`.
   6. Delete the drained markers **by marker id**. A marker committed after step 2 is
      kept, so the next run republishes that entity.

   Because runs are serialized and state is read after the lock, a higher revision or
   sequence always carries state at least as fresh as a lower one. A crash before commit
   replays the markers. Publishing the same state twice is harmless.
3. **Resync.** `telescreen projections resync` inserts markers for every directory entity
   and every issue in batches. Run it at cutover, after restoring a consumer from backup,
   or whenever a health watermark shows a consumer is behind.

## 6. Consistency and staleness rules

- Replicas are eventually consistent. With all services up, lag is bounded by the engine
  poll interval plus delivery. No replica expires on age, so R1 holds while Telescreen is stopped.
- **Unknown ref in an Inventory write** (owner field, assignment) returns
  `recipient_unknown` as a validation error. This is the same response as today.
- **Removed or inactive member** is never a recipient, cannot authenticate and is
  displayed as inactive.
- **Revoked API token** stops authenticating in a service when that service applies the
  revocation entity. The relay ordering rule (section 5) guarantees that the revocation
  always ends up as the newest revision. This is the main staleness exposure. It is
  documented in the runbook and covered by an outage/replay test and a concurrent-relay test.
- **Unknown recipient at Notification** (the replica may lag behind a producer): if
  `occurred_at` is within the grace window (`TELESCREEN_NOTIFICATION_REPLICA_GRACE_SECONDS`,
  default 600), the command is `deferred` and the producer retries. After the window,
  rows are recorded `skipped` with `recipient_unknown` (always, never raised), so a
  command cannot become a poison message. Producers still raise `recipient_unknown`
  locally for requests without `record_unreachable`, because Telescreen owns the directory
  (section 10).
- **Empty replica** (fresh consumer): Inventory reads show unresolved actors with the
  existing `unknown` display (`"Unknown"`, inactive), and authentication fails closed
  (`unauthorized`).
  Notification defers within the grace window. The runbook orders resync before producers
  start.

## 7. Authentication and authorization (D1, D2)

Each service builds its `ApiContext.authentication` hook from `identity/verifier.py`,
`jose.py` and discovery (the same OIDC settings), plus a **replica authenticator**.

- An OIDC bearer is verified locally. The identity key is computed with the shared
  identity/fingerprint key reference and looked up in the replica `identity` entries. The
  member must be active with a role, and permissions come from `role_permissions`.
- An API token is hashed with the existing `hash_token` and matched against non-revoked
  replica `api_token` entries. Permissions come from `effective_token_permissions`.
- The resulting `Principal` is identical to Telescreen's for the same directory state.
  Parity tests run Telescreen `authenticate` and each replica authenticator over the same
  fixture.

Public service routes require the service header plus the user `Authorization` header,
as in the inventory proxy contract. Telescreen proxies them and discards caller-supplied
service headers. Notification accepts two credentials:
`TELESCREEN_NOTIFICATION_SERVICE_TOKEN` (Telescreen: every internal route and the proxy)
and `TELESCREEN_NOTIFICATION_INVENTORY_TOKEN` (Inventory: `/commands` only, with
`SendOperation`/`MemberSendOperation` for event `data_overdue` only). The producer
identity comes from the credential, never from the payload. Inventory keeps
`TELESCREEN_INVENTORY_SERVICE_TOKEN`.

## 8. Inventory independence (R1, R5)

1. **Removed:** `BureauClient`, `inventory_service/bureau.py` `build_bureau_router`, the
   `/retention-status` client and endpoint, and `TELESCREEN_BUREAU_SERVICE_URL`. The
   `/api/data/*` proxy stays in Telescreen.
2. **Replica-backed ports.** `ActorLookup`, `AssigneeLookup` and auth use
   `inventory_directory_entries`. `AssetIssueLookup` and the owner fallback use
   `inventory_issue_links` plus the `ownership_rules` replica and the pure `match_rule`
   (moved to `telescreen/core/ownership_match.py`). Open and reconciled status sets come
   from `core.vocabulary`, as today. Parity tests compare the replica adapters with
   `telescreen.directory`/`issues` adapters over the same fixture. JSON/CSV responses and
   permissions are unchanged.
3. **Audit (D5).** The existing `inventory_audit_outbox` stays. In split mode its pump
   drains locally: in one inventory transaction it appends to `inventory_audit_records`
   (same hash-chain format, own head) and marks the row delivered. No receipt table is needed.
4. **Retention (R5).** The existing overdue publisher and `inventory_retention_outbox`
   stay (one obligation id per asset, due field and value revision). A local dispatcher
   handles each pending obligation in **one inventory transaction**:
   - It checks eligibility (`RetentionLookup`, now local). The eligibility query lives in
     `telescreen.inventory` (`inventory/retention_outbox.py:retention_status(session,
     command, clock)`, shared with `InventoryRetentionLookup.status`), so
     `inventory_service` runs it in the dispatch session without importing inventory
     SQL models.
   - If the obligation is not current (cleared, deleted or superseded), it appends
     `data_asset.overdue_superseded` to the local chain.
   - If it is current, it resolves the recipient. The owner is taken first from the asset
     owner ref through the replica, then from the issue fallback, then from the
     ownership-rule team. The dispatcher then writes a `NotificationCommand` (`event_id` =
     obligation id) to `inventory_notification_outbox`:
     - a member gets one `MemberSendOperation` (`assigned`, `data_overdue`);
     - a team gets two `SendOperation`s (email and Slack, `team:<slug>`,
       `system.data_overdue`, `record_unreachable`).
   - It appends `data_asset.overdue_notified` with recipient kind and count, and marks the
     obligation delivered.

   A second pump delivers `inventory_notification_outbox` to Notification. The audit
   action names and detail fields are unchanged.
5. **Worker loops:** due-date publication, audit drain, retention dispatch and
   notification delivery. Each is independent, with rotating bounded batches (20) and
   30 s timeouts.

## 9. Notification service (R2, R4)

New package `telescreen/notification_service/` (`app`, `cli`, `wiring`, `worker`,
`transport`, `replica`, `inbox`), entry point `telescreen-notification`
(`serve`, `migrate [--adopt --writers-stopped]`, `send-test`, `audit verify|export`), and
`python -m telescreen.notification_service`.

- **Inbox.** `/commands` claims `notification_inbox(event_id, payload_sha256,
  applied_at)` and applies every operation through the existing `NotificationService`
  in the same notification transaction, with `id_seed = event_id`. `DirectoryOperation`
  upserts `notification_directory_entries`. A command whose id exists in the migrated
  `retention_notification_receipts` is a `duplicate`, which preserves pre-cutover dedup.
- **Identity through ports.** `notifications/preferences.py`, `member_send.py`,
  `tester.py` and `delivery.py` stop importing `MemberRow`/`TeamRow`. They use
  `ports.directory` protocols: main-table adapters stay in `telescreen.directory`, and
  replica adapters live in `notification_service/replica.py`.
- **Templates.** `telescreen/core/templating.py` (C7). Template validate/render import no
  `workflow` module.
- **DLP.** The existing `DlpGate` is authoritative and runs in Notification before any
  row is stored, so it stays on the outbound path. `DlpGate.inspect` and
  `inspect_redacted` gain an optional `prior_hits: int = 0`. The single-process path
  never sets it, so its behavior is unchanged.

  `NotificationService._render` derives `prior_hits` from the operation's
  `producer_redaction_hits`:
  - The v1 event-less path and the inline event path use `subject + body`.
  - The template path, for every context field with hits, multiplies the field's count
    by the number of times that field's variable occurs in the selected channel's subject
    and body template texts. Fields the template does not render contribute 0, as today.

  Prior hits fold in exactly as redactor hits do in `inspect_redacted` today. Let
  `total = own redactor hits + prior_hits`. Under BLOCK, if `total > 0`, the message is
  `[BLOCKED BY DLP]` with verdict BLOCKED. Otherwise the detector inspects the text; if
  `total > 0`, a PASS verdict becomes MASKED and BLOCKED stays BLOCKED. `hit_count` is
  `inspected hits + total`. For the v1 path, `inspect` applies the same fold after its
  detector pass.

  Result: for the same raw input, the row `dlp_verdict`, a non-zero `dlp_hit_count` and
  the `notification.queued`/`notification.blocked` audit action match single-process
  mode under both MASK and BLOCK. The hit_count value is also equal when every detection
  lies inside one substituted value. A detection that spans a template-literal boundary
  is counted by Notification's own pass, exactly as today.
- **Delivery.** `NotificationService` gets a `TaskQueue` adapter for
  `notification_delivery_state(notification_id, attempt, available_at, leased_until,
  max_attempts)`. A pump claims due rows (`FOR UPDATE SKIP LOCKED` on PostgreSQL, and an
  immediate transaction on SQLite). It runs the unchanged `NotificationDeliveryHandler`
  with a synthesized `ClaimedTask` and applies `done`/`retry`/`dead`. Queued rows that
  have no state row (copied at cutover) are seeded as attempt 1. SMTP and Slack
  credentials exist only in this process.
- **Public routes.** `/api/auth/me/notification-settings` (GET/PUT) and
  `/api/notification-templates[/...]` use the existing router builders and permissions
  with the Notification `ApiContext`. Telescreen proxies the same paths in split mode, so
  the UI is unchanged.
- **Audit.** `AuditLog` is bound to `notification_audit_records`/`_chain_head`.

## 10. Producers and receipt semantics (R3)

All producers already depend on `NotificationSender` or `MemberNotifier`: issues
(activity, comments, mentions, digest, exceptions, verification), alerts evaluator,
workflow notify, notify targets, human, SLA, collector and police health, API issue
routes and the weekly report. In split mode the Telescreen composition binds
**outbox adapters** to those two ports. Producer modules are not edited.

The outbox adapter does three things inside the caller's `AsyncSession`:

1. It validates recipient refs with the local `RecipientResolver`, and raises
   `recipient_unknown` exactly where `NotificationService._check_recipients` raises today.
2. It applies `masking.redact.Redactor` to inline subject/body and every context string,
   so outbox rows and wire messages hold only masked text. It records each
   `RedactionResult.hit_count` in `producer_redaction_hits`: subject, body, and one entry
   per context field with hits. Notification therefore receives masked text plus the
   count-only signal of what was removed, and folds it into the authoritative gate
   (section 9). Text that a producer already redacted before calling the port (for
   example the v1 workflow inline body) adds no new hits, so its verdict matches today's
   too. The inventory retention dispatcher (section 8.4) does the same for its context.
3. It writes one `notification_outbox` row and enqueues a
   `NOTIFICATION_COMMAND_DELIVERY` engine task, mirroring `InventoryPublisher`. A
   recovery provider re-enqueues pending rows.

Notification outcomes never roll back the producer.

`NotificationReceipt` gains `pending: bool = False`. Split-mode receipts are `pending=True`,
`queued=True`, `verdict=pass` (meaning "not blocked at publication"), list all
deterministic ids and have `skipped=()`. Documented split-mode semantics:

| Consumer | Single process (unchanged) | Split mode |
| --- | --- | --- |
| Workflow notify step output | `queued` false when blocked/unqueued | always `queued: true` plus ids, unless `recipient_unknown` fails the step |
| Notify targets (v2) | per-target queued/blocked/skipped counts | queued = recipients, blocked = skipped = 0 |
| Alert fire outcome | `blocked` when every request is blocked | `delivered` with count = planned rows; `blocked` cannot occur |
| Test sends (integrations) | in-process tester | synchronous `POST /test` |
| CLI template test send | in-process | Telescreen CLI exits 2 and refers to `telescreen-notification send-test` |

Final DLP verdicts, skipped, opt-out and unreachable outcomes are recorded on the
Notification rows (same ids) and in the Notification audit chain. In split mode the
Telescreen engine completes leftover `notification_delivery` tasks as no-ops.

## 11. Audit per service (D5)

Each service appends its own actions to its own chain. All chains use the same hash
formula and verifier, parameterized by record/head tables:

| Chain | Tables | Read path |
| --- | --- | --- |
| Telescreen | `audit_records`, `audit_chain_head` (unchanged) | `/api/audit/search`, `/api/audit/verify` |
| Inventory | `inventory_audit_records`, `inventory_audit_chain_head` | `telescreen-inventory audit verify|export` |
| Notification | `notification_audit_records`, `notification_audit_chain_head` | `telescreen-notification audit verify|export` |

Export emits stored (masked) records as JSON lines plus the head hash. Auditor procedure:

1. Run the three verifies.
2. Merge the exports by `at`.
3. Correlate across services by command `event_id`, obligation id and notification ids
   carried in the details.

Existing records are never rewritten. Pre-cutover inventory actions remain in the main
chain. An audit intent whose acknowledgment was lost just before cutover may appear in
both chains; it has the same `event_id`, and the runbook says to deduplicate by it. In
split mode the UI audit page shows only Telescreen actions.

## 12. Persistence ownership and migrations (R6)

`Store` gains `NOTIFICATION`. The three table sets are disjoint, and `store_for` fails on
any unknown table.

| Store | Tables |
| --- | --- |
| MAIN (50) | current 52 minus the four moved tables, plus `notification_outbox`, `projection_changes` |
| INVENTORY (15) | current 10 plus `inventory_directory_entries`, `inventory_issue_links`, `inventory_audit_records`, `inventory_audit_chain_head`, `inventory_notification_outbox` |
| NOTIFICATION (9) | moved `notifications`, `notification_templates`, `notification_settings`, `retention_notification_receipts`, plus `notification_inbox`, `notification_directory_entries`, `notification_delivery_state`, `notification_audit_records`, `notification_audit_chain_head` |

Replica tables share one shape: `(kind, id)` PK, `revision`, `present`, indexed
`lookup_key` (handle, slug, `kind:identity_key` or token hash), `payload_json` and
`applied_at`. `inventory_issue_links` has `issue_id` PK, indexed `resource_key`,
`short_id`, `status` and `owner_ref`.

DDL order: Nodetto JSONL first (new frames per model module, exact index names, logical
relations only within a store), then `ddl-arrange`, `ddl-export`, models, then new revisions.

Nodetto layout (run 7). Frame membership is geometric. `ddl-arrange` reflows a frame
together with the tables inside it, and lays out every table outside all frames in the
frameless row. At `a5392d9` only `inventory_audit_records`, `inventory_audit_chain_head`,
`notification_audit_records` and `inventory_notification_outbox` lie inside a frame. The
other eight service-split tables sit in the frameless row (y 3346) or above
`f-workflow`, and the `f-projections` and `f-inventory-links` frames are empty. The fix is
visual only. Each of the 12 tables is moved into the frame of its model module, and the
frame is enlarged to fit:

| Model module | Frame | Tables |
| --- | --- | --- |
| `projections.py` | `f-projections` "Directory projections" | `projection_changes`, `inventory_directory_entries`, `notification_directory_entries` |
| `inventory_links.py` | `f-inventory-links` "Inventory issue links" | `inventory_issue_links` |
| `notification_handoff.py` | `f-notification-handoff` "Notification handoff" | `notification_outbox`, `inventory_notification_outbox`, `notification_inbox`, `notification_delivery_state` |
| `service_audit.py` | `f-service-audit` "Service audit chains" | `inventory_audit_records`, `inventory_audit_chain_head`, `notification_audit_records`, `notification_audit_chain_head` |

Then run `mise run ddl-arrange` and `mise run ddl-export`. No table, column, index or
relation changes, so the SQL exports, models and migrations stay unchanged. A test in
`tests/db/test_nodetto_schema.py` asserts that each of the 12 tables lies inside the frame
in this table: the table origin is inside the frame, and `x + width` is within the frame
width. The 11 pre-existing frameless inventory handoff tables are out of scope.

Revisions:

- Legacy shared graph `0010_service_split`: all 12 new tables, the `directory_revision`
  counter seed, two genesis heads and the PostgreSQL append-only triggers for the two new
  audit tables.
- Owner histories: `main_0002`, `inventory_0002` and a new packaged baseline
  `notification_0001` (SQL snapshots for both dialects). Existing revisions and the
  `main_0001`/`inventory_0001` snapshots are not edited. Only the `baseline.py` helper
  allow-lists the new snapshot and audit tables.
- `--store notification --adopt` validates the nine-table schema and stamps
  `notification_0001` where the tables already exist (shared or legacy database). Shared
  owner-history upgrades require notification adoption first, with an explicit error.
- Split main stores keep the four moved tables unused and ungranted (as inventory tables
  were).
- Co-located notification history (owner clarification O1, 2026-10-15). When the
  notification store is the main store (notification URL unset or equal to main) but
  owner histories are used (for example an inventory-only separate database in single
  process mode), `main_0001` has already created the four moved tables. The configured
  upgrade then applies `notification_0001` by validating those four tables against the
  packaged baseline schema exactly as `--adopt` does, creating only the five
  notification-only tables, seeding the audit head and stamping, in one transaction. This
  is not silent adoption of an unknown store: the tables were created by the main history
  in the same database. A distinct notification URL remains rejected in single process
  mode; no notification URL is required for that mode.
  Scope of O1 (re-review, run 3): `db/migrate.py` selects the co-located path from
  configuration only, never from table presence. It applies to the configured upgrade
  (no `--store`) when the inventory URL differs from main and the notification URL is
  unset or equal to main, after `main` has been upgraded in that run (fresh or adopted
  main store). Unchanged paths: a single shared URL with existing owner histories still
  raises "notification history requires adoption"; an explicit `--store notification`
  upgrade and every distinct notification URL (split mode) still require an empty store
  or `--adopt`. Within the selected O1 path the co-located upgrade has two cases, both in
  one transaction:
  (a) fresh main created by `main_0001` (no legacy `alembic_version`): validate the four
  co-located tables against the packaged notification baseline, create only the five
  notification-only tables, seed the audit head, install the append-only trigger and stamp;
  (b) main adopted from legacy `0010_service_split` (legacy `alembic_version` is
  `0010_service_split`, so all nine tables and the seeded head already exist): run the
  same checks as `--store notification --adopt` (schema of all nine tables and the
  `Store.NOTIFICATION` owner state, including the head), create and seed nothing, and
  stamp `notification_0001`.
  Any other state (for example only some of the five notification-only tables present)
  and any validation mismatch raise and roll back without a stamp. In both cases the
  downgrade of `notification_0001` on a database that also holds `alembic_version_main`
  drops only the five notification-only tables and the append-only trigger; the four
  co-located tables stay with the main history. The svc-p02-db regression test in
  `tests/db/test_service_split_migration.py` covers case (a) upgraded twice and case (b),
  each with a separate inventory database. Observable result (owner resolution 3, run 3):
  after a configured `telescreen migrate` in single process mode with a separate
  inventory database, the main database holds exactly the MAIN and NOTIFICATION tables
  plus `alembic_version_main` and `alembic_version_notification`, and the inventory
  database holds exactly the INVENTORY tables plus `alembic_version_inventory`
  (`tests/e2e/test_inventory_database_config.py`).

Restricted-login PostgreSQL tests (marked `integration`) grant each runtime role DML on
only its store's tables. They deny cross-store reads/writes, version tables and trigger DDL.

## 13. Cutover and rollback runbook (stopped writers)

Shared or inventory-only split deployment -> full split:

1. Upgrade the Inventory service code before Telescreen emits new kinds.
2. Stop all writers (Telescreen API/engine, collectors, inventory) and take backups.
3. Run `telescreen migrate` (0010 or `main_0002`), then
   `telescreen migrate --store inventory` (`inventory_0002`). Create the notification
   store with `telescreen-notification migrate`.
4. Start Notification, Inventory, the Telescreen API and worker with their service URLs
   and tokens configured. Run `telescreen projections resync`; wait until both service
   replicas contain the current directory and the projection outbox is drained. This
   seeds Notification's recipient resolver before any copied queued member row is visible.
5. Stop all writers again. Run `telescreen notification-store copy --writers-stopped`,
   then `telescreen notification-store verify --writers-stopped`. These reuse the
   `db/transfer*` row copy and value-free manifests for the four moved tables; the copy
   preserves `notification_directory_entries`. For shared -> split inventory, run the
   existing `inventory-store copy/verify`. Inventory transfer gains `inventory_0002`
   support: replicas and the notification outbox are excluded (and must be empty or
   delivered), and inventory audit tables copy with append-only lineage.
6. Restart Notification, Inventory, the Telescreen API and worker, then wait for each
   process to become ready before enabling normal writes. Copied queued notifications
   are delivered by Notification. Pending retention obligations are deduplicated against
   the copied `retention_notification_receipts`.

Rollback to single process: stop writers, then run
`notification-store copy --direction merge` and verify. Merge refuses if target receipts
or template versions are missing from the source. The audit chain tables are copied as
historical chains. Then unset the service URLs and restart. The notification and
inventory chains stay verifiable with their CLIs.

## 14. Configuration and processes (R8)

| Process | Settings | Command |
| --- | --- | --- |
| Telescreen API/engine | main URL, `INVENTORY_SERVICE_URL/TOKEN`, `NOTIFICATION_SERVICE_URL/TOKEN`, OIDC, identity key; no SMTP/Slack | `telescreen serve`, `telescreen worker` |
| Inventory | `INVENTORY_DATABASE_URL`, `INVENTORY_SERVICE_TOKEN`, `NOTIFICATION_SERVICE_URL`, `NOTIFICATION_INVENTORY_TOKEN`, OIDC, identity and fingerprint keys, overdue cron, public base URL; no SMTP/Slack | `telescreen-inventory serve` |
| Notification | `NOTIFICATION_DATABASE_URL`, both notification tokens, OIDC, identity key, DLP settings, SMTP/Slack, replica grace | `telescreen-notification serve` |

All names carry the `TELESCREEN_` prefix. Tokens are `env:`/`file:` secret references.
`examples/compose/docker-compose.services.yml` replaces `docker-compose.inventory.yml`.
It defines three PostgreSQL instances, three migration containers and three service
processes; only Telescreen publishes a host port. Secrets are placeholders only.

## 15. Static gates (R7)

These are isolated-process import tests:

- `telescreen.inventory_service` and `telescreen.notification_service` (CLI, app, worker)
  import none of `telescreen.wiring*`, `issues`, `collectors`, `workflow`, `alerts`,
  `police`, `search` or `directory` (main-table adapters).
- Inventory imports no `telescreen.notifications` implementation.
- Notification imports no `telescreen.inventory*`.
- A split-mode Telescreen container imports no `telescreen.inventory` business modules
  and no `telescreen.notifications` delivery implementation (sender, delivery, email, slack,
  tester, retention, preferences, repository, templates service). The shared outbound guard
  modules `telescreen.notifications.dlp` and `telescreen.notifications.guard` remain allowed
  because GitHub outbound requests use them in split mode.
- The Police SDK still imports no `telescreen.*`.

## 16. Data safety

Replicas, outboxes, wire messages, logs and exports hold only masked text, enums, ids,
refs, keyed HMACs, existing SHA-256 token verifiers and count-only
`producer_redaction_hits`. Raw detected values, raw item keys
and source content never enter them. The leak matrix adds every new table, every outbox
payload and every captured HTTP body in the multi-process tests, using runtime-assembled
sample secrets embedded in issue titles, workflow inline text, comments and asset
locators. HTTP errors stay value-free.

## 17. Verification and decomposition

Multi-process TCP tests start three uvicorn processes on three SQLite files, with
PostgreSQL variants marked `integration`. They cover:

- bearer and API-token auth per service;
- collection -> inventory;
- issue link push;
- directory replication and revocation;
- overdue -> notification -> fake SMTP/Slack transport;
- issue, workflow and alert notifications;
- settings and template APIs through the proxy;
- outage/replay in each direction: Telescreen stopped for R1; Notification stopped while
  producers commit; Inventory stopped while links queue.

The single-process suite must stay green. Commands: `mise run ci`, `mise run ddl-check`,
`mise run web-ci`, `uv build`, `uv run pytest -q tests/inventory_service
tests/notification_service tests/split tests/leak`.

**Run 8 acceptance flows (svc-p08 owner resolution).** These are offline tests that run in
the default `uv run pytest`. They reuse the `_port`/`_start`/`_ready` harness of
`tests/inventory_service/test_process.py`: ephemeral ports, bounded readiness and
receipt polling with finite timeouts, and every process terminated in `finally`. They
live in `tests/split/` and `tests/leak/`, one module per topic, each under 1000 lines.
Stopping a process means terminating it and starting a new process on the same SQLite
file and port settings. Exactly-once is asserted from stored state, not from logs.
Each test waits for readiness after every (re)start and, before stopping a producer,
waits until its outboxes are drained (the replica waits do this for Telescreen, whose
worker drives delivery). Concurrent directory-stream deliveries can arrive out of order;
the inbox answers GAP (recorded as `conflict`) and the task retries about 5 s later. This
is expected behavior, not a defect (svc-p08 owner resolution 2). Flow (b) passes at
`f2c6707` (`tests/split/test_stopped_telescreen_process.py`) and flow (a) at `3190903`
(`tests/split/test_replay_process.py`). Flow (c)
(`tests/split/test_cutover_process.py`) and the leak matrix
(`tests/leak/test_leak_matrix_split.py`) pass at `66531bd` with no production change.
Run 11 changes no code: it re-runs the final gates (svc-p08 owner resolution 4). The
no-skip-marker check is phrased without the test-runner name, for example
`! grep -nE 'mark\.skip|\.skip\(|skipif' <files>`, so the progress gate does not
classify it as a zero-count test run; every real test command keeps its passed count.

- (a) **Producer outage/replay.** Telescreen -> Inventory: stop Inventory, commit an
  issue change that produces an `IssueLinkState`, restart, run the engine, and assert
  that one `inventory_issue_links` row is at the published sequence and the outbox row
  is delivered. Telescreen -> Notification: stop Notification, commit a producer
  notification (for example an issue comment), restart, and assert one
  `notification_inbox` row per `event_id` and one notification row per deterministic
  id. A redelivery of the same command returns `DUPLICATE` and adds no rows.
- (b) **Telescreen stopped (R1, R5).** After resync, terminate the Telescreen API and
  worker. Inventory still answers `/api/data/*` with a replica-authenticated API token
  (called directly with the service-auth headers its routes need). An overdue asset
  makes Inventory publish an `inventory_notification_outbox` command, which Notification
  applies (inbox receipt plus `data_overdue` notification row) and delivers to the fake
  transport, with no Telescreen process running.
- (c) **Cutover delivery and dedup.** Seed the Inventory and Notification directory
  replicas with `projections resync` before stopping writers and copying notification
  state (section 13 steps 4-5); the copy preserves `notification_directory_entries`, so
  the Notification pump cannot fail the copied member row with `recipient_unknown`.
  The entry points are `telescreen notification-store copy/verify --writers-stopped`
  (section 13 step 5) and the Notification inbox check
  against `retention_notification_receipts`. `NotificationStatus` has no `pending` value:
  an unsent monolith row is `queued`. The copied unsent row is seeded with
  `status='queued'` and the handle-based member reference `member:alice` that senders
  store. It is delivered exactly once by the Notification worker after cutover (one
  fake-transport delivery, row `sent`). The seeded retention obligation id equals the
  copied receipt id (section 8: `event_id` = obligation id), so the Inventory retention
  command returns `DUPLICATE` and creates no second notification row.

**Leak matrix (section 16).** The runtime-assembled raw samples travel through issue
titles, comments, workflow inline text, asset locators and context fields. The scan then
covers: every table of each store, enumerated from `db/ownership.py`
(`tables_for(Store.MAIN|INVENTORY|NOTIFICATION)`) rather than a hardcoded list; every
outbox and inbox payload column; every HTTP request and response body captured by the
tests; all process logs; and `telescreen-inventory audit export` and
`telescreen-notification audit export` output. Telescreen has no audit export command, so
its chain is covered by the main-store table scan and `/api/audit/search`. No raw sample
may appear anywhere, either raw or JSON-escaped.

Non-vacuity does not rely on `ProducerRedactionHits`: content is redacted before a
producer builds its command, so a zero producer count is correct. The test instead
proves that the sample entered the system and was handled. The ingest/scan request that
carries the runtime sample returns 200. A finding or issue exists that holds only the
masked representation. Where a path keeps counts (the collection run `findings_count`
or the notification `dlp_hit_count`), the count is positive.

Plan shape (`svc-pNN`, disjoint writePaths). The plan files (archived in
`impl-plans/completed/`) and the final dispatch manifest
(`impl-plans/completed/dispatch/telescreen-svc-20261016-g11-dispatch.json`, superseding
g10 with the same svc-p08 writePaths) are authoritative for writePaths. p01-p07
are integration-accepted and are recorded as accepted dependencies at checkpoint
`66531bd`; they are not redispatched. The remaining work runs in waves:

1. p08 (re-verify only, as specified by svc-p08 owner resolution 4: the 3x process
   suite and the final gates on the unchanged checkpoint source).
2. Serial finalization. It archives the svc plans and the g1-g11 manifests to
   `impl-plans/completed/`, and updates `impl-plans/README.md`, `README.md` and the
   status lines of this design and its related specs. It also applies the Nodetto
   layout fix (section 12), which is not yet applied at `66531bd` (the Nodetto JSONL
   was last changed in run 1), because the Nodetto document and `tests/db/**` are paths
   of the accepted p02. `impl-plans/active/inventory-alert-boundaries.md` is not a svc
   plan and stays where it is.

The critical path is p01 -> p02 -> p07 -> p04/p05 -> p08. Each plan owns the shared
exact-count/inventory tests that its change breaks.

| Plan | Content | Depends on |
| --- | --- | --- |
| p01 foundation (accepted) | ports codecs (4.1-4.3, including the validated `ProducerRedactionHits`/`ContextFieldHits` fields) and the `NotificationReceipt.pending`/`NotificationRequest.id_seed` fields, template imports in `notifications/templates/render.py` and `validate.py`, `core/vocabulary.py` additions (new engine task kinds), `Store.NOTIFICATION` declarations, config/topology validation, `core/templating.py`, `core/ownership_match.py`, `pyproject` script/wheel, README split-mode section (sole owner of root files). Constraints for later plans: `IdentityEntity` uses `identity_kind`; `NotificationContext` forbids extra fields | - |
| p02 db | Nodetto, exports, models, `0010`, owner histories (including `notification_0001`), adoption, ownership/model/Nodetto/migration exact-count tests (74 tables at head), the inventory model import gate, and inventory transfer v2 (`db/transfer*.py`, section 13 step 4), because `0010`/`inventory_0002` break transfer v1 | p01 |
| p03 telescreen publishers | change markers (including the two ORM-delete conversions in `directory/`), single-flight lock-before-read relay with deletion by marker id, resync command module, outbox adapters with redaction-hit counting, notification outbox delivery/recovery, HTTP clients for 4.4. Tests: two relay runs interleaved with an API-token revoke leave the replica revoked, and the same holds for an issue-link status change (deterministic SQLite interleaving; PostgreSQL variant marked `integration`); a marker committed mid-run is republished | p01, p02 |
| p07 shared service runtime | shared directory replica store/apply (`telescreen/replica`), replica authenticator (section 7) with parity tests, chain-parameterized audit (`audit/` service, chain, verify, query, export) used by sections 8.3 and 11 | p01, p02 |
| p04 inventory | replica-backed lookups and auth binding, issue links, local audit drain, retention dispatcher and notification outbox, removal of callbacks; `notification-store` copy/verify (`db/notification_transfer.py`, reusing p02's transfer helpers) | p01, p02, p07 |
| p05 notification service | package, inbox, replica binding, delivery pump, routes, audit CLI, identity-through-ports changes in `notifications/`, `DlpGate` `prior_hits` fold in `notifications/dlp.py` and `sender.py`. Test: DLP parity: the same raw sample secret in a context field, an inline body and a template-rendered field gives the same row verdict, hit_count > 0 and audit action under MASK and BLOCK, single-process vs the split adapter + inbox path in process | p01, p02, p07 |
| p06 telescreen split composition | `wiring*.py`, proxies, integrations, weekly move, `cli.py` registration and dispatch of every new Telescreen command (`telescreen notification-store` is parsed by p04's `cli_inventory_store.add_commands` and must be routed to `cli_inventory_store.run_notification` (the `inventory-store` branch at `cli.py:337` calls `run`); at `a5392d9` it falls through to exit 2. The p06 CLI test asserts that `notification-store verify` reaches `run_notification`), no-op delivery handler | p01, p02, p03 |
| p08 verification and deployment | multi-process and PostgreSQL tests (including a multi-process DLP parity case under `TELESCREEN_DLP_POLICY=block`), restricted-login grants, leak matrix, import gates, compose overlay (names fixed by sections 9 and 14), examples tests, command.md, runbook (plan archiving is serial finalization) | p04, p05, p06 |
