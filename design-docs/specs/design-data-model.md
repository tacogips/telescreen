# Data Model

PostgreSQL 16 schema for the Reporting Bureau (SQLAlchemy 2 async, `MappedAsDataclass`,
Alembic). The same models run on SQLite for offline tests.

## Overview

- One Alembic revision `0001_initial` creates every table in v1. Migrations live in
  `src/telescreen/db/migrations/` and are run programmatically by `telescreen migrate`
  (no root `alembic.ini`).
- No column ever holds a raw detected value or monitored content. Columns that carry
  user-visible text derived from monitored data are named `masked_*` or `display_*` and are
  produced by the masking pipeline.
- JSON documents are `Text` columns containing canonical JSON (`*_json` suffix), parsed into
  typed dataclasses in repositories.
- Common columns: `id` (UUID4 string PK), `created_at`, `updated_at` (UTC) unless noted.

## Details

### People and recipients

| Table | Columns | Notes |
|-------|---------|-------|
| `members` | id, handle (unique), display_name, email, slack_user_id, active | `email`/`slack_user_id` are operator-entered contact data, not detected data |
| `teams` | id, slug (unique), name, email (mailing list, nullable), slack_channel (nullable) | A team is the first-line/escalation responder group; `email` is the mailing list |
| `team_memberships` | team_id, member_id, PK(team_id, member_id) | |
| `api_tokens` | id, label, token_hash (sha256 hex, unique), scopes (comma list), member_id nullable, created_at, revoked_at | Token shown once at creation |
| `counters` | name PK, value | `issue_number` counter |

Recipient references (used in workflows, escalation policies and notifications) are strings:
`member:<handle>`, `team:<slug>`, `email:<address>`, `slack:<channel-id>`.

### Collectors and scans

| Table | Columns | Notes |
|-------|---------|-------|
| `collector_configs` | id, name (unique), kind (`slack`, `gdrive`, `gdocs`, `gsheets`, `github`, `custom`, `police`), trigger_mode (`event`, `schedule`, `both`), cron nullable, sweep_cron nullable, sweep_max_concurrency (default 16), selector_json, credential_ref nullable, cursor_json nullable, enabled, last_scheduled_at | Selector is per-kind typed (see collectors design); `credential_ref` is a secret ref |
| `scan_jobs` | id, collector_config_id nullable, source, trigger (`event`, `schedule`, `push`, `manual`, `workflow`), status (`running`, `succeeded`, `failed`, `partial`), started_at, finished_at, resources_scanned, resources_skipped, findings_count, error_code nullable | No content, no raw locators |
| `replay_nonces` | scope, nonce, expires_at, PK(scope, nonce) | Webhook/callback replay protection |

### Findings and issues

| Table | Columns | Notes |
|-------|---------|-------|
| `issues` | id, number (unique), grouping_key (unique), source, resource_key, display_locator, resource_ref_json nullable, collector_config_id nullable, display_title, rule_class, severity, status, assignee_member_id nullable, assignee_team_id nullable, first_seen, last_seen, last_checked_at, sweep_covered (bool), times_seen, distinct_fingerprints, regression_count, resolved_at nullable, resolution (`manual`, `auto`) nullable, INDEX(collector_config_id, status, last_checked_at) | `last_seen` is the last finding (Sentry semantics). `last_checked_at` is the last time any scan (complete, incomplete or skipped) covered the resource. `sweep_covered` is the adapter's statement from the latest scan that a full sweep of the config enumerates this resource. Both are used only by sweep removal reconciliation |
| `findings` | id, issue_id, scan_job_id nullable, source, resource_key, display_locator, location (masked), rule_id, rule_class, detector, confidence, masked_snippet, fingerprint, fingerprint_key_id, observed_at, UNIQUE(issue_id, scan_job_id, fingerprint) | Sentry "event"; one row per observation per scan |
| `issue_history` | id, issue_id, kind, actor_ref, from_value, to_value, note (masked), at | kinds: `created`, `status_changed`, `assigned`, `commented`, `regressed`, `auto_resolved`, `workflow_started`, `evidence_added`, `sla_breached` |

Keys:

