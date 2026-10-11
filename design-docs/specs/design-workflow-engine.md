# Workflow DSL and Durable Engine

Self-built, PostgreSQL-backed workflow engine for AI-agent and incident-response workflows,
with human tasks, send-back, SLA escalation, evidence gates, police verification and timers.

## Overview

Workflows are YAML or JSON documents (`apiVersion: telescreen.workflow/v1`). They are
validated at upload, stored as immutable versions, and executed as runs whose every state
change (step runs, transitions, waits, timers) is persisted. Progress survives restarts
because all pending work is an `engine_tasks` row. Concepts follow riela (nodes,
manager/worker, branching, loops) but state lives in the database (see `notes.md`).

## Details

### Document schema (v1)

```yaml
apiVersion: telescreen.workflow/v1
kind: Workflow
name: github-credential-leak        # slug, unique per version series
description: text
trigger:                            # optional; omitted means manual start only
  on: [issue_created, issue_regressed]
  match: {sources: [github], rule_classes: [credential, token, private_key], min_severity: high}
defaults: {max_step_visits: 20, retry: {max_attempts: 3, backoff: PT10S}}
start: triage
steps:
  - {id: triage, type: human_task, ...}
```

A JSON Schema for the document is published at
`src/telescreen/workflow/dsl/workflow-v1.schema.json` (owned by workflow-dsl), served at
`GET /api/workflows/schema`, and used by the UI editor. The authoritative validator is the Python loader.

Common step fields: `id` (slug, unique), `type`, `name` (optional), `next` (step id; required
unless the type defines its own routing), `retry` (`max_attempts`, `backoff` ISO-8601
duration; only for executor types marked retryable), `on_error` (step id; default: run fails).

| Type | Fields | Routing | Waits |
|------|--------|---------|-------|
| `detect` | `target: issue_resource`, `detectors` (optional list) | `outcomes: {found, clean, error}` | no (the rescan runs inside the step task) |
| `ai_agent` | `provider: managed|local`, `model` (optional), `prompt` (template), `output: [{name, type: string|boolean|number|enum, values?}]` | `next` | no |
| `condition` | `cases: [{when: <expr>, next}]`, `default` | first matching case | no |
| `human_task` | `title` (template), `assignee` (recipient ref), `outcomes: {<name>: <step>}`, `sla` (duration, optional), `escalation_policy` (name, optional), `notify_on_assign: bool` | chosen outcome | yes |
| `notify` | `channel: slack|email`, `to: [recipient refs]`, `subject` (template), `body` (template) | `next` | no |
| `evidence_upload` | `title`, `assignee`, `min_files` (default 1), `sla`, `escalation_policy` | `next` | yes |
| `police_verify` | `task_type: verify_remediation|detect|custom:<name>`, `labels: [..]`, `input` (map of templates), `timeout` (duration) | `outcomes: {passed, failed, error}` | yes |
| `wait` | `duration` | `next` | yes (timer) |
| `send_back` | `to` (prior step id), `reason` (template), `max_send_backs` (default 3), `on_exhausted` (step id) | `to` or `on_exhausted` | no |
| `parallel` | `branches: [step ids]`, `join` (join step id) | forks all branches | no |
| `join` | `mode: all|any` | `next` | yes (until branches arrive) |
| `sub_workflow` | `workflow` (name), `version` (optional, default latest active) | `outcomes: {succeeded, failed}` | yes |
| `map` | `source.partition` (`via: collector|police`, `collector` config name), `map` (`via: collector|police`), `max_concurrency`, `shard_timeout`, `shard_max_attempts`, `failure`, `timeout` | `outcomes: {succeeded, partial, failed}` | yes (until the map job is reduced) |
| `end` | `result: succeeded|failed|cancelled` | terminates run | - |

