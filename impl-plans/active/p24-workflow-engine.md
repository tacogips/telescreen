# P24 Durable Workflow Engine, Queue, Scheduler, Human Tasks, Escalation and Workflow APIs Implementation Plan

**Status**: Completed (implementation, run A session 338; formal review downstream)
**Design Reference**: design-docs/specs/design-workflow-engine.md (Engine semantics, Queue and worker, Human tasks SLA and escalation, Triggers), design-docs/specs/notes.md (polling queue rationale), design-docs/specs/design-api-and-ui.md (Endpoints: Workflows, Runs, Human tasks, Escalation)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p24-workflow-engine`
- planPath: `impl-plans/active/p24-workflow-engine.md`
- wave: 4
- dependsOn: `p06-workflow-dsl`, `p08-masking`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p24-workflow-engine.md`
  - `src/telescreen/workflow/engine`
  - `src/telescreen/workflow/queue`
  - `src/telescreen/workflow/scheduler`
  - `src/telescreen/workflow/escalation`
  - `src/telescreen/workflow/triggers`
  - `src/telescreen/workflow/executors/__init__.py`
  - `src/telescreen/workflow/executors/flow.py`
  - `src/telescreen/workflow/executors/human.py`
  - `src/telescreen/api/routers/workflows.py`
  - `src/telescreen/api/routers/runs.py`
  - `src/telescreen/api/routers/human_tasks.py`
  - `src/telescreen/api/routers/escalation.py`
  - `tests/workflow/engine`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. This is the largest plan. Work in the task order
below and keep every file under 1000 lines (split the engine into the listed modules).

## Intent and Context