- `resource_key = HMAC(key, "resource\0" + source + "\0" + raw_locator)` (hex).
- `grouping_key = sha256(source + "\0" + resource_key + "\0" + rule_class)` (hex).
- `fingerprint = HMAC(key, "v1\0" + rule_id + "\0" + normalized_value)` (hex).
- `raw_locator` formats per collector kind are fixed in `design-collectors-and-ingestion.md` (Resource
  identity). They never contain commit shas or segment positions.
- `display_locator` is the raw locator after Masking redaction.
- `resource_ref_json` is the collector adapter's opaque resource reference (for example Drive file
  id, Slack channel and ts, GitHub repo/ref/path) used for rescans. It is stored only if
  `Redactor` finds no hit in it; otherwise it is null and the issue cannot be rescanned by a
  `detect` step (outcome `error`).

Issue status machine (`IssueStatus` StrEnum):

| From | Allowed to (user) | System transitions |
|------|-------------------|--------------------|
| unresolved | acknowledged, in_progress, resolved, ignored | resolved (auto) |
| acknowledged | in_progress, resolved, ignored, unresolved | resolved (auto) |
| in_progress | resolved, ignored, unresolved | resolved (auto) |
| regressed | acknowledged, in_progress, resolved, ignored | resolved (auto) |
| resolved | unresolved (reopen) | regressed (new finding) |
| ignored | unresolved | none (new findings only bump counters) |

### Workflows

| Table | Columns | Notes |
|-------|---------|-------|
| `workflow_definitions` | id, name, version, source_format (`yaml`, `json`), source_text, compiled_json, active, created_by, created_at, UNIQUE(name, version) | `source_text` is rejected at upload if RuleDetector finds anything |
| `escalation_policies` | id, name (unique), tiers_json, created_at, updated_at | Tier list, see workflow design |
| `workflow_runs` | id, definition_id, issue_id nullable, parent_step_run_id nullable, status (`running`, `waiting`, `succeeded`, `failed`, `cancelled`), trigger (`issue_created`, `issue_regressed`, `manual`, `sub_workflow`), context_json, lock_version, started_at, finished_at | `context_json` holds masked step outputs only |
| `step_runs` | id, run_id, step_id, visit (1-based count of entries into this step), branch_token nullable, status (`ready`, `running`, `waiting`, `succeeded`, `failed`, `cancelled`), attempt, input_json, output_json nullable, outcome nullable, error_code nullable, started_at, finished_at | UNIQUE(run_id, step_id, visit, branch_token) |
| `transitions` | id, run_id, from_step_run_id nullable, to_step_id, kind (`start`, `next`, `branch`, `send_back`, `fork`, `join`, `timeout`, `error`), reason (masked) nullable, at | Run history for the UI |
| `engine_tasks` | id, kind, ref_id, payload_json, status (`ready`, `leased`, `done`, `cancelled`, `dead`), available_at, lease_owner nullable, lease_expires_at nullable, attempts, max_attempts, idempotency_key (unique), last_error_code nullable, created_at | The durable queue; timers are tasks with future `available_at` |
| `human_tasks` | id, run_id, step_run_id (unique), kind (`approval`, `evidence`), title (masked), assignee_ref, escalation_policy_id nullable, tier_index, due_at nullable, status (`open`, `completed`, `cancelled`), outcome nullable, comment (masked) nullable, completed_by nullable, completed_at nullable | |
| `map_jobs`, `map_partitions`, `map_items` | see `design-map-reduce-sweeps.md` (Data model) | Concurrent map-reduce sweeps; UNIQUE(map_job_id, shard_key), INDEX(map_job_id, status) |

Engine task kinds: `advance_step`, `timer_fired`, `sla_check`, `police_timeout`,
`collector_scan`, `police_push_delivery`, `notification_delivery`, `child_run_finished`,
`collector_watch_renew`, and the map-reduce kinds `map_partition`, `map_dispatch`,
`map_shard`, `map_reduce` (`design-map-reduce-sweeps.md`). External side effects (notification sends, police push deliveries) only
happen in their own delivery tasks, never inside `advance_step` or `sla_check`.

### Notifications, evidence, police