Static validation at upload (errors list with JSON paths, no values): schema shape; unique
ids; `start` and every routing target exist; `send_back.to` is a step that can precede it;
every step reachable from `start`; `parallel.branches` reach the named `join` and no branch
reaches `end` without passing it; `human_task.outcomes` non-empty; expressions and
templates parse and reference only known roots; durations parse; `sub_workflow` references
an existing definition and `map` names an existing collector config of a compatible kind
(both checked by the API, not by the offline CLI); source text passes
`Redactor` (otherwise `raw_value_detected`).

### Expressions and templates

- Expressions (`condition.when`): hand-written tokenizer and recursive-descent parser, no
  `eval`. Grammar: literals (string, number, `true`, `false`, `null`), paths
  (`steps.<id>.output.<field>`, `steps.<id>.outcome`, `issue.<field>`, `run.<field>`),
  operators `== != < <= > >= and or not in`, parentheses, list literals for `in`. Unknown
  path evaluates to `null`. Type mismatch evaluates to false and is recorded in the
  transition reason.
- Templates: `{{ path }}` substitution with the same paths, no logic. Values come from masked
  run context only.
- Context available: `issue` (number, short_id, title, status, source, rule_class,
  severity, display_locator, times_seen, first_seen, last_seen), `run` (id, workflow,
  started_at), `steps.<id>` (latest visit: `outcome`, `output`).

### Ports used by executors (`telescreen.ports.workflow`)

```python
class StepExecutor(Protocol):
    step_type: StepType
    async def execute(self, ctx: StepContext) -> StepOutcome: ...
    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome: ...
```

`StepOutcome` variants (dataclass with `kind` enum): `completed(output: JsonValue, route:
str | None)`, `waiting(wait_kind, timer_at: datetime | None)`, `failed(error_code,
retryable)`. `StepSignal` variants: `human_completed`, `evidence_accepted`,
`police_result`, `timer_fired`, `child_finished`, `branch_arrived`, `map_finished`. Executors never touch
the queue directly; they return outcomes and receive signals.

