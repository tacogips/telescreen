# V12-P04 Notification Templates, Rendering, Per-Target Rows, System Call Sites

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1 (D1-D5, D8), 3, 4.3, 8; design-docs/specs/design-masking.md "v1.2 additions"; design-docs/specs/design-data-model.md "v1.2 additions"
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p04-notification-templates`
- planPath: `impl-plans/active/v12-p04-notification-templates.md`
- wave: 2
- dependsOn: `v12-p01-foundation`, `v12-p02-db`
- writePaths:
  - `impl-plans/active/v12-p04-notification-templates.md`
  - `src/telescreen/notifications`
  - `src/telescreen/issues/verification.py`
  - `src/telescreen/issues/digest.py`
  - `src/telescreen/issues/exceptions.py`
  - `src/telescreen/issues/recipient_channel.py`
  - `src/telescreen/issues/service.py` (`IssueSnapshot.masked_excerpt` population only)
  - `src/telescreen/collectors/health.py`
  - `src/telescreen/police/health.py`
  - `tests/notifications`
  - `tests/issues/v11_remediation/test_remediation.py` (request-text assertions only)
  - `tests/police/test_v11_police.py` (request-text assertion only)
- sharedPaths: none
- sharedPathNotes (added by the session-8 inventory audit): D4 call sites now send
  `event` + `context` with empty `subject`/`body`, so the existing assertions on fake
  sender request text break. Rewrite only these assertions, keep every other line:
  - `test_remediation.py` about :427 and :594 (`body.startswith("TELE-n")`) -> assert
    `request.event` is `exception_requested` / `exception_expired` and
    `request.context.issue_short_id == "TELE-n"`; about :472-475 and :708-714 -> assert
    the event (`exception_requested` / `owner_digest`), that the context carries the short
    id (`issue_short_id`, or `"TELE-1" in context.digest_summary`), and keep the
    `not in` checks against every context string field instead of `request.body`;
  - `test_v11_police.py` about :367 (`"police_silent" in request.subject`) -> assert
    `request.event is NotificationEvent.POLICE_HEALTH_ALERT` and
    `request.context.health_condition == "police_silent"`.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Make notification text editable without ever letting raw values reach a template:

- versioned DB templates per name, plus built-ins `system.<event>` held in code as
  version 0;
- an allow-listed context, rendered inside `NotificationService.send` before `Redactor`
  and the DLP gate;
- per-target `notifications` rows, including `skipped` rows;
- a delivery fix: a row with no address on its channel becomes `failed`;
- the seven non-workflow call sites move to `event` + `NotificationContext`. Workflow
  call sites are v12-p05.

Imitate:

- `notifications/sender.py:NotificationService.send` (one transaction: gate, row, audit,
  enqueue);
- `workflow/dsl/templates.py:parse_template` (reuse it; the notifications package (L3)
  may import `telescreen.workflow.dsl.templates`, which is L1);
- `issues/policy/` store and activate patterns, and
  `workflow/engine/definitions.py:WorkflowService.activate` (one active version per
  name);
- `notifications/dlp.py:CensoredMessage` sealing (do not weaken it).

## Non-goals

- No router (v12-p10), no wiring (v12-p11), no workflow executor change (v12-p05).
- No HTML email; text only. No template logic (if/loops/filters).
- No change to `DlpGate.inspect` semantics.

## Invariants

- Requests with `event is None` behave exactly as in v1.1 (same rows, audit and
  failures).
- Built-ins reproduce the v1.1 subject and body byte for byte (one parity test per
  event).
- The rendered subject and body pass `Redactor.redact`, then `DlpGate.inspect`. Nothing
  rendered is stored or sent without the gate.
- New `NotificationService` fields are defaulted: `templates: TemplateResolver =
  field(default_factory=BuiltinTemplateResolver)`, and `redactor` is taken from
  `gate.redactor`. Current wiring (`wiring.py:233`) compiles unchanged.
- Template text, the context and the rendered text are never logged, never put into
  audit details, and never in exception messages.

## Deliverables

1. **`notifications/templates/` package** (new; split so that each file stays small):
   - `variables.py`:
     - `VARIABLE_FIELDS: tuple[tuple[str, str], ...]` maps the path `issue.short_id`
       to the `NotificationContext` field `issue_short_id`, and so on for all 21
       variables;
     - `EVENT_GROUPS: tuple[tuple[NotificationEvent, tuple[str, ...]], ...]` holds the
       allowed variable groups per event (design 3.3 table: `workflow_notify` ->
       issue, run, step, task, assignee; ...);
     - `allowed_paths(event) -> tuple[str, ...]`;
     - `lookup(context, path) -> str` (None -> "", int -> `str(int)`).
   - `builtins.py`: `BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...]`, one per event, named
     `system.<event value>`, version 0. Texts:
     - `human_task_assigned`: `Workflow task assigned` / `{{ task.title }}`;
     - `sla_escalated`: `Workflow task escalated` / `{{ task.title }}`;
     - `owner_review_assigned`: `Telescreen: review {{ issue.short_id }}` /
       `{{ issue.short_id }} {{ issue.short_id }}\n{{ issue.rule_class }} {{
       issue.severity }} {{ issue.exposure }}\nrisk {{ issue.risk_score }}\n{{ task.link
       }}` (v1.1 printed `TELE-<n> <snapshot.short_id>`, and `IssueSnapshot.short_id` is
       `TELE-<n>`);
     - `owner_digest`: `Telescreen owner digest` / `{{ digest.summary }}`;
     - `exception_requested`: `Exception requested for {{ issue.short_id }}` / `{{
       issue.short_id }} {{ issue.rule_class }} {{ issue.severity }} {{ issue.exposure
       }}`;
     - `exception_expired`: the same body with subject `Exception expired for ...`;
     - `verification_inconclusive`: subject `Verification inconclusive for {{
       issue.short_id }}`, the same body;
     - `collector_health_alert`: `Telescreen health: {{ health.target }} {{
       health.condition }}` / `{{ health.details }}`;
     - `police_health_alert`: `Telescreen health: police {{ health.target }} {{
       health.condition }}` / `Police worker {{ health.target }} is silent.`;
     - `workflow_notify` (new): `Telescreen: {{ issue.short_id }} {{ issue.rule_class
       }}` / `{{ issue.title }}\nseverity {{ issue.severity }}\n{{ issue.location }}\n{{
       issue.link }}`.
   - `render.py`: `render_for_channel(version: TemplateText, channel, context) ->
     tuple[str, str]`:
     - variant selection follows design 3.1;
     - subject: replace `\r` and `\n` with a space;
     - Slack: escape `&` `<` `>` in substituted VALUES only;
     - body: `rstrip()`. This makes the optional `task.link` line vanish, so v1.1 parity
       holds.
   - `validate.py`: `validate_template(name, event_kind, texts, existing_event:
     NotificationEvent | None, redactor) -> tuple[TemplateIssue(field, code), ...]`.
     Codes and limits follow design 3.4: `invalid_name`, `reserved_name`,
     `template_event_mismatch`, `too_long`, `invalid_template`, `unknown_variable`,
     `raw_value_detected`, `incomplete_variant`. The `Redactor.has_hits` check runs on
     every text field.
   - `synthetic.py`: `synthetic_context(event) -> NotificationContext` returns fixed
     constants: `TELE-123`, `credential`, `high`, `unresolved`, `public`, 72, location
     `github:example-org/example-repo/config/app.yml`, excerpt `token = "****"`, link
     `https://telescreen.example.test/issues/example`, workflow `example-workflow`, and
     so on. Reserved example domains and Masker formats only.
   - `repository.py`: `TemplateRepository` (`versions(name)`, `active(name)`,
     `latest_version(name)`, `insert(row)`, `names()`). Normalize timestamps with
     `ensure_utc`.
   - `service.py`:
     - `TemplateService(repository, redactor, gate: DlpGate, audit, clock)` with
       `list`, `get`, `version_text`, `validate`, `preview(event, texts) ->
       PreviewResult(email, slack)`, `create_version(session, ..., actor_ref) ->
       (name, version)`, and `activate(session, name, version, actor_ref)`;
     - `create_version` writes a new row with `active=False`. On validation failure it
       raises a `TemplateValidationError(issues)` and audits `template.rejected` with
       field names and codes only;
     - `activate` locks the name's rows (`with_for_update`), sets exactly one row
       active, or none when the version is 0 and the name is a built-in; an unknown
       version -> `NOT_FOUND`;
     - audit `template.created` / `template.activated` with details name, version and
       event kind;
     - it also implements `TemplateCatalog.lookup` (p01 port).
   - `resolver.py`: `TemplateResolver` Protocol `resolve(session, name, event) ->
     ResolvedTemplate(name, version, event_kind, texts, fallback: bool)`.
     `BuiltinTemplateResolver` uses built-ins only. `DbTemplateResolver(repository)`
     resolves the active DB row, else the built-in when the name is `system.*`, else
     falls back to `system.<event>` with `fallback=True`.