| Table | Columns | Notes |
|-------|---------|-------|
| `notifications` | id, channel (`slack`, `email`), recipient_ref, subject (masked), body (masked), dlp_verdict (`pass`, `masked`, `blocked`), dlp_hit_count, status (`queued`, `sent`, `failed`, `blocked`), delivery_attempts, run_id nullable, issue_id nullable, created_at, sent_at nullable | `recipient_ref` is the reference string, not the resolved address. Row and audit are written in the requester's transaction; delivery happens later in a `notification_delivery` task |
| `evidence` | id, issue_id, run_id nullable, step_run_id nullable, display_filename, content_type, size_bytes, sha256 nullable, storage_key nullable, verdict (`accepted`, `rejected`), rejection_rule_classes, uploaded_by, created_at | Rejected rows have no `sha256`/`storage_key`; content was discarded |
| `police_workers` | id, name, version, mode (`pull`, `push`), contract_version, task_types, labels, concurrency, endpoint_url nullable, token_hash nullable, status (`active`, `disabled`), last_heartbeat_at nullable, last_delivery_at nullable, created_at | Push signing secret is derived, never stored (police design) |
| `police_tasks` | id, task_type, labels, input_json, status (`queued`, `leased`, `delivered`, `completed`, `failed`, `expired`, `cancelled`), worker_id nullable, attempt, max_attempts, lease_expires_at nullable, deadline, callback_token_hash nullable, callback_token_used_at nullable, result_json nullable, outcome nullable, step_run_id nullable, collector_config_id nullable, created_at, completed_at | |

### Audit

| Table | Columns | Notes |
|-------|---------|-------|
| `audit_records` | seq (integer PK, gapless), id, at, actor_kind (`member`, `token`, `system`, `police`, `collector`), actor_ref, action, target_kind, target_id, summary (masked), details_json (masked), prev_hash, hash | Append-only |
| `audit_chain_head` | id (always 1), seq, hash | Locked `FOR UPDATE` by each append |

Hash chain: `hash = sha256(prev_hash + "\n" + canonical_json(seq, id, at, actor_kind,
actor_ref, action, target_kind, target_id, summary, details_json))`; the genesis
`prev_hash` is 64 zeros. Appends happen in the same transaction as the audited change. The
repository exposes no update or delete. On PostgreSQL the migration installs a trigger that
raises on `UPDATE` or `DELETE` of `audit_records` (integration-tested). Verification
recomputes the chain and reports the first broken `seq`.

Audited actions (minimum): issue status/assignment changes, workflow definition upload,
run start/finish/cancel, step transitions including send-back, human task completion,
SLA escalation, notification sent/blocked, evidence accepted/rejected, police registration,
police result accepted/rejected, index guard rejection, token creation/revocation, collector
config changes.

### v1.1 additions (revision `0002_v1_1`)

Behavior is in `design-v1-1-dspm-adoption.md`. One new Alembic revision, `0002_v1_1`
(`down_revision = "0001_initial"`), adds everything below. `0001_initial` is never edited.

`0001_initial` builds its tables from the current ORM metadata. On a fresh database it
therefore already creates every v1.1 table and column, while a database migrated under v1
lacks them. `0002_v1_1` must be idempotent: it inspects the live schema and creates only
the missing tables, columns and indexes. It then runs the backfills
(`base_severity = severity` where null, `fingerprint_key_version = 1` where null).
Downgrade drops the v1.1 additions that exist.
Enum-like columns stay `String(32)`, so new `IssueStatus`, `IssueResolution`,
`IssueHistoryKind`, `HumanTaskKind` and `EngineTaskKind` values need no DDL. New tables and
columns hold only enums, counts, timestamps, ids, keyed references (HMAC hex), operator
configuration and redacted text. They never hold raw values, file names, paths, titles or
emails.

New columns on existing tables:

| Table | Columns | Backfill / notes |
|-------|---------|------------------|
| `issues` | `base_severity` String(32) not null | `= severity`. From v1.1, `severity` is the effective (exposure-floored) severity |
| `issues` | `exposure_visibility` String(32) not null default `unknown`, `exposure_json` Text nullable, `exposure_changed_at` nullable | Canonical JSON of `Exposure` (enums and counts only). INDEX(`exposure_visibility`) |
| `issues` | `placement_keys_json` Text nullable, `resource_modified_at` nullable | JSON array of `"<placement kind>:<hex>"` keyed references |
| `issues` | `owner_ref` String(80) nullable | `"<identity kind>:<hex>"`; never a raw id or email |
| `issues` | `policy_name` String(64) nullable, `policy_version` Integer nullable | The policy that made the last decision |
| `issues` | `risk_score` Integer not null default 0, `asset_tier` String(32) nullable | INDEX(`status`, `risk_score`). Recomputed by the first maintenance tick |
| `findings` | `fingerprint_key_version` Integer not null server default 1 | INDEX `ix_findings_key_fingerprint` (`fingerprint_key_version`, `fingerprint`) |
| `teams` | `lead_member_id` String(36) nullable | Exception approver |
| `police_workers` | `health_alerted_at` nullable | Open `police_silent` alert marker |

