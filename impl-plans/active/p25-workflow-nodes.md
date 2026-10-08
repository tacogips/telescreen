# P25 Workflow Node Executors (detect, ai_agent, notify, police_verify) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-workflow-engine.md (Node executors with external effects, Ports used by executors), design-docs/specs/design-police-contract.md (Inputs by task type: verify_remediation)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p25-workflow-nodes`
- planPath: `impl-plans/active/p25-workflow-nodes.md`
- wave: 4
- dependsOn: `p02-db-schema`, `p06-workflow-dsl`, `p08-masking`, `p09-detection-llm-ml`
- writePaths:
  - `impl-plans/active/p25-workflow-nodes.md`
  - `src/telescreen/workflow/executors/detect.py`
  - `src/telescreen/workflow/executors/ai_agent.py`
  - `src/telescreen/workflow/executors/notify.py`
  - `src/telescreen/workflow/executors/police_verify.py`
  - `tests/workflow/executors`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. `src/telescreen/workflow/executors/__init__.py` is
owned by p24 (same wave). Do not create it; namespace import works until p24 adds it.

## Intent and Context

These four step types have external effects, and each implements
`telescreen.workflow.step_api.StepExecutor`. Example from the issue: "node1 checks
whether credential information is stored -> if yes, a different AI model verifies
details". That is a `detect` step followed by an `ai_agent` step with another model. The
`police_verify` step implements the Police re-check in the remediation workflow.

## Non-goals

- No engine changes and no registration (p28 registers executors in p24's registry).

## Deliverables

- `detect.py`: `DetectExecutor(rescanner: ResourceRescanner)` with
  `step_type = StepType.DETECT`. `execute`:
  - no `ctx.issue_id` -> `completed(route=outcomes.error, output={result:"error", error_code:"not_rescannable"})`;
  - otherwise `rescan(issue_id, detectors=step.detectors)` (None means the configured
    pipeline) -> route found, clean or error with output `{result, error_code?}`.
  - It never fails the step: errors route through the `error` outcome.
- `ai_agent.py`: `AiAgentExecutor(managed: LlmClient | None, local: LlmClient | None, managed_model: str | None, local_model: str | None, redactor: Redactor)`:
  1. Render the prompt template with `ctx.context` (masked context), then
     `redactor.redact` it.
  2. Build a JSON schema from `output` fields (string, boolean, number, or enum of
     `values`; all required; `additionalProperties: false`).
  3. Call `complete_json` with the step's model or the provider default.
  4. Parse with `parse_json` and type-check each field. A missing or extra field or a
     wrong type means up to 2 more attempts, then `failed(llm_output_invalid, retryable=False)`.
  5. Redact string outputs; output is the object of fields; route `next`.
  - A provider error -> `failed(provider_error, retryable=True)`.
  - A missing client for the provider -> `failed(llm_unavailable, retryable=False)`.
- `notify.py`: `NotifyExecutor(sender: NotificationSender)`: render subject and body,
  then `sender.send(ctx.session, NotificationRequest(channel, recipients=to, subject, body, actor_ref="system:workflow", run_id, issue_id))`.
  - Queued -> `completed({queued: true, notification_ids: [...]})`.
  - Blocked -> `completed({queued: false, delivered: false})`.
  - `recipient_unknown` -> `failed(recipient_unknown, retryable=False)`.
  - The step never waits for delivery.
- `police_verify.py`: `PoliceVerifyExecutor(gateway: PoliceGateway)`. `execute`:
  - Build the input:
    - for `verify_remediation`:
      `{issue:{id, short_id, source, display_locator, rule_class, severity, first_seen, last_seen}, instructions: <rendered "instructions" template, or empty>}`
      from `ctx.context.issue`;
    - for `custom:<name>`: `{payload: {<rendered input templates>}}`;
    - for `detect`: the rendered templates as given.
  - Dispatch `PoliceTaskRequest(task_type, labels, input, deadline=now+timeout, step_run_id=ctx.step_run_id)`.
  - Return `waiting(POLICE, timer_at=deadline)`. Do not store the task id in the step:
    the `police_tasks.step_run_id` column already links the task to this step.
  - On `timer_fired`, cancel the outstanding task first: look it up through
    `police_tasks.step_run_id` with a read-only query on `PoliceTaskRow`, and call
    `gateway.cancel(ctx.session, task_id)` when one exists and is not terminal.
  - `resume`:
    - `police_result` with outcome passed or failed -> route accordingly, output
      `{outcome, summary, findings_count}` from `signal.data`;
    - outcome `error` -> route error;
    - `timer_fired` -> route error with output `{timed_out: true}`.

## Key Pitfalls

- Templates render only masked context. Still run `Redactor` on every rendered string
  that leaves the process (prompt, notification text, police instructions).
- The LLM call happens inside the step transaction. It must be read-only and safe to
  repeat. Never enqueue or write anything except through the passed session and ports.
- Never put the prompt or the LLM raw response in errors or logs.

## Tests (`tests/workflow/executors/`)

Build `StepContext` objects directly (SQLite session from the harness or a dummy
session where not used) with spec dataclasses from `telescreen.workflow.dsl.model`.

- detect: fake rescanner found, clean or error -> the matching route; no issue -> error
  route; a step with `detectors: [rule, managed_llm]` passes that tuple to the
  rescanner.
- ai_agent: a fake client returning a valid object -> output typed and routed next; a
  response with a sample secret in a string field -> stored output redacted; invalid
  output 3 times -> `llm_output_invalid`; the prompt passed to the client contains the
  rendered issue short id and no raw sample even when the context had one; local
  provider without a client -> `llm_unavailable`; a provider error -> retryable failure.
- notify: a recording fake sender -> correct request; blocked receipt -> `queued false`;
  `recipient_unknown` -> failed.
- police_verify: dispatch called with a deadline of now plus timeout and an input
  containing the issue fields; resume passed -> passed route; `timer_fired` -> error
  route with `timed_out`.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/workflow/executors/detect.py src/telescreen/workflow/executors/ai_agent.py src/telescreen/workflow/executors/notify.py src/telescreen/workflow/executors/police_verify.py tests/workflow/executors"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/workflow/executors 2>&1 | tee /tmp/telescreen-p25-workflow-nodes-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p25-workflow-nodes-typelint.log 2>&1; grep -E '^(src/telescreen/workflow/executors/(detect|ai_agent|notify|police_verify)\.py|tests/workflow/executors)' /tmp/telescreen-p25-workflow-nodes-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] Four executors delivered with the exact names and `step_type` values
- [ ] Tests above pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