2. **`notifications/sender.py`**:
   - Event path: resolve the template, render the request channel's variant, apply
     `Redactor.redact(...).text` to both texts, then the gate.
   - `inline=True`: use `subject`/`body` as given (still Redactor + gate) and record
     `event_kind`.
   - Rows record `event_kind`, `template_name`, `template_version` (None for inline),
     `set_name` and `target_index`.
   - `record_unreachable=True`: per ref, `recipient_unknown` (resolver raises) or no
     address on the request channel (use `ResolvedRecipients` emails for email; Slack
     channels or user ids for slack) -> row status `skipped`, `error_code` set, audit
     `notification.skipped`, no task, id appended to `receipt.skipped`.
   - `record_unreachable=False` keeps the v1 behavior: unknown -> raises
     `RECIPIENT_UNKNOWN`.
   - Audit details add `event_kind`, `template_name`, `template_version`, `set_name`,
     `target_index`, `template_fallback` (only the ones that are set).
3. **`notifications/delivery.py`**:
   - When the resolved recipients have no address on the row's channel, mark the row
     `failed` with `error_code=recipient_unreachable`, audit `notification.failed`, and
     return `TaskResult.dead(ErrorCode.RECIPIENT_UNREACHABLE)`.
   - Set `error_code` on the existing `failed` paths too (`recipient_unknown`,
     `delivery_failed`). `NotificationRepository.set_delivery` gains `error_code: str |
     None = None`.