New tables:

| Table | Owner | Columns | Notes |
|-------|-------|---------|-------|
| `placement_policies` | issues | id, name, version, source_format, source_text, compiled_json, effect, enabled, active, owner_team nullable, created_by, created_at, UNIQUE(name, version) | Like `workflow_definitions`: `source_text` is rejected at upload if the RuleDetector finds anything; `rationale` inside it is redacted. At most one active version per name |
| `issue_exceptions` | issues | id, issue_id, reason (redacted), duration, status, requested_by, approver_ref nullable, decided_by nullable, decided_at nullable, expires_at nullable, created_at, INDEX(issue_id), INDEX(status, expires_at) | At most one `requested` or `active` row per issue (checked in the service under the issue row lock) |
| `member_identities` | directory | id, member_id, kind, identity_key (64 hex), origin, created_at, UNIQUE(kind, identity_key) | Keyed only; derived rows follow `members.email` / `slack_user_id` |
| `resource_coverage` | collectors | id, collector_config_id, resource_key, source, state, reason nullable, truncated_bytes, exposure_visibility, first_seen_at, last_attempt_at, UNIQUE(collector_config_id, resource_key) | Only resources whose latest state is not `scanned`; no locator or title |
| `scan_coverage` | collectors | scan_job_id, state, reason (empty string when none), count, PK(scan_job_id, state, reason) | Counts per scan job |
| `collector_health` | collectors | collector_config_id PK, last_success_at, last_failure_at, consecutive_failures, last_event_at, watch_expires_at, open_conditions (comma list), alerted_at, updated_at | One row per config, created on first job or event |

Issue status machine from v1.1: the user transitions, record-path transitions and resolution
reasons in `design-v1-1-dspm-adoption.md` section 5 replace the v1 table above. Status
groups `OPEN_STATUSES` and `RECONCILED_STATUSES` are defined in `telescreen.core.vocabulary`.

Keys from v1.1 (`Fingerprinter` with an identity key and an active key; version 1 is the
identity key):

- `resource_key`, `grouping_key`, `placement_key = HMAC(identity, "placement\0" + kind +
  "\0" + value)` and `owner identity_key = HMAC(identity, "identity\0" + kind + "\0" +
  normalized)` always use version 1.
- `fingerprint` and external re-keying use the active version, recorded in
  `findings.fingerprint_key_version`.

Audited actions added: `policy.created`, `policy.activated`, `policy.disabled`,
`issue.remediation_requested`, `issue.verified`, `issue.resolved` (with reason),
`exception.requested`, `exception.approved`, `exception.rejected`, `exception.revoked`,
`exception.expired`, `identity.added`, `identity.removed`, `team.lead_changed`,
`health.alert_opened`.

### v1.2 additions (revision `0003_v1_2`)

Behavior is in `design-v1-2-notifications-and-identity.md`. One new revision, `0003_v1_2`
(`down_revision = "0002_v1_1"`), adds everything below. `0001_initial` and `0002_v1_1`
are never edited. Because `0001_initial` builds tables from the current ORM metadata,
`0003_v1_2` is idempotent like `0002_v1_1`: it inspects the live schema, creates only the
missing tables, columns and indexes, and then runs the role backfill. Downgrade drops the
v1.2 additions that exist. Enum-like columns stay `String(32)` or shorter.

New table (owner: notifications):

