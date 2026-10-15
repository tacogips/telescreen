# Service split: Telescreen, Inventory and Notification (requirements)

Status: ACCEPTED REQUIREMENTS, 2026-10-15 (owner investigation and decisions). The
design author's evidence corrections C1-C9 are recorded in
[design-service-split.md](design-service-split.md) section 2.

## Overview

The owner asked (translated): "Is data inventory loosely coupled with Telescreen's
collection and notification modules? Make each of them deployable as a microservice.
Data inventory uses the notification module, and Telescreen uses the notification
module. Telescreen (data collection) and data inventory are also loosely coupled:
Telescreen reads data from inventory and registers data into inventory."

This document records the current state, the target topology and the owner's
decisions. The design author must verify the evidence against the code, correct it
where wrong, and write the design in `design-service-split.md`.

## 1. Current state (evidence)

The inventory extraction (`design-inventory-microservice.md`, `bde11e7`) already runs
inventory as `telescreen-inventory serve` with its own database (10 tables in
`db/ownership.py`). Collection publishes observations through a durable outbox/inbox.
However, the dependency points the wrong way for the target:

| Inventory -> Telescreen call today | Where |
| --- | --- |
| Authenticate the user (bearer delegated to the Bureau) | `inventory_service/clients.py:32`, `/internal/inventory/v1/authenticate` |
| Open issues per asset (enrichment) | `clients.py:41`, `/issues` |
| Member/team display names | `clients.py:51`, `/actors` |
| Owner fallback from issue assignee | `clients.py:59`, `/assignee` |
| Audit delivery into the Bureau audit chain | `clients.py:91`, `/audit` |
| Retention obligation delivery; the Bureau sends the notification | `clients.py:104`, `/retention` |

Notification is not separable. It lives in the main process and shares the main store
(`notifications`, `notification_templates`, `notification_settings`,
`retention_notification_receipts`, `engine_tasks`):

- `ports/notifications.py:60,81`: `NotificationSender.send` and
  `MemberNotifier.send_to_member` take the caller's `AsyncSession` and queue inside
  the caller transaction. Callers: issues (activity, comments, mentions, digest,
  exceptions, verification), alerts evaluator, workflow executors (notify, human,
  SLA escalation, notify targets), collector and police health, API issue routes,
  inventory retention wiring.
- `notifications/preferences.py`, `weekly.py`, `member_send.py`, `retention.py` read
  `MemberRow`/`TeamRow` directly.
- `notifications/templates/render.py` and `validate.py` import `workflow.dsl`.
- Delivery uses the shared `engine_tasks` queue and worker.

## 2. Target topology

Three independently deployable services, each owning its own database:

| Service | Owns |
| --- | --- |
| Telescreen | Collection (collectors, police, sweeps), detection, issues, workflow, alerts, search, identity/directory (members, teams, roles, tokens), its own audit |
| Inventory | Asset register, lifecycle events, items, field schema and values, retention due dates, its own audit, read-only replicas it needs |
| Notification | Notification requests, templates, member notification preferences, DLP gate on outbound content, delivery (email, Slack), retry, deduplication receipts, its own audit, read-only replicas it needs |

Allowed runtime dependency directions (and no others):

- Telescreen -> Inventory: read assets and register data (observations, reconciliation,
  stop-tracking, issue link state, directory projection).
- Telescreen -> Notification: notification commands, directory projection.
- Inventory -> Notification: retention/overdue notification commands.
- Inventory and Notification never call Telescreen. Notification never calls Inventory
  or Telescreen business APIs.

The existing single-process composition (one process, optionally one database) stays
supported and is the default for small installations.

## 3. Owner decisions

- **D1 Authentication.** Every service verifies OIDC bearer tokens itself, using the
  existing identity verifier (`identity/verifier.py`, `jose.py`, discovery). Inventory
  no longer delegates authentication to Telescreen. Service-to-service calls keep a
  service credential. Permission decisions in Inventory and Notification use a
  member/role replica (D2), so role semantics stay identical to Telescreen's.
- **D2 Identity data.** Telescreen remains the identity owner. It publishes a versioned
  directory projection (members, teams, team memberships, roles, identity-to-member
  mapping, delivery addresses) through a durable outbox to Inventory and
  Notification, which keep read-only replicas. Owner validation and display names in
  Inventory, and recipient resolution in Notification, use those replicas. Replicas
  are eventually consistent; the design defines staleness behavior (for example an
  unknown ref is a validation error, a removed member is not a recipient).