4. **`notifications/slack.py`**: `send` returns `SlackPostResult(channel: str, ts: str)`
   parsed from the `ok=true` response (`SlackResponse` gains optional `channel` and
   `ts`). Callers may ignore the result.
5. **Call sites** (pass `event`, `context`, `inline=False`, and empty strings for
   `subject`/`body`; keep channel selection and recipients exactly as today):
   - `issues/verification.py:_notify` -> `verification_inconclusive`;
   - `issues/exceptions.py`: the requested path -> `exception_requested`;
     `_notify_assignee` -> `exception_expired`. Keep the `_notify` signature internal
     but pass an event and a context;
   - `issues/digest.py:_send` -> `owner_digest` with `digest_summary=self._body(...)`;
   - `collectors/health.py:_notify` -> `collector_health_alert`
     (`health_target=config.name`, `health_condition`, `health_details` = the existing
     key=value body);
   - `police/health.py:_open_alert` -> `police_health_alert`
     (`health_target=worker.name`, `health_condition="police_silent"`). Keep the
     existing audit summary subject string as is;
   - `issues/service.py`: fill `IssueSnapshot.masked_excerpt` from the latest finding's
     `masked_snippet` (one ordered query in the existing snapshot builder).
   Contexts are built with a small helper in each module from `IssueRow` fields:
   `issue_short_id=short_id(number)`, `issue_rule_class`, `issue_severity`,
   `issue_exposure=exposure_visibility`, `issue_risk_score`, `issue_status`.

## Pitfalls

- Slack escaping applies to values only. Escaping the literal admin text would break
  `&` in subjects that parity tests expect.
- `rstrip()` the body only, not the subject.
- `parse_template` raises `TelescreenError(EXPRESSION_ERROR)`. Map it to the
  `invalid_template` issue; never pass the text along.
