# V12-P05 Workflow DSL v2 Notification Sets, Executors and Escalation

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1 (D5-D8), 4; design-docs/specs/design-workflow-engine.md "Workflow DSL v2 (v1.2)"
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p05-workflow-notification-sets`
- planPath: `impl-plans/active/v12-p05-workflow-notification-sets.md`
- wave: 2
- dependsOn: `v12-p01-foundation`, `v12-p02-db`
- writePaths:
  - `impl-plans/active/v12-p05-workflow-notification-sets.md`
  - `src/telescreen/workflow/dsl`
  - `src/telescreen/workflow/executors/notify.py`
  - `src/telescreen/workflow/executors/human.py`
  - `src/telescreen/workflow/escalation`
  - `src/telescreen/workflow/engine/definitions.py`
  - `src/telescreen/workflow/recipients.py`
  - `src/telescreen/workflow/notify_targets.py` (new)
  - `tests/workflow/v12` (new directory)
  - `tests/workflow/test_v11_workflow.py` (owner-review request-text assertions only)
- sharedPaths: none
- sharedPathNotes (added by the session-8 inventory audit): `HumanTaskExecutor._notify`
  now sends `event=owner_review_assigned` + `context` with empty `subject`/`body` (D4), so
  `test_v11_workflow.py` about :406-412 (`request.subject == "Telescreen: review TELE-8"`
  and the `request.body` checks) breaks. Rewrite only those lines: assert `request.event
  is NotificationEvent.OWNER_REVIEW_ASSIGNED`, `request.context.issue_short_id ==
  "TELE-8"`, `issue_rule_class`/`issue_severity`/`issue_exposure` = `credential`/`high`/
  `public`, `issue_risk_score == 40`, `task_link == "https://telescreen.example/my-tasks"`,
  and keep both `private-file.txt` negative checks against every context string field.
  Keep the request count and channel assertions unchanged.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Workflows declare named notification sets (targets = recipients x channels x optional
template) under `apiVersion: telescreen.workflow/v2`. Three sites fan out one DLP-gated
`NotificationRequest` per target and channel:

- notify steps;
- human-task assignment (`notify_set` / `notify_targets`);
- escalation tiers.

The sites also send the workflow system events through templates
(`human_task_assigned`, `owner_review_assigned`, `sla_escalated`). Rendering, DLP and the
`skipped` rows happen in `NotificationService` (v12-p04). Here we only build requests.
v1 documents must load and run exactly as before.

Imitate:

- `workflow/dsl/loader.py:_read_step`, `_Reader.strings`, `_enum_tuple` (path-only
  issues);
- `workflow/dsl/validate.py:_recipient`;
- `workflow/dsl/compiled.py:_step_json`;
- `workflow/engine/definitions.py:WorkflowService.upload` (API-level `MapStep` collector
  check);
- `workflow/escalation/sla.py:SlaCheckHandler._send_tier`;
- `workflow/executors/human.py:HumanTaskExecutor._notify`.

## Non-goals

- No change to `NotificationService` or templates (p04), the routers (p10/p06) or wiring
  (p11).
- No new step type. No change to engine semantics, the queue or the run state machine.
- No editing of `workflow-v1.schema.json`. Add a new schema file instead.

## Invariants

- A v1 document's `CompiledWorkflow`, its `compiled_to_json` output and its executor
  behavior are unchanged. The existing `tests/workflow/**` pass untouched, except the
  owner-review request-text lines of `tests/workflow/test_v11_workflow.py` (sharedPathNotes).
- The engine executes the stored definition (`engine/engine.py` and
  `triggers/service.py` call `compiled_from_json(parse_json(definition.compiled_json))`),
  not the loader output. `set_targets` is therefore rebuilt in `compiled_from_json`; a
  set reference that is lost there would silently send nothing at run time.
- `CompiledWorkflow` gains defaulted fields `api_version: str =
  "telescreen.workflow/v1"` and `notification_sets: tuple[NotificationSetSpec, ...] =
  ()`. `compiled_to_json` writes `workflow.api_version` (not the hard-coded v1), plus
  `notification_sets` when non-empty. `compiled_from_json` therefore round-trips v2.
- `loader.py` stays under 1000 lines (652 now). New parsing goes into
  `dsl/notification_sets.py`.
- New executor and handler fields are defaulted (`templates: TemplateCatalog | None =
  None` on `WorkflowService`; `public_base_url` reuse). `wiring.py` compiles unchanged.
- Validation errors carry JSON paths and codes only, never recipient values.

## Deliverables

1. **`dsl/model.py`**:
   - `NotificationTargetSpec(recipients: tuple[str, ...], channels:
     tuple[NotificationChannel, ...], template: str | None = None)`;
   - `NotificationSetSpec(name: str, targets: tuple[NotificationTargetSpec, ...])`;
   - `NotifyStep` adds `notification_set: str | None = None`, `targets:
     tuple[NotificationTargetSpec, ...] = ()`, `template: str | None = None` and
     `set_targets: tuple[NotificationTargetSpec, ...] = ()`. Keep
     `channel`/`to`/`subject`/`body`;
   - `HumanTaskStep` and `EvidenceUploadStep` add `notify_set: str | None = None`,
     `notify_targets: tuple[NotificationTargetSpec, ...] = ()` and `set_targets:
     tuple[NotificationTargetSpec, ...] = ()`;
   - `set_targets` is filled by the loader from the named set (deliverable 3) and is
     never serialized. `StepContext` (`workflow/step_api.py`) carries no compiled
     workflow, so executors read `step.set_targets` plus `step.notification_set` /
     `step.notify_set` (the set name used for `set_name`);
   - the `CompiledWorkflow` fields as in Invariants.
2. **`dsl/notification_sets.py`** (new):
   - `read_notification_sets(reader, value, path)`;
   - `read_targets(reader, value, path)`;
   - `validate_recipient_for_channels(ref, channels, path, issues)`. Kinds are
     `member:`, `team:`, `email:`, `slack_channel:` and `slack:`:
     - `email:` needs exactly one `@`, no whitespace or control characters, none of
       `,;<>`, and at most 254 characters;
     - `slack_channel:` must match `^[CDGUW][A-Z0-9]{8,}$`;
     - `slack:` is any non-empty value (v1 alias);
     - `email:` with `slack`, or `slack_channel:`/`slack:` with `email` ->
       `undeliverable_combination`.
   - Codes: `duplicate_name`, `empty_list`, `invalid_recipient`,
     `undeliverable_combination`, `unknown_notification_set`.
   - `resolve_set_targets(workflow: CompiledWorkflow) -> CompiledWorkflow`: returns a copy
     whose notify, human_task and evidence_upload steps carry `set_targets` from
     `workflow.notification_sets` (unknown names -> empty). This is the ONE resolver,
     used by the loader (deliverable 3) and by `compiled_from_json` (deliverable 5).
3. **`dsl/loader.py`**:
   - Accept `telescreen.workflow/v1` and `telescreen.workflow/v2`.
   - Read `notification_sets` and the new step fields through `notification_sets.py`.
   - In a v1 document, any v2-only field (`notification_sets`, `notification_set`,
     `targets`, `template`, `notify_set`, `notify_targets`) ->
     `requires_api_version_v2` at that path.
   - Notify `to` + `notification_set` (or `targets`) together -> `conflicting_fields`.
     In v2, a notify with none of `to`/`notification_set`/`targets` -> `missing_field`.
   - After the steps and sets parse, call `resolve_set_targets` (each `notification_set`
     / `notify_set` name -> that step's `set_targets` via `dataclasses.replace`). When the
     name is unknown, leave `set_targets` empty; `validate.py` reports it.
4. **`dsl/validate.py`**: unknown `notification_set` / `notify_set` ->
   `unknown_notification_set`. Every inline and set target goes through
   `validate_recipient_for_channels`.
5. **`dsl/compiled.py`**: serialize `api_version`, `notification_sets` and the new step
   fields (only when set, so v1 output is byte-identical). `set_targets` is not
   serialized; `compiled_from_json` decodes the sets and step references and then
   returns `resolve_set_targets(workflow)`, so `compiled_from_json(compiled_to_json(w))
   == w` holds for v2 and the engine sees the set targets.
6. **`dsl/escalation.py`**:
   - `EscalationTier` gains `notification_set: str | None = None` and `targets:
     tuple[NotificationTargetSpec, ...] = ()`;
   - `notify` and `channels` become optional, but a tier needs at least one of `notify`,
     `targets`, `notification_set` (`missing_field` at `/tiers/<i>`);
   - `_RECIPIENT` accepts `slack_channel:`; JSON round-trip helpers include the new
     fields.
7. **`dsl/schema.py`** and new **`dsl/workflow-v2.schema.json`**: the schema accepts
   `apiVersion` in `[v1, v2]` plus the new optional properties; `workflow_schema_text()`
   returns the v2 file. Do not delete the v1 file.
8. **`workflow/notify_targets.py`** (new):
   - `build_workflow_context(issue: IssueSnapshot | None, workflow_name: str,
     step_name: str, task_title: str | None, task_due_at: datetime | None, task_tier:
     int | None, assignee_ref: str | None, public_base_url: str | None) ->
     NotificationContext`:
     - issue fields come from the snapshot (`issue_exposure=visibility.value`,
       `issue_excerpt=masked_excerpt`, `issue_location=display_locator`,
       `issue_title=display_title`);
     - `issue_link` and `task_link` are built only when the base URL is set
       (`<base>/issues/<id>`, `<base>/my-tasks`).
   - `fan_out(sender, session, targets, set_name: str | None, event, default_template:
     str | None, inline_subject: str | None, inline_body: str | None, context, actor_ref,
     run_id, issue_id) -> tuple[TargetOutcome, ...]`:
     - one request per (target index, channel) with `record_unreachable=True`;
     - template precedence (design 4.1): `target.template`, then the step template,
       then inline text (`inline=True`), then the default;
     - `TargetOutcome(index, channel, queued, blocked, skipped, notification_ids)` counts
       come from the receipt and the verdict.
9. **`executors/notify.py`**:
   - The v1 `to` form keeps the v1 path (`event=None`, inline rendered text, v1 output).
   - The v2 `to` form with `template` sends `event=workflow_notify` with that template.
   - The set/targets forms call `fan_out` and return output `{queued, notification_ids,
     targets: [{index, channel, queued, blocked, skipped}]}`; `queued` is true when any
     row is queued; recipient problems never fail the step.
   - Inline text is rendered with `render(step.subject, ctx.context)` plus
     `Redactor.redact`, as today.
10. **`executors/human.py`**:
    - `_notify` sends assignee notifications with `event=human_task_assigned` (generic)
      or `owner_review_assigned`, and a context: `task_title=title`,
      `assignee_ref=assignee`, `task_link` from the base URL. Keep today's channel
      selection exactly.
    - Then, when the step has `set_targets` or `notify_targets`, call `fan_out` with
      default `system.human_task_assigned` (`set_name=step.notify_set` for set
      targets). The workflow name for the context is `ctx.context.run.workflow`.
11. **`escalation/sla.py`**:
    - `_send_tier` keeps the tier `notify`/`channels` behavior with
      `event=sla_escalated` and a context (`task_title=human_task.title`,
      `task_tier=tier_index`).
    - Then it resolves `tier.notification_set` against the run's compiled workflow
      (definition row -> `compiled_from_json`). A missing set -> audit
      `notification.set_missing` (target the human task; details set name only).
    - Then it fans out set and inline `targets` with default `system.sla_escalated`.
12. **`engine/definitions.py`**: `WorkflowService` gains `templates: TemplateCatalog |
    None = None`. When set, `upload` checks every referenced template name (step,
    target, set) -> `unknown_template` when missing or without an active version,
    `template_event_mismatch` when the event kind is not `workflow_notify`. For each
    human_task/evidence_upload with an `escalation_policy` whose stored tiers name a
    `notification_set` absent from the workflow -> `unknown_notification_set` at
    `/steps/<id>/escalation_policy`. Raise `WorkflowValidationError` as the map check
    does.
13. **`workflow/recipients.py`**: no behavior change expected. Touch it only if a helper
    must accept `slack_channel:` (the resolver itself is p07).

## Pitfalls

- `ValidationIssue` paths for targets: `/notification_sets/<i>/targets/<j>/recipients/<k>`
  and `/steps/<n>/targets/<j>/...`.
- Never put recipient values in step outputs, audit details or errors. Outputs carry
  counts only.
- `HumanTaskExecutor` must not fail the task when fan-out records `skipped`.
  `RECIPIENT_UNKNOWN` cannot occur with `record_unreachable=True`.
- Do not edit `workflow/step_api.py` or `workflow/engine/engine.py`. Executors get set
  targets from `step.set_targets` (resolved at load time). Only `sla.py` loads the run's
  definition (`WorkflowDefinitionRow` by `run.definition_id`, then
  `compiled_from_json(parse_json(row.compiled_json))`) to resolve tier sets.
- Tier set resolution must not raise when the definition is missing. Skip, and audit.

## Tests (`tests/workflow/v12/`)

- Loader:
  - a v1 document with `notification_sets` -> `requires_api_version_v2`;
  - v2 with a set and a notify `notification_set` loads, and `compiled_to_json` ->
    `compiled_from_json` round-trips with equality AND the decoded notify and human-task
    steps have non-empty `set_targets` (guards the engine path);
  - a notify executor run on a step taken from `compiled_from_json(compiled_to_json(w))`
    fans out the set targets (not zero requests);
  - `email:` + slack -> `undeliverable_combination`;
  - `slack_channel:general` -> `invalid_recipient`;
  - an unknown set -> `unknown_notification_set`;
  - empty channels -> `empty_list`;
  - `to` + `notification_set` -> `conflicting_fields`;
  - every existing `examples/workflows/*.yaml` still loads (read-only use).
- Escalation: a tier with only `notification_set` parses; a tier with none of the three
  -> `missing_field`; `slack_channel:C0123ABCDE` is accepted.
- Notify executor with a fake `NotificationSender` (records requests and returns
  receipts):
  - 2 targets x 2 channels -> 4 requests with `set_name`, `target_index`,
    `record_unreachable=True`;
  - template precedence (target over step over inline over default) shows in
    `template_name` / `inline`;
  - output counts;
  - the v1 `to` form -> a single request with `event=None` (unchanged).
- Human executor: assignee request `event=human_task_assigned`, context `task_title` set;
  a `notify_set` adds target requests.
- SLA: a tier with a set -> requests with `event=sla_escalated`; a missing set -> audit
  `notification.set_missing` and the tier `notify` refs still sent.
- Upload: with a fake `TemplateCatalog`, an unknown template -> `unknown_template`; an
  `owner_digest` template -> `template_event_mismatch`; an escalation policy naming an
  undeclared set -> `unknown_notification_set`; with `templates=None` the check is
  skipped.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/workflow` and `<tests>` =
`tests/workflow/v12 tests/workflow/test_v11_workflow.py`. Also run `uv run --frozen pytest -p no:cacheprovider tests/workflow
tests/examples` -> exit 0 (the v1 and v1.1 workflow suites are unchanged).

## Completion Criteria

- [ ] Deliverables 1-12 implemented; files under 1000 lines.
- [ ] `tests/workflow` and `tests/examples` pass; scoped gates pass; typelint grep empty.
- [ ] `git diff tests/workflow/test_v11_workflow.py` shows only the owner-review
      request-text assertion rewrite.
- [ ] Progress Log complete.

## Progress Log