- **D3 Issue links.** Telescreen pushes issue link state per resource key (issue id,
  short id, status, severity, assignee ref as needed for owner fallback) into
  Inventory through the existing ordered outbox/inbox. Inventory stores this
  projection and serves open issue counts and related issues from it. No raw values.
- **D4 Recipients.** Callers send member/team refs plus a notification event and
  allow-listed context. The Notification service owns preferences, templates, DLP,
  recipient resolution through its replica, delivery, retry and deduplication.
- **D5 Audit.** Each service owns the audit records for actions it performs, in an
  append-only verifiable chain in its own store. Inventory stops delivering audit
  commands to Telescreen. The design documents how an auditor obtains and verifies
  the combined trail (for example per-service audit APIs/exports). Existing main
  audit records are not rewritten.

## 4. Required behavior

- **R1** Inventory has no runtime call to Telescreen: the six calls in section 1 are
  replaced per D1-D5. Inventory starts, authenticates users, serves all `/api/data/*`
  routes with unchanged JSON/CSV and permissions, and sends overdue notifications
  while Telescreen is stopped (using its replicas).
- **R2** A Notification service runs as its own process (`telescreen-notification
  serve`, or an equivalent entry point) with its own database and migrations, and
  owns the notification tables, delivery queue/worker and channel credentials
  (SMTP, Slack). Telescreen and Inventory hold no SMTP/Slack credentials in split mode.
- **R3** All notification producers (section 1 list) publish versioned notification
  commands through a producer-local durable outbox committed in the producing
  transaction, delivered at least once and applied idempotently through a
  notification inbox. Notification failure never rolls back the producer. Behavior
  that today depends on the synchronous `NotificationReceipt` (ids, DLP verdict,
  skipped recipients, unreachable outcomes, workflow step results, test sends) is
  preserved or the change is explicitly designed and documented; test sends may use
  a synchronous authenticated call.
- **R4** Notification settings, templates and notification history remain available in
  the UI and API with unchanged permissions. In split mode Telescreen proxies or the
  UI calls the Notification API; template validation/rendering no longer imports
  `workflow.dsl` business code (move the template language to a shared pure library or
  the notification package).
- **R5** Inventory sends retention/overdue notifications directly to Notification. The
  current retention semantics are preserved: one obligation per asset, due field and
  value revision; eligibility check before commitment; skipped outcomes for cleared,
  deleted or superseded obligations; owner fallback through the D3 projection.
- **R6** Persistence ownership is explicit for three stores (`db/ownership.py` gains a
  notification store); each store has its own migration history, adoption and a
  stopped-writer cutover runbook for existing data, and restricted-login PostgreSQL
  tests prove cross-store SQL is denied.
- **R7** Static gates: Inventory and Notification executables import no Telescreen
  business composition; Telescreen imports no inventory or notification
  implementation in split mode; cross-service contracts live in `ports/` as dataclasses
  and enums with validated versioned codecs.
- **R8** Compose example: separate PostgreSQL instances and processes for the three
  services; only the Telescreen UI/API publishes a host port unless the design needs
  otherwise.

## 5. Binding constraints

- Raw detected values, raw item keys and source content are never persisted, logged,
  indexed, notified, exported, returned or put into any outbox, replica or wire
  message. Notification DLP stays on the outbound path.
- Telescreen stays read-only on monitored sources.
- Delivery between services is at least once with idempotent receipts; no in-memory
  callbacks or best-effort second commits; no distributed transactions; outages of
  any one service leave durable pending work that recovers.
- Existing public API responses, route permissions and UI behavior are unchanged
  except where this document requires a change.
- Strict typing, file-size and DDL rules from AGENTS.md apply. Existing Alembic
  revisions and owner baselines are immutable; add new revisions.

## 6. Out of scope

- Splitting collection from issues inside Telescreen.
- A message broker, service mesh or production deployment.
- Migrating production data (the runbook is required, executing it is not).

## 7. Acceptance

- Separate-process tests start the three services against three databases and
  exercise: SSO-equivalent bearer auth in each service, collection -> inventory
  observation, issue link push, directory replication, inventory overdue ->
  notification -> email/Slack fake, issue/workflow/alert notifications, settings and
  template APIs, and outage/replay in each direction.
- Inventory and Notification run their tests with Telescreen stopped where R1 requires.
- `mise run ci`, `mise run ddl-check`, web CI and package build pass; docs and the
  compose overlay are updated.