- `unknown_variable`: compare the parsed path (`".".join(part.path)`) against
  `allowed_paths(event)`.
- An email variant with an empty string is `incomplete_variant`. A missing (None)
  variant is fine.
- Do not re-resolve recipients twice in a way that changes v1 audit counts for
  `event is None`.
- `skipped` rows still store the gated (masked) subject and body. Never store pre-gate
  text.

## Tests (`tests/notifications/v12/`; update `tests/notifications/test_notifications.py` only where the delivery fix changes v1.1 expectations)

- Parity: for each of the 9 v1.1 events, the context built from the same `IssueRow` or
  health inputs renders text identical to the v1.1 f-string (put copies of the v1.1
  expressions in the test).
- Validation:
  - `{{ issue.secret }}` -> `unknown_variable` on `body`;
  - `{{ digest.summary }}` for `exception_expired` -> `unknown_variable`;
  - a sample secret from `tests.support.samples` in `slack_body` ->
    `raw_value_detected`, no row inserted, and an audit `template.rejected` whose details
    contain only field names;
  - name `system.custom` -> `reserved_name`;
  - a new version with another event -> `template_event_mismatch`.
- Lifecycle:
  - create v1 and v2 (both inactive), activate v2 -> resolver returns v2;
  - activate version 0 of `system.owner_digest` after a DB version -> resolver returns
    the built-in;
  - activating an unknown version -> `NOT_FOUND`.
- Rendering: Slack escapes `<!channel>` in `issue.title`; an email subject with `\n`
  becomes a single line; a missing optional variable renders empty.
- Preview: returns email and Slack variants with the verdict `pass`; synthetic contexts
  pass the rule detector (`Redactor.has_hits` false for every field of every event).
- Sender:
  - an event request writes `event_kind`, `template_name` and `template_version` 0;
  - `record_unreachable` with `email:x@example.test` on Slack -> `skipped` /
    `recipient_unreachable`, no `engine_tasks` row;
  - an unknown member -> `skipped` / `recipient_unknown`;
  - an inline request with a sample secret in the body -> DLP masked, and the row has no
    raw sample;
  - `event is None` with an unknown member still raises `RECIPIENT_UNKNOWN`.
- Delivery: a member without an email on an email row -> `failed` /
  `recipient_unreachable`, and the fake SMTP transport receives nothing.
- Slack: a MockTransport `{"ok":true,"channel":"C1","ts":"1.2"}` -> `SlackPostResult`.
- Call sites: the existing `tests/issues`, `tests/collectors` and `tests/police` suites
  still pass. The only assertions that compare request subject/body are the ones listed
  in sharedPathNotes (planner grep on 2026-10-11:
  `grep -rnE "\.(subject|body)\b" tests/issues tests/collectors tests/police`); rewrite
  them as described there. Any other failing assertion outside `writePaths` becomes a
  Reconciliation request.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/notifications src/telescreen/issues
src/telescreen/collectors/health.py src/telescreen/police/health.py` and `<tests>` =
`tests/notifications tests/issues/v11_remediation/test_remediation.py
tests/police/test_v11_police.py`. Additionally run:

```bash
uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/police > /tmp/telescreen-v12-p04-notification-templates-regress.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e tests/leak > /tmp/telescreen-v12-p04-notification-templates-e2e.log 2>&1; echo "exit=$?"
```

The first command must exit 0, which shows that the call-site behavior is unchanged by
parity. The second is informational during the fanout, because wave-2 plans edit
workflow and api code at the same time. Record its exit status. A failure in a p04 path
must be fixed; a failure elsewhere is noted.

## Completion Criteria

- [ ] Template package, sender, delivery, Slack result and call sites implemented.
- [ ] New and existing notification tests pass; the regression command exits 0.
- [ ] `git diff` on the two v1.1 test files shows only the request-text assertion
      rewrites of sharedPathNotes.
- [ ] Scoped gates pass; typelint grep empty; each file under 1000 lines.
- [ ] Progress Log complete.

## Progress Log
