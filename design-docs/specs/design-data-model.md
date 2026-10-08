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

## References

See `design-docs/references/README.md`.