This plan builds the self-built durable engine (acceptance: "Workflow engine executes
YAML/JSON-defined workflows with DB-persisted state including branching, send-back,
human tasks with SLA escalation (slack/email), evidence gate, police verification,
timers"). All pending work is an `engine_tasks` row, polled with `FOR UPDATE SKIP LOCKED`
(no LISTEN/NOTIFY in v1). Executors return `StepOutcome`s and receive `StepSignal`s; only
the engine touches the queue. Node executors with external effects (detect, ai_agent,
notify, police_verify) come from p25, and the `map` executor from p26. They are
registered by assembly.

## Non-goals

- No detect, ai_agent, notify, police_verify or map executors (p25, p26).
- The `police_timeout` task kind is not used: `police_verify` waits with
  `timer_at = deadline` and is resumed by `timer_fired` (planning decision; same
  behavior).

## Task 1: Queue (`workflow/queue/`)
- `sql_queue.py`: `SqlTaskQueue(clock)` implements `TaskQueue`:
  - `enqueue`: inserts a `ready` row. A duplicate `idempotency_key` returns False (check
    first, then catch `IntegrityError` in a savepoint).
  - `cancel_by_ref` sets ready and leased tasks with that `ref_id` to `cancelled`.
  - Extra methods:
    - `claim(session, worker_id, kinds, limit, lease: timedelta) -> list[ClaimedTask]`:
      `status='ready' AND available_at<=now`, ordered by `available_at`, `limit`,
      `with_for_update(skip_locked=True)`. Claimed rows become `leased` with the owner,
      `lease_expires_at` and `attempts += 1`.
    - `apply_result(session, task_id, result: TaskResult)`: done -> `done`; retry ->
      `ready` with `available_at` and `last_error_code`, or `dead` when
      `attempts >= max_attempts`; dead -> `dead`.
    - `reclaim_expired(session, now) -> int`: expired leases go back to `ready`.
- `worker.py`:
  - `TaskHandlerRegistry` with `register(handler: TaskHandler)` and `get(kind)`.
  - `DeadTaskHook` Protocol: `async def on_dead(self, task: ClaimedTask) -> None`.
  - `EngineWorker(sessions, queue, registry, scheduler, clock, worker_id: str, concurrency: int, poll_seconds: float, dead_hook: DeadTaskHook | None, sleeper)`:
    - `run(stop: asyncio.Event)`: each loop reclaims leases, runs the scheduler tick,
      claims up to `concurrency`, runs the handlers concurrently, applies the results in
      their own transactions, and sleeps when idle.
    - `run_until_idle(max_rounds: int = 1000) -> int`: the test and `--once` helper.
      It processes everything ready at the current clock time, without sleeping.
  - A handler exception becomes `retry` with backoff `10 s * 2^(attempt-1)` and
    `internal_error`, logged with the task id and kind only.

## Task 2: Scheduler (`workflow/scheduler/`)
- `Scheduler(sessions, providers: tuple[ScheduleProvider, ...], clock)` with
  `tick() -> int`. Each provider runs in its own transaction; a provider exception is
  logged and does not stop the others.

## Task 3: Engine core (`workflow/engine/`)
- `repository.py`: runs, step runs, transitions and definitions queries (definitions are
  rehydrated with `compiled_from_json`).
- `context.py`: builds the initial `RunContext` from `IssueReader.get` (masked fields only)
  and `RunInfo`. Every step output is passed through `Redactor` (string leaves) before it
  is stored in `context_json` and `output_json`.
- `engine.py`: `RunEngine(definitions, executors: ExecutorRegistry, queue: SqlTaskQueue, issues_reader: IssueReader, issues: IssueRecorder, indexer: SearchIndexer, redactor: Redactor, audit: AuditLog, clock)`.
  It implements `RunSignaler` and offers:
  - `start_run(session, definition_id, issue_id, trigger: RunTrigger, parent_step_run_id: str | None) -> str`:
    run `running`, transition `start`, first step run `ready` with visit 1, an
    `advance_step` task (key `advance:<step_run_id>:<attempt>`), history
    `workflow_started` when there is an issue, and audit `run.started`.
  - `AdvanceStepHandler` (`advance_step` and `timer_fired`). The payload is
    `{step_run_id, signal?}`. Everything happens in ONE transaction:
    1. Load the step run. Without a signal it must be `ready`; with a signal it must be
       `waiting`. Otherwise acknowledge it as a duplicate.
    2. Mark it `running`.
    3. Call `execute`, or `resume(signal)`.
    4. Apply the outcome exactly as in the design:
       - completed: store the redacted output, mark `succeeded`, resolve the route
         (`route` or `next`), write a transition, create the next step run
         (visit = count of previous visits + 1) and enqueue it;
       - waiting: mark `waiting`; set the run `waiting` if no other step is active;
         `timer_at` -> a `timer_fired` task with a `{signal: timer_fired}` payload;
       - failed and retryable with attempts left -> the step goes back to `ready` and a
         new `advance_step` task is enqueued at `now + backoff * 2^(attempt-1)`;
       - otherwise route to `on_error`, or fail the run.
    5. Bump `lock_version` on run updates. A conflict -> `TaskResult.retry`.
  - Visit guard: entering a step more than `max_step_visits` times fails the run with
    `loop_limit_exceeded`.
  - `send_back`: counts previous uses against `max_send_backs` (else `on_exhausted`);
    writes a `send_back` transition with the redacted reason; cancels the open human
    tasks of steps visited after the target; creates a new visit of the target.
  - `parallel`: forks each branch with a fresh `branch_token`; branch steps inherit it.
  - `join`: records arrivals. `all` continues when every token of the fork has arrived;
    `any` continues on the first and cancels the other branches' waiting or ready steps.
  - `end`: the run becomes the step's `result` and `finished_at` is set. A child run
    enqueues `child_run_finished` for the parent (key `child:<run_id>`).
  - `signal(session, step_run_id, signal)` is idempotent: it is ignored unless the step
    is `waiting`, and otherwise enqueues an `advance_step` with the signal (key
    `signal:<step_run_id>:<kind>:<visit>`).
  - `cancel_run(session, run_id, actor_ref)` cancels waiting steps, open human tasks
    and queued tasks (`cancel_by_ref` for each step run id), sets the run `cancelled`,
    and audits it.
  - After every commit, call `indexer.index_run(run_id)`.
  - `DeadTaskHook` implementation: a dead `advance_step` fails its step with
    `task_dead` and routes to `on_error` or fails the run.
- `definitions.py`: `WorkflowService(redactor, configs: CollectorConfigReader, audit, clock)`:
  - `validate(source_format, text) -> list[ValidationIssue]` (DSL plus
    `redactor.has_hits(text)` -> code `raw_value_detected`);
  - `upload(session, source_format, text, actor_ref)` creates a new version (max + 1);
    `sub_workflow` targets must exist; `map` collectors must exist with a compatible
    kind; the stored `compiled_json` comes from `compiled_to_json`; audit
    `workflow.uploaded`;
  - `activate(session, name, version)`: exactly one active version per name;
  - `get`, `list` and `versions`.
- `child.py`: the `ChildRunFinishedHandler` signals the parent step with `child_finished`
  and the outcome (succeeded or failed).

## Task 4: Executors (`workflow/executors/`)
- `__init__.py`: `ExecutorRegistry` with `register(executor: StepExecutor)` and
  `get(step_type) -> StepExecutor` (missing -> `TelescreenError(workflow_invalid)`).
- `flow.py`:
  - `ConditionExecutor`: evaluates the cases in order and routes to the first true one,
    else `default`; a `type_mismatch` note is recorded in the transition reason.
  - `WaitExecutor`: `waiting(TIMER, now + duration)`; resume on `timer_fired` -> next.
  - `SendBackExecutor`: completed with the route set to `to`; the engine applies the
    send-back semantics.
  - `ParallelExecutor` and `JoinExecutor`: marker outcomes; the engine owns fork and join.
  - `SubWorkflowExecutor`: starts a child run through an injected `RunStarter`
    (the `RunEngine.start_run` bound method), waits with `CHILD`, and routes `child_finished`
    to the succeeded or failed outcome.
  - `EndExecutor`.
- `human.py`: `HumanTaskExecutor` and `EvidenceUploadExecutor(notifications: NotificationSender, queue: SqlTaskQueue, clock)`:
  - insert a `human_tasks` row (kind approval or evidence, rendered and redacted title,
    rendered assignee ref, `due_at = now + sla`, `tier_index=0`,
    `escalation_policy_id` resolved by name);
  - when `notify_on_assign`, send the assignee notification through `NotificationSender`
    (email channel, plus slack when the ref is a slack or team ref with a channel);
  - when there is an SLA, enqueue `sla_check` at `due_at` (key
    `sla:<human_task_id>:<tier>`);
  - return `waiting(HUMAN)`;
  - resume on `human_completed`: route via `outcomes[signal.outcome]` (evidence tasks
    route to `next`).

## Task 5: Escalation and triggers
- `escalation/service.py`: `EscalationPolicyService` with CRUD on `escalation_policies`
  (validated with `load_escalation_policy`; audit).
- `escalation/sla.py`: `SlaCheckHandler` (`sla_check`). If the task is still `open` and
  `tier_index < len(tiers)`:
  - move to the next tier (`assignee_ref = assign_to`);
  - notify through `NotificationSender` for each channel;
  - `issues.add_history(sla_breached)` and audit `sla.escalated`;
  - set `due_at = now + tier.after` and enqueue the next `sla_check`.
  After the last tier: record a final `sla_breached` and schedule nothing more.
- `triggers/service.py`: `WorkflowTriggerService(engine, definitions)` implements
  `IssueEventSink`. For each active definition (latest active per name) whose trigger
  `on` and `match` accept the event (sources, rule classes, min severity), start a run
  unless a non-terminal run of that workflow name exists for the issue.

## Task 6: Routers
- `routers/workflows.py`: `WorkflowsRouterDeps(ctx, service: WorkflowService, engine: RunEngine)`.
  - `GET /api/workflows`, `GET /api/workflows/{name}`,
    `GET /api/workflows/{name}/versions/{v}` (source_text, compiled_json as a string),
    `GET /api/workflows/schema` (application/schema+json).
  - `POST /api/workflows` with `{source_format, source_text}` -> 201 with the version, or
    422 with `{valid:false, errors:[{path,code}]}`.
  - `POST /api/workflows/validate` -> `{valid, errors}`.
  - `POST /api/workflows/{name}/activate` with `{version}`.
  - `POST /api/workflows/{name}/runs` with `{issue_id?}` (write) -> `{run_id}`.
- `routers/runs.py`: `GET /api/runs?status&workflow&issue_id` and
  `GET /api/runs/{id}` (steps with `input_json` and `output_json` strings, transitions,
  human tasks with their declared `outcomes`); `POST /api/runs/{id}/cancel`.
- `routers/human_tasks.py`:
  - `GET /api/human-tasks?assignee=me|<ref>&status`;
  - `POST /api/human-tasks/{id}/complete` with `{outcome, comment?}`. The caller must be
    the assignee or a member of the assignee team (`RecipientResolver.is_member_of`), or
    hold `admin`. The outcome must be declared. Evidence tasks need
    `EvidenceQuery.accepted_count(issue_id, step_run_id) >= min_files`, else 409
    `evidence_required`. The comment is redacted. Signal `human_completed`. Audit
    `human_task.completed`.
- `routers/escalation.py`: `GET` and `POST /api/escalation-policies` (read and admin),
  `PUT /api/escalation-policies/{name}` (admin); bodies carry `tiers_json` text plus
  `source_format`.

## Key Pitfalls

- Everything in `advance_step`, `sla_check` and `timer_fired` must be safe to roll back
  and repeat. External effects happen only through `NotificationSender` (which enqueues)
  and `PoliceGateway` (which enqueues). The read-only LLM call and rescan fetch inside
  p25 executors are the only allowed exceptions.
- Duplicate deliveries must be no-ops (status checks first).
- Never store unredacted step outputs, reasons or comments.
- Use `FakeClock` plus `run_until_idle` in every test. Never use real sleeps.

## Tests (`tests/workflow/engine/`)

Use the SQLite harness, fake ports (`IssueReader`, `IssueRecorder`, `NotificationSender`
recording requests, `EvidenceQuery`, `RecipientResolver`, `AuditLog`, `SearchIndexer`,
`CollectorConfigReader`) and stub executors for the p25 types (for example a stub
`police_verify` that waits for a signal).

- Linear run: start -> condition -> end succeeded; transitions recorded.
- Branching on `steps.x.output.flag` both ways.
- Send-back after a human rejection returns to triage with visit 2 and a masked reason;
  exceeding `max_send_backs` -> `on_exhausted`.
- Loop guard -> `loop_limit_exceeded`.
- Human task: SLA tier escalation at due time (`FakeClock.advance`) -> the assignee
  changes, a notification is recorded, history `sla_breached` is added; after the last
  tier, no more checks.
- Evidence task completion without accepted evidence -> 409; with evidence -> continues.
- Wait timer resumes after the duration and not before.
- Parallel with join `all` and `any` (`any` cancels the other branch).
- Sub-workflow: the child finishes -> the parent routes `succeeded`.
- Retryable executor failure -> retried with backoff, then success; a dead task -> step
  fails and routes to `on_error`.
- Duplicate `advance_step` delivery -> no double transition.
- Trigger service starts a run on `issue_created` that matches, and skips one already
  running.
- Cancel run cancels the open human task and the queued tasks.
- Workflow upload with a sample secret in a comment line -> 422 `raw_value_detected`;
  with an unknown sub_workflow -> 422.
- A step output containing a sample is stored redacted (`find_leaks` on
  `context_json` and `output_json` is empty).
- The SQLite queue claims respect `available_at`; `reclaim_expired` works.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/workflow/engine src/telescreen/workflow/queue src/telescreen/workflow/scheduler src/telescreen/workflow/escalation src/telescreen/workflow/triggers src/telescreen/workflow/executors/__init__.py src/telescreen/workflow/executors/flow.py src/telescreen/workflow/executors/human.py src/telescreen/api/routers/workflows.py src/telescreen/api/routers/runs.py src/telescreen/api/routers/human_tasks.py src/telescreen/api/routers/escalation.py tests/workflow/engine"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/workflow/engine 2>&1 | tee /tmp/telescreen-p24-workflow-engine-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p24-workflow-engine-typelint.log 2>&1; ! grep -E '(^|/)(src/telescreen/workflow/(engine|queue|scheduler|escalation|triggers)|src/telescreen/workflow/executors/(__init__|flow|human)\.py|src/telescreen/api/routers/(workflows|runs|human_tasks|escalation)\.py|tests/workflow/engine)' /tmp/telescreen-p24-workflow-engine-typelint.log   # no p24 diagnostics
```

## Run A Resume (2026-10-09, session 338)

State: interrupted at s319; all modules exist (19 tests). Dispatch:
`impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A2. Resume from the
in-tree code; do not rewrite `engine.py`. If an edit would push `engine.py` (712 lines)
near 1000, move send-back/join helpers into a new `workflow/engine/flow_ops.py`. New tests
go into new files (for example `tests/workflow/engine/test_engine_runA.py`,
`test_routers.py`). Static gap review (s338 planning) found:

- [x] R0 Baseline: untouched-tree scoped format, Ruff, ty and pytest passed; logs are
  `/tmp/telescreen-p24-workflow-engine-baseline-{format,ruff,ty,pytest,typelint}.log`.
- [x] R1 Send-back cancels open later-step human tasks and their SLA tasks; masked reason regression passes.
- [x] R2 Run advances use `UPDATE ... WHERE id AND lock_version`; conflict retries do not consume task attempts, including six consecutive stale rounds, and later advance normally.
- [x] R3 Step retry limit/backoff is honored; max-attempt exhaustion routes to `on_error`.
- [x] R4 Child failures from the loop guard and child cancellation notify the parent; missing-route terminal paths notify too.
- [x] R5 Worker invokes the dead hook when applying a retry produces final `dead` status.
- [x] R6 Trigger dedupe joins active runs by workflow name across definition versions.
- [x] R7 Redacted assignee storage, condition mismatch transition reason, sweep-capability validation, and explicit queue reclaim time are covered.
- [x] R8 Upload and router masking, join-any single continuation, workflow/run/escalation routes and scopes, sensitive escalation rejection, and metadata-only upload audit are covered.
- [x] R9 Final scoped gates and line counts are recorded below; all files remain under 1,000 lines.

## Completion Criteria

- [x] Queue, worker, scheduler, engine, definitions, flow and human executors, escalation, triggers and the four routers delivered with the exact names
- [x] Every test case above passes, plus R1-R8
- [x] No file over 1000 lines (`wc -l` recorded on the final tree)
- [x] Run A Resume items R0-R9 checked with evidence
- [x] Scoped verification passes on the final tree; logs recorded
- [x] Progress Log updated (run A session)

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 Workflow Engine Implementation
**Tasks Completed**: Delivered the durable SQL queue and worker, scheduler, workflow repository/definition service/engine/context/child handler, flow and human executors, escalation and trigger services, and workflow/run/human-task/escalation routers. Added SQLite queue and workflow behavior tests for idempotent/due claims and expired leases, linear transitions, wait/parallel joins, raw-value rejection, retry/redaction and duplicate delivery.
**Tasks In Progress**: Add the remaining plan-listed behavior tests, including both condition routes, send-back/exhaustion, loop guard, human-task authorization/evidence and SLA escalation, child workflow completion, dead-task/on-error routing, issue triggers, and run cancellation.
**Blockers**: None.
**Notes**: Scoped format, Ruff, ty, pytest (6 passed), and typelint checks passed. Full-tree typelint passed (248 files). `wc -l` recorded 3,573 total lines across the p24 Python/test files and plan; largest Python file is `src/telescreen/workflow/engine/engine.py` at 711 lines, and plan is 277 lines. Logs are in `tmp/telescreen-v1-20261008-s319/p24-workflow-engine/`; branch reviews and final integration checks are downstream workflow steps.

### Session: 2026-10-09 Workflow Engine Test Completion
**Tasks Completed**: Added regression tests for both condition routes, human rejection/send-back and exhaustion, loop limits, SLA tier escalation and final breach, evidence authorization and accepted-file gates, raw comment redaction, child completion, issue trigger deduplication, dead-task error routing, run cancellation (including human-task SLA work), and unknown sub-workflow rejection. Added `EvidenceUploadExecutor` under `StepType.EVIDENCE_UPLOAD`. Fixed cancellation to cancel queued SLA tasks by human-task id.
**Tasks In Progress**: None. Formal branch and adversarial reviews and combined-tree integration checks are downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: Before: plan `0d06ac1353d3fad03f2aae0611a4ab838ec841c6b35177b69a41b37d124fa2de`; engine `8d62bede2c91e4590b5719230353a1479ddb1966810994abc6ff2af12e9abd66`; human executor `b77492d5e196ae1ffc00e94760086c54efc98f483c7dd92c336ed7aba35115da`; extended tests `68290b8f2e60391424fd035b358c6fc425d3299fbb7d3d48bff75ca6465463d8`. After hashes are recorded in `tmp/telescreen-v1-20261008-s319/p24-workflow-engine/source-hashes-continuation-final.sha256`.
**Verification**: Using the exact p24 `P` scope from the Verification section: `uv run --frozen ruff format --check --no-cache $P` -> exit 0 (`ruff-format-continuation-final.log`); `uv run --frozen ruff check --no-cache $P` -> exit 0 (`ruff-check-continuation-final.log`); `uv run --frozen ty check $P` -> exit 0 (`ty-continuation-final.log`); `uv run --frozen python tools/typelint.py <p24 scope>` -> exit 0, 20 files clean (`typelint-scoped-continuation-final.log`); `uv run --frozen python tools/typelint.py` -> exit 0, 249 files clean (`typelint-full-continuation-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/workflow/engine` -> exit 0, 19 passed (`pytest-continuation-final.log`); no-dynamic-evaluation grep -> exit 0; `wc -l <p24 paths>` -> exit 0, 4,389 lines, all files under 1,000 (`wc-lines-continuation-post-plan.log`). Logs are under `tmp/telescreen-v1-20261008-s319/p24-workflow-engine/`.
**Notes**: All files remain below 1,000 lines; `engine.py` is 712 lines, `test_engine_extended.py` is 793 lines, and this plan is 292 lines. One test exposed that `cancel_run` left the human task's SLA check ready; the fix cancels that task. Earlier failed fixture iterations and the pre-fix cancellation failure are preserved in the evidence directory; final-source scoped pytest passed. Codex agent `/root/p24_port_audit` performed a read-only API/model audit and made no edits. Workflow mode is `issue-resolution`; issue URL, number and repository were not supplied. Review gates remain owned by later workflow steps.

### Session: 2026-10-09 Run A implementation resume (session 338)
**Tasks Completed**: Closed resume gaps R0-R9: optimistic run version checks; send-back cancellation and redaction; configured retry/backoff; child terminal propagation; final dead-hook dispatch; cross-version trigger dedupe; safe assignee, condition note, map sweep validation and metadata-only workflow audit; expanded API and behavior regressions.
**Tasks In Progress**: None for p24 implementation. Formal integrity/adversarial review and serial integration/finalization are downstream.
**Blockers**: None within p24 writePaths.
**Reconciliation requests**: None.
**Hashes**: Pre-edit snapshot hashes are in the runtime fanout evidence and `pre-edit-hashes.sha256`; final source hashes: `tmp/telescreen-v1-20261009-s338/p24-workflow-engine/source-hashes-final.sha256`. Per-edit intents and snapshots are under the same evidence directory.
**Verification**: Scoped format/check, Ruff, ty and `pytest -p no:cacheprovider tests/workflow/engine` passed (31 tests); complete logs: `/tmp/telescreen-p24-workflow-engine-{format-check-final,ruff-final,ty-final,pytest-final}.log`. Filtered p24 typelint has no diagnostics; complete log `/tmp/telescreen-p24-workflow-engine-typelint-final.log`. Final line-count evidence `/tmp/telescreen-p24-workflow-engine-wc-lines-final-plan-update.log` (largest p24 Python file 793 lines; plan 323 lines; no file >=1000).
**Notes**: The full-tree typelint process returned exit 1 for three unrelated TL003 findings owned by p26 in `src/telescreen/workflow/mapreduce/{partition,shard,sink}.py`; p24 filtered typelint is clean. Workflow mode `issue-resolution`; issue reference is `workflowInput` (no issue URL/number); execution `opus-luna-design-and-implement-review-loop-session-338`; Codex read-only audit reference `/root/p24_port_audit`. Formal review, shared documentation/index changes, exact-file staging, commit and push remain downstream.

### Session: 2026-10-09 Adversarial repair P24-ADV-01
**Tasks Completed**: `SqlTaskQueue.apply_result` reschedules `ErrorCode.CONFLICT` retries as READY, records the conflict code and refunds the claim attempt; non-CONFLICT retries retain their bounded retry behavior. Added queue-level limit-boundary coverage and a worker/engine regression covering six stale CAS rounds followed by successful run completion. Corrected the scoped typelint filter to match absolute tool paths.
**Tasks In Progress**: None for p24 implementation; independent review and serial finalization remain downstream.
**Blockers**: None.
**Hashes**: Queue `src/telescreen/workflow/queue/sql_queue.py` before `f3dd823169e38da29db924192c25cdb76bbf24dd74c30414b058e37918e3e69f`, after `3bed133af38dbb9f1823abe408ef183eb8f4ac0e7ff0f089406795b2ed2d12b8`; tests `tests/workflow/engine/test_resume_gaps.py` before `7f3f2f7e8f57566ad68a24394c39e86214efff7266abfff7c4e142e643475b3a`, after `b3548d1cc40472d3b4ef2ea94bf87501228cb344ed190c94283507e5de6a0c9b`. Plan pre-edit hash `6cba00caf011aaf729d645b056ccc20c2d92855f46e81509a66610ee796369e8`; final plan hash is recorded in `tmp/telescreen-v1-20261009-s338/p24-workflow-engine/source-hashes-adv-conflict-final.sha256`.
**Verification**: Focused new regressions -> exit 0, 2 passed (`/tmp/telescreen-p24-adv-conflict-focused.log`). Final bash-scoped format -> exit 0 (`/tmp/telescreen-p24-adv-conflict-format-final2.log`); Ruff -> exit 0 (`/tmp/telescreen-p24-adv-conflict-ruff-final2.log`); ty -> exit 0 (`/tmp/telescreen-p24-adv-conflict-ty-final2.log`); `pytest -p no:cacheprovider tests/workflow/engine` -> exit 0, 33 passed (`/tmp/telescreen-p24-adv-conflict-pytest-final2.log`); full typelint -> exit 0, 253 files clean, and absolute-path scoped filter -> exit 0 (`/tmp/telescreen-p24-adv-conflict-typelint-full.log`); `wc -l` and `<1000` gate -> exit 0, largest p24 file `test_resume_gaps.py` at 895 lines (`/tmp/telescreen-p24-adv-conflict-wc-lines.log`); no unchecked plan boxes -> exit 0.
**Prior Attempts**: The first static-check invocation under zsh treated the space-separated scope as one path and exited 2/1/2 (format/Ruff/ty); rerun under bash resolved it. The first bash format check found one line requiring reflow (exit 1); the final format check passes. Logs: `/tmp/telescreen-p24-adv-conflict-{format,ruff,ty,pytest}.log` and `/tmp/telescreen-p24-adv-conflict-{format,ruff,ty,pytest}-final.log`.
**Notes**: Changes remain within p24 `writePaths`; no ports, vocabulary or p26 files changed. Workflow mode `issue-resolution`; issue reference `workflowInput` (no URL/number); execution `nested-v1-769462c539ba1177c5a573360acb57cef539bc041899e652497bf3042632a3d7`; Codex read-only audit reference `/root/p24_port_audit`. Review approval is pending downstream.