| Table | Columns | Notes |
|-------|---------|-------|
| `notification_templates` | id, name String(64), version Integer, event_kind String(32), description String(200) nullable, subject Text, body Text, email_subject Text nullable, email_body Text nullable, slack_body Text nullable, active Boolean, created_by String(80), created_at, UNIQUE(name, version), INDEX(name, active) | Built-ins (`system.<event>`, version 0) live in code, not rows. All rows of a name share `event_kind`. At most one active row per name (checked in the service under a row lock on the name's rows). Text is admin-entered and rejected when `Redactor` finds a hit |

New columns:

| Table | Columns | Backfill / notes |
|-------|---------|------------------|
| `notifications` | `event_kind` String(32) nullable, `template_name` String(64) nullable, `template_version` Integer nullable, `set_name` String(64) nullable, `target_index` Integer nullable, `error_code` String(32) nullable | Null for v1/v1.1 rows. `status` gains `skipped` (error code `recipient_unknown` or `recipient_unreachable`); `failed` rows now also record `error_code` |
| `members` | `role` String(16) nullable, `last_sign_in_at` nullable | Backfill: the highest scope among the member's non-revoked bound `api_tokens` (`admin` -> `admin`, `write` -> `responder`, `read` -> `viewer`), else null. INDEX(`role`, `active`) |
| `team_memberships` | `origin` String(16) not null server default `manual` | `idp` rows are added and removed only by OIDC team sync |

`member_identities` needs no DDL. New kind `oidc_subject` with origin `oidc`, where
`identity_key = HMAC(identity key v1, "identity\0oidc_subject\0" + issuer + "\0" + sub)`.
The UNIQUE(kind, identity_key) constraint makes one `(issuer, sub)` map to one member, and
the service allows at most one `oidc_subject` per member per issuer.

Recipient references gain `slack_channel:<channel id>`; `slack:` stays as an alias.

Audited actions added: `template.created`, `template.activated`, `template.rejected`,
`notification.skipped`, `notification.set_missing`, `role.changed`, `member.provisioned`,
`identity.linked`, `team_membership.synced`, `auth.sign_in`, `auth.sign_in_denied`.
`token.created` and `token.revoked` now carry the calling principal's actor ref. No audit
detail holds a token, `sub`, claim value or template text.

### v1.3 additions (revision `0004_v1_3`)

Status: implemented 2026-10-13.

Behavior is in `design-v1-3-sentry-parity.md` (section 13.1 lists every column). One new
revision, `0004_v1_3` (`down_revision = "0003_v1_2"`), adds everything below and is
idempotent in the same way as `0003_v1_2`. Earlier revisions are never edited.

Issue status machine: `IssueStatus` adds `archived` and `escalating`.

- The stream's open set is `UNRESOLVED_STATUSES` (unresolved, acknowledged, in_progress,
  regressed, escalating).
- The internal `OPEN_STATUSES` and `RECONCILED_STATUSES` add `archived` and `escalating`.
- User transitions add `escalating -> acknowledged | in_progress | unresolved` and
  `archived -> unresolved`.
- `archived` is entered only through the archive route. It leaves through:
  - its condition (-> `escalating`);
  - widened exposure (-> `escalating`);
  - `archive_until` (-> `unresolved`);
  - unarchive;
  - verification, resolution, an exception approval or policy `allowed`.

  Every exit clears the archive columns. `archive_until` is never null while archived and
  is at most 90 days after `archived_at`. `ignored` remains reachable only through an
  approved exception.

New columns:

| Table | Columns | Backfill / notes |
|-------|---------|------------------|
| `issues` | `priority` String(16) not null server default `medium`, `priority_manual` Boolean default false, `needs_review` Boolean default false, `merged_into_id` String(36) nullable INDEX, `archive_kind` String(32), `archive_until`, `archive_threshold` Integer, `archive_baseline` Integer, `archived_at` (all nullable) | `priority` from severity (`critical`/`high` -> `high`, `medium` -> `medium`). `needs_review` true for `unresolved` and `regressed`. INDEX(`status`, `merged_into_id`) |
| `notifications` | `category` String(32) nullable | Null for earlier rows. `skipped` rows may carry `recipient_opted_out` |

New tables. Owner `issues`:

- `saved_views`, `saved_view_stars`, `saved_view_defaults`;
- `issue_comments`, `issue_mentions`, `issue_subscriptions`, `issue_bookmarks`;
- `external_issue_links`, `issue_merges`, `ownership_rules`.

Owner `alerts`: `alert_rules`, `alert_fires`. Owner `notifications`:
`notification_settings`. Owner `integrations`: `integration_checks`.