Other ports consumed: `NotificationSender` (notifications), `PoliceGateway`, `EvidenceQuery`
(count accepted evidence for the run's issue and step run), `IssueRecorder.add_history`
(`workflow_started`, `sla_breached`), `IssueReader`, `ResourceRescanner` (detect node),
`LlmClient` (ai_agent), `AuditLog`, `RunSignaler` (implemented by the engine; called by
police, evidence and human-task API).

### Engine semantics (`telescreen.workflow.engine`)

- Start: create `workflow_runs` (status `running`, `context_json` with masked issue fields),
  transition `start`, step run for `start`, enqueue `advance_step` task.
- `advance_step` handler (one DB transaction): load step run; if not `ready` it is a
  duplicate delivery and is acknowledged (idempotent). Mark `running`, call executor, apply
  outcome:
  - `completed`: store output (passed through `Redactor`), mark `succeeded`, resolve route,
    write transition, create next step run (`visit` = previous visits + 1) and enqueue it.
  - `waiting`: mark `waiting`, run status `waiting` if no other active step; enqueue
    `timer_fired` at `timer_at` when set.
  - `failed` and retryable with attempts left: step back to `ready`, task `available_at =
    now + backoff * 2^(attempt-1)`.
  - `failed` otherwise: route to `on_error` if set, else run `failed`.
- Signals (`RunSignaler.signal(step_run_id, signal)`): idempotent; ignored unless the step
  run is `waiting`; enqueues a `resume` delivery (an `advance_step` task with the signal in
  its payload).
- Loop guards: a step entered more than `max_step_visits` times fails the run with
  `loop_limit_exceeded`; `send_back` counts its own uses against `max_send_backs`.
- `send_back`: writes a `send_back` transition with the masked reason, cancels open human
  tasks of steps visited after the target, and creates a new visit of the target step.
- `parallel`/`join`: each branch gets a `branch_token`; branch steps carry it; arriving at
  the join records arrival; `all` waits for every token, `any` continues on first and
  cancels the other branches' waiting steps.
- `sub_workflow`: starts a child run with `parent_step_run_id`; child completion enqueues
  `child_finished` to the parent.
- Run `cancel` (API): cancels waiting steps, human tasks and queued tasks for the run.
- Optimistic locking: `workflow_runs.lock_version` incremented on each run-level update;
  conflict retries the task.
- Side-effect rule: everything inside an `advance_step`, `sla_check` or `timer_fired`
  transaction must be safe to roll back and repeat. Externally visible effects are recorded
  in the same transaction and performed later by their own delivery tasks: notifications
  (`NotificationSender` enqueues `notification_delivery`) and police push deliveries
  (`PoliceGateway.dispatch` enqueues `police_push_delivery`). The only external calls
  allowed inside a step transaction are read-only ones that are safe to repeat: the
  `ai_agent` LLM call and the `detect` rescan fetch. A repeat of those costs time, not
  correctness.

### Queue and worker (`telescreen.workflow.queue`)

- `TaskQueue` port: `enqueue(kind, ref_id, payload, available_at, idempotency_key)`,
  `claim(worker_id, kinds, limit, lease) -> list[ClaimedTask]`, `complete(id)`,
  `retry(id, available_at, error_code)`, `cancel_by_ref(ref_id)`.
- `SqlTaskQueue.claim`: `SELECT ... WHERE status='ready' AND available_at<=now ORDER BY
  available_at LIMIT n FOR UPDATE SKIP LOCKED` (SQLite: plain select in a write
  transaction), then set `leased`, `lease_owner`, `lease_expires_at`. Expired leases are
  returned to `ready` by the worker loop. After `max_attempts` a task becomes `dead` and the
  related step run fails with `task_dead`.
- `telescreen worker` loop: reclaim expired leases, run scheduler tick, claim up to
  `TELESCREEN_WORKER_CONCURRENCY` tasks, dispatch by kind, sleep
  `TELESCREEN_WORKER_POLL_SECONDS` (default 1.0) when idle. Polling only in v1 (no
  LISTEN/NOTIFY; rationale in `notes.md`).
- Tests drive the engine with SQLite repositories, `FakeClock` and a synchronous
  `run_until_idle()` helper that claims and executes ready tasks whose `available_at <= now`.

### Human tasks, SLA and escalation

- `human_task` and `evidence_upload` create a `human_tasks` row with `assignee_ref`,
  `due_at = now + sla`, `tier_index = 0`. When `notify_on_assign` is true (the default)
  they queue an assignee notification through `NotificationSender` in the same
  transaction. An `sla_check` task is enqueued at `due_at`.
- Escalation policy (`escalation_policies.tiers_json`):

```yaml
name: secops-standard
tiers:
  - {after: PT4H, assign_to: team:secops-tier2, notify: [team:secops-tier2, email:secops-leads@example.invalid], channels: [slack, email]}
  - {after: PT8H, assign_to: member:ciso, notify: [member:ciso], channels: [email]}
```

- `sla_check` fires: if the task is still `open`, move to the next tier (`tier_index + 1`,
  `assignee_ref = assign_to`), queue the tier notifications through `NotificationSender`
  (no network I/O in this transaction), write history `sla_breached` and audit,
  set `due_at = now + tier.after` and enqueue the next `sla_check`. After the last tier, the
  task stays open, no further checks are scheduled, and a final `sla_breached` is recorded.
- Completion (`POST /api/human-tasks/{id}/complete {outcome, comment}`): caller must be the
  assignee, a member of the assignee team, or hold `admin`; outcome must be declared;
  `evidence` tasks additionally require `>= min_files` accepted evidence for the step run
  and the run's issue (`EvidenceQuery.accepted_count(issue_id, step_run_id)`; 409
  `evidence_required` otherwise). Comment is redacted before storage.
- Recipient resolution: `member:` -> member email/slack user, `team:` -> team mailing list
  email, team Slack channel, and members, `email:` -> address, `slack:` -> channel
  (v1.2: `slack_channel:` -> public or private channel id only, `slack:` kept as an alias).