Text columns that hold user or admin input (view name and query, rule name, ownership
pattern, link title and URL) are `Redactor`-checked and rejected on a hit. Comment bodies
are redacted before storage. `filters_json` and `actions_json` are canonical JSON of typed
dataclasses.

Audited actions added:

- issues: `issue.archived`, `issue.unarchived`, `issue.escalated`, `issue.priority_changed`,
  `issue.reviewed`, `issue.merged`, `issue.unmerged`, `issue.bulk`;
- comments: `comment.edited`, `comment.deleted`;
- views: `view.created`, `view.updated`, `view.deleted`;
- external issues: `external_issue.linked`, `external_issue.unlinked`,
  `external_issue.created`, `external_issue.blocked`;
- alerts: `alert_rule.created`, `alert_rule.updated`, `alert_rule.deleted`,
  `alert_rule.muted`, `alert_rule.unmuted`;
- members and organization: `member.updated`, `member.deactivated`, `member.reactivated`,
  `ownership_rule.changed`, `integration.tested`.

Details hold ids, enums, counts and field names only.

### v1.4 additions (revision `0005_v1_4`)

Status: implemented 2026-10-14. Behavior is in `design-v1-4-data-inventory.md` (section 4
lists every column). The Nodetto document is changed first. Then one new revision,
`0005_v1_4` (`down_revision = "0004_v1_3"`), adds everything below. It is idempotent in the
same way as `0004_v1_3`, and earlier revisions are never edited.

New tables (owner `inventory`, frame "Data inventory"):

| Table | Purpose |
|-------|---------|
| `data_assets` | The register: one row per `resource_key` (discovered, or registered with `resource_key("registered:" + source, locator)`); `number` shown as `DATA-<n>` from the counter `data_asset_number`; origin, kind, state (`present`, `deleted`, `unknown`), redacted name and locator, item count, first/last seen, deleted at, `overdue_notified_for` |
| `data_asset_events` | Append-only lifecycle events with item and value counts, metadata changes (field key, old and new display text) and notes. A PostgreSQL trigger rejects `UPDATE` and `DELETE` |
| `data_asset_items` | Keyed item fingerprints (`kind = item`) and finding fingerprints (`kind = value`) per asset, with `first_seen_at` and `removed_at`; UNIQUE (`asset_id`, `kind`, `fingerprint`) |
| `data_fields` | Admin field schema: immutable `key`, label, type, options, required, position, role, archived |
| `data_field_values` | One canonical JSON value per (asset, field); `value_date` for date fields |

New column: `collector_configs.inventory_mode` varchar(16), not null, server default
`sensitive_only`.

Item fingerprints are `HMAC(HMAC(identity key v1, "telescreen:data-item:v1"), resource_key
+ "\0" + raw_item_key)`. Raw item keys are never stored. Audited actions added:

- `data_asset.registered`, `data_asset.updated`, `data_asset.fields_changed`;
- `data_asset.event_recorded`, `data_asset.marked_deleted`, `data_asset.restored`;
- `data_asset.exported`, `data_asset.overdue_notified`;
- `data_field.created`, `data_field.updated`, `data_field.reordered`;
- `data_field.archived`, `data_field.unarchived`, `data_field.deleted`.

### Schema source of truth (Nodetto)

Since v1.3 the DDL is designed in the Nodetto desktop app. The ERD document
`db/schema/telescreen.nodetto.jsonl` (one page, tables grouped into frames by
model module) is authoritative; `db/schema/telescreen.postgresql.sql` and
`telescreen.sqlite.sql` are its `nodetto-cli ddl` exports. SQLAlchemy models and
Alembic revisions remain the runtime mechanism, and
`tests/db/test_nodetto_schema.py` requires that the models match the document
exactly (types, nullability, defaults, primary keys, identity columns, unique
keys, index names) and that the SQLite export builds the same structure as
`alembic upgrade head`. Table-to-table references are drawn field to field as
Nodetto logical relations (dashed, no database constraint); the test checks
that each joins same-typed columns to a primary or unique key. Triggers and seed rows are outside Nodetto's ERD model
and stay in Alembic code. The change workflow is in `db/schema/README.md`.

## References

See `design-docs/references/README.md`.