### Node executors with external effects

| Executor | Behavior |
|----------|----------|
| `detect` | Asks `ResourceRescanner` to rescan the issue resource (from `issues.resource_ref_json`) through the owning collector adapter; outcome `found` if the issue still has findings from this scan, `clean` if not, `error` if the issue has no rescannable reference or the fetch fails |
| `ai_agent` | Builds prompt from template (masked context only, plus `Redactor` pass), calls `LlmClient.complete_json` with schema built from `output`, validates, redacts string outputs |
| `notify` | Renders templates and calls `NotificationSender.send`. The DLP gate runs, the notification row and its audit record are written, and `notification_delivery` is enqueued in the step transaction. The step completes once the notification is queued, with output `{queued: true, notification_ids}`, or with `{queued: false, delivered: false}` when the DLP verdict is `blocked`. Delivery is at-least-once, keyed by notification id. The step does not wait for delivery |
| `evidence_upload` | Waiting human task of kind `evidence`; resumes on `human_completed` |
| `map` | Creates a `map_jobs` row and its initial partitions, then waits; the map-reduce machinery (`design-map-reduce-sweeps.md`) dispatches shards concurrently and resumes the step with `map_finished` carrying the count-only sweep result |
| `police_verify` | Dispatches `PoliceTaskRequest` through `PoliceGateway` with deadline `now + timeout` and enqueues `police_timeout`; resumes on `police_result` (outcome `passed` or `failed`) or timeout (`error`) |

### Workflow DSL v2 (v1.2)

Full rules are in `design-v1-2-notifications-and-identity.md` section 4. Summary:

- `apiVersion: telescreen.workflow/v2`. v1 documents load and run unchanged; v2-only fields
  in a v1 document fail with `requires_api_version_v2`. `compiled_json` records the source
  `apiVersion`, so stored v1 compiled documents reload unchanged. The published schema
  accepts both versions.
- Top-level `notification_sets: [{name, targets: [{recipients, channels, template?}]}]`.
- `notify`: exactly one of `to` (v1 form with `channel`), `notification_set` or `targets`;
  text from `template`, inline `subject`/`body`, or the built-in `system.workflow_notify`. A
  target `template` overrides the step's text.
- `human_task` / `evidence_upload`: optional `notify_set` or `notify_targets`, notified in
  addition to the assignee when the task opens (default template
  `system.human_task_assigned`).
- Escalation tiers: optional `notification_set` (resolved against the run's workflow) and
  `targets`. A tier needs at least one of `notify`, `targets`, `notification_set` (default
  template `system.sla_escalated`).
- Recipient kinds: `member:`, `team:`, `email:`, `slack_channel:` (new) and `slack:`
  (alias). `email:` works only with the `email` channel, and `slack_channel:`/`slack:` only
  with `slack`.
- The executors pass a typed `NotificationContext` and template name to
  `NotificationSender`; rendering and DLP happen there once per target and channel. Unknown
  or unreachable recipients become `skipped` rows and never fail a v2 step. The v2 notify
  output is `{queued, notification_ids, targets: [{index, channel, queued, blocked,
  skipped}]}`.
- Upload (API) checks: referenced templates exist, are active and have event kind
  `workflow_notify`, and the sets named by the escalation policies of human-task steps are
  declared.

The v1 `human_task`, `sla_escalated` and `notify` texts become the built-in templates
`system.human_task_assigned`, `system.sla_escalated` and (for v2 steps without text)
`system.workflow_notify`. The built-ins reproduce the v1.1 text exactly.

### Triggers

`WorkflowTriggerService` implements `IssueEventSink`: for each active definition (latest
active version per name) whose trigger `on` and `match` accept the event, start a run unless
a non-terminal run of the same workflow name exists for the issue. Idempotency key
`trigger:<issue>:<workflow>:<event-id>`.

## References

See `design-docs/references/README.md`.
