# P06 Workflow DSL, Expressions and Step API Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-workflow-engine.md (Document schema, Expressions and templates, Ports used by executors, Human tasks escalation policy), design-docs/specs/design-map-reduce-sweeps.md (DSL step type `map`)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p06-workflow-dsl`
- planPath: `impl-plans/active/p06-workflow-dsl.md`
- wave: 3
- dependsOn: `p01-foundation`, `p04-ports`
- writePaths:
  - `impl-plans/active/p06-workflow-dsl.md`
  - `src/telescreen/workflow/__init__.py`
  - `src/telescreen/workflow/dsl`
  - `src/telescreen/workflow/step_api.py`
  - `tests/workflow/dsl`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan builds the offline-validatable workflow language (`telescreen.workflow/v1`),
covering:

- the typed compiled model;
- the safe expression and template language;
- the escalation-policy document;
- the executor contract that the engine (p24), node executors (p25) and the map step
  (p26) implement.

It is pure: no database, no masking and no network. Redactor checks on source text are
applied by callers (API in p24, CLI in p28).

## Non-goals

- No engine, queue or persistence. No `sub_workflow` existence check (the API does it).
- No `map` `source.items` and no `map.via: sub_workflow` (dropped in v1; reject them
  with code `unsupported_field`).

## Deliverables

### `src/telescreen/workflow/__init__.py`
Docstring only.

### `dsl/model.py` (frozen dataclasses; contract for p24/p25/p26/p29)

- `RetrySpec(max_attempts: int, backoff: timedelta)`. `TriggerMatch(sources: tuple[str, ...], rule_classes: tuple[RuleClass, ...], min_severity: Severity | None)`.
  `TriggerSpec(on: tuple[IssueEventKind, ...], match: TriggerMatch)`.
  `Defaults(max_step_visits: int = 20, retry: RetrySpec | None = None)`.
- Common step fields: `id: str`, `name: str | None`, `next: str | None`,
  `retry: RetrySpec | None`, `on_error: str | None`.
- One dataclass per type:
  - `DetectStep(detectors: tuple[DetectorKind, ...] | None, outcomes: Outcomes3)`, where
    `Outcomes3` routes the outcome names found, clean and error to step ids.
  - `AiAgentStep(provider: AgentProvider (managed|local), model: str | None, prompt: str, output: tuple[OutputField, ...])`
    with `OutputField(name, type: OutputType (string|boolean|number|enum), values: tuple[str, ...])`.
  - `ConditionStep(cases: tuple[ConditionCase(when: Expression, next: str), ...], default: str)`.
  - `HumanTaskStep(title: str, assignee: str, outcomes: tuple[NamedRoute(name, to), ...], sla: timedelta | None, escalation_policy: str | None, notify_on_assign: bool = True)`.
  - `NotifyStep(channel: NotificationChannel, to: tuple[str, ...], subject: str, body: str)`.
  - `EvidenceUploadStep(title, assignee, min_files: int = 1, sla, escalation_policy)`.
  - `PoliceVerifyStep(task_type: str, labels: tuple[str, ...], input: tuple[NamedTemplate(name, template), ...], timeout: timedelta, outcomes)`
    with routes for passed, failed and error.
  - `WaitStep(duration: timedelta)`.
  - `SendBackStep(to: str, reason: str, max_send_backs: int = 3, on_exhausted: str | None)`.
  - `ParallelStep(branches: tuple[str, ...], join: str)`. `JoinStep(mode: JoinMode (all|any))`.
  - `SubWorkflowStep(workflow: str, version: int | None, outcomes)` with routes for
    succeeded and failed.
  - `MapStep(partition_via: MapVia (collector|police), collector: str, partition_task_type: str | None, partition_labels: tuple[str, ...], map_via: MapVia, map_task_type: str | None, map_labels: tuple[str, ...], max_concurrency: int = 16, shard_timeout: timedelta, shard_max_attempts: int = 3, max_failed_ratio: float, max_failed_items: int, timeout: timedelta, outcomes)`
    with routes for succeeded, partial and failed.
  - `EndStep(result: RunStatus)`, restricted to succeeded, failed and cancelled.
- `type StepSpec = DetectStep | AiAgentStep | ... | EndStep`. Each class has
  `step_type: ClassVar[StepType]`.
- `CompiledWorkflow(name, description, trigger: TriggerSpec | None, defaults: Defaults, start: str, steps: tuple[StepSpec, ...])`
  with `step(step_id) -> StepSpec` (raises `KeyError` if missing).

### `dsl/loader.py`
- `load_workflow(text: str, source_format: SourceFormat) -> CompiledWorkflow`: parse with
  `parse_yaml` or `parse_json`, check `apiVersion` = `telescreen.workflow/v1` and
  `kind` = `Workflow`, build the model, run `validate_workflow`, and raise
  `WorkflowValidationError` with every issue found (not only the first).
- `compiled_to_json(workflow: CompiledWorkflow) -> JsonValue` and
  `compiled_from_json(value: JsonValue) -> CompiledWorkflow`, used for the
  `workflow_definitions.compiled_json` round trip (durations as ISO strings).
- `ValidationIssue(path: str, code: str)` where `path` is a JSON pointer such as
  `/steps/3/next`. `class WorkflowValidationError(TelescreenError)` with the field
  `issues: tuple[ValidationIssue, ...]` (code `workflow_invalid`, static detail).
- Issue codes (closed set): `invalid_document`, `unsupported_api_version`,
  `missing_field`, `invalid_type`, `invalid_value`, `unsupported_field`, `duplicate_id`,
  `unknown_step`, `unreachable_step`, `invalid_send_back`, `invalid_parallel`,
  `empty_outcomes`, `invalid_expression`, `invalid_template`, `invalid_duration`.

### `dsl/validate.py`
- `validate_workflow(workflow: CompiledWorkflow) -> list[ValidationIssue]` implements
  every static rule in the design:
  - ids are slugs (`^[a-z][a-z0-9_-]{0,63}$`) and unique;
  - `start` and every routing target exist;
  - every step is reachable from `start`;
  - `send_back.to` can reach the `send_back` step (a path from target to step exists);
  - every `parallel` branch reaches the named `join`, and no branch reaches an `end`
    without passing it;
  - `human_task.outcomes` is not empty;
  - expressions and templates parse and use known roots;
  - `map` uses `map_via == partition_via`, and `via: police` needs task types on both
    sides.

### `dsl/expressions.py` and `dsl/templates.py`
- A hand-written tokenizer and recursive-descent parser (NO `eval`, `ast.literal_eval`
  or `compile`). The grammar is in the design. `parse_expression(text) -> Expression`
  raises `TelescreenError(expression_error)`.
- `evaluate(expr: Expression, ctx: RunContext) -> EvalResult(value: JsonValue, note: str | None)`:
  - an unknown path gives null;
  - a type mismatch in a comparison gives false, with `note = "type_mismatch"`;
  - `and`/`or` short-circuit;
  - `in` accepts list literals or array values.
- `parse_template(text) -> Template` (`{{ path }}` only, no logic).
  `render(template, ctx) -> str` (null renders as an empty string, numbers render via
  their JSON text).
- Allowed roots: `issue.<field>`, `run.<field>`, `steps.<id>.outcome`,
  `steps.<id>.output.<field...>`.

### `dsl/context.py`
- `IssueContext(number: int, short_id: str, title: str, status: str, source: str, rule_class: str, severity: str, display_locator: str, times_seen: int, first_seen: str, last_seen: str)`.
- `RunInfo(id: str, workflow: str, started_at: str)`.
- `StepResultContext(step_id: str, outcome: str | None, output: JsonValue)`.
- `RunContext(issue: IssueContext | None, run: RunInfo, steps: tuple[StepResultContext, ...])`
  with `lookup(path: tuple[str, ...]) -> JsonValue | None`, `with_step(result) -> RunContext`
  (replaces the latest visit), and `to_json() -> JsonValue` / `from_json(value) -> RunContext`
  (for the `workflow_runs.context_json` column). Only masked values are ever placed here
  (callers ensure this).

### `dsl/escalation.py`
- `EscalationTier(after: timedelta, assign_to: str, notify: tuple[str, ...], channels: tuple[NotificationChannel, ...])`.
- `EscalationPolicySpec(name: str, tiers: tuple[EscalationTier, ...])`.
- `load_escalation_policy(text: str, source_format: SourceFormat) -> EscalationPolicySpec`
  and the to/from JSON helpers.
- Recipient refs are validated with the shape `^(member|team|email|slack):.+$`.

### `dsl/workflow-v1.schema.json` and `dsl/schema.py`
A JSON Schema of the document for the UI editor, plus `workflow_schema_text() -> str`
(read with importlib.resources). The Python loader stays authoritative.

### `src/telescreen/workflow/step_api.py` (executor contract; frozen)

```python
class StepOutcomeKind(StrEnum): COMPLETED = "completed"; WAITING = "waiting"; FAILED = "failed"
class WaitKind(StrEnum): HUMAN = "human"; TIMER = "timer"; POLICE = "police"; CHILD = "child"; JOIN = "join"; MAP = "map"
```

- `StepOutcome(kind, output: JsonValue, route: str | None, wait_kind: WaitKind | None, timer_at: datetime | None, error_code: ErrorCode | None, retryable: bool)`
  with static constructors `completed(output, route=None)`,
  `waiting(wait_kind, timer_at=None)`, `failed(error_code, retryable)`.
- `StepContext(session: AsyncSession, run_id: str, step_run_id: str, step: StepSpec, visit: int, attempt: int, context: RunContext, issue_id: str | None, now: datetime)`.
- Protocol:

```python
class StepExecutor(Protocol):
    step_type: StepType
    async def execute(self, ctx: StepContext) -> StepOutcome: ...
    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome: ...
```

  (`StepSignal` comes from `telescreen.ports.workflow`.)

## Tests (`tests/workflow/dsl/`)

- A full valid YAML workflow with every step type (map via collector, police_verify,
  parallel and join, send_back) loads; the `compiled_to_json`/`compiled_from_json` round
  trip gives an equal object.
- The JSON format of the same document loads identically.
- Each validation rule has a failing document -> the expected code and path, including
  several issues reported at once.
- `map` with `items:` -> `unsupported_field` at `/steps/N/source/items`; `map.via: sub_workflow` -> `unsupported_field`.
- Expressions: `steps.verify.output.is_credential == true and issue.severity == "high"`
  -> true; `issue.times_seen > "x"` -> false with note `type_mismatch`;
  `steps.x.outcome in ["found", "clean"]` works; `__import__('os')` -> expression_error;
  an unknown root `env.HOME` -> invalid_expression at validation.
- Template `"Issue {{ issue.short_id }}"` renders; an unknown path renders as empty.
- Escalation policy example from the design loads; an invalid ref `bob` -> invalid_value.
- `parse_duration` errors map to `invalid_duration` with the step path.
- The `workflow-v1.schema.json` loads as Draft 2020-12 and accepts the valid document.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/workflow/__init__.py src/telescreen/workflow/dsl src/telescreen/workflow/step_api.py tests/workflow/dsl
uv run --frozen ruff check --no-cache src/telescreen/workflow/__init__.py src/telescreen/workflow/dsl src/telescreen/workflow/step_api.py tests/workflow/dsl
uv run --frozen ty check src/telescreen/workflow/__init__.py src/telescreen/workflow/dsl src/telescreen/workflow/step_api.py tests/workflow/dsl
uv run --frozen pytest -p no:cacheprovider tests/workflow/dsl 2>&1 | tee /tmp/telescreen-p06-workflow-dsl-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p06-workflow-dsl-typelint.log 2>&1; ! grep -E '(/src/telescreen/workflow/(__init__|step_api)\.py|/src/telescreen/workflow/dsl/|/tests/workflow/dsl/)' /tmp/telescreen-p06-workflow-dsl-typelint.log
```

## Completion Criteria

- [x] Model, loader, validator, expressions, templates, context, escalation, schema and step_api delivered with the exact names
- [x] No `eval`, `exec`, `compile` or `literal_eval` in `src/telescreen/workflow/dsl` (only regex `re.compile` is present)
- [x] `step_type: ClassVar[StepType]` passes the scoped `ty check`; no suppression or per-file-ignore was added
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 21:00 JST
**Tasks Completed**: Implemented the typed workflow model and step API, YAML/JSON loader and compiled JSON round trip, static graph validation, safe expression parser/evaluator, path-only templates, masked run context serialization, escalation policy parsing and round trip, JSON Schema resource, and tests covering every step type and the assigned edge cases.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: New source/test paths were absent before this session; final SHA-256 values are recorded in `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/final-source-hashes.sha256`. The plan hash before this progress update was `a2ec265be015ce04e19cf31d1b9e846b95bffe1ef72549121ff10ba037063aaa`; final plan hash is recorded separately in `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/plan-hash.sha256`.
**Verification**: Scoped Ruff format -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-format-final.log`); scoped Ruff check -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-ruff-final.log`); scoped ty -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-ty-final.log`); DSL pytest -> exit 0, 8 passed (`/tmp/telescreen-p06-workflow-dsl-pytest-final.log`); full typelint -> exit 1, 47 diagnostics in other concurrent paths (`/tmp/telescreen-p06-workflow-dsl-typelint-final.log`); p06 filter over that output -> exit 0, no diagnostics; no-dynamic-evaluation grep -> exit 0.
**Notes**: A focused map rejection test first exposed that `source.partition.items` was not rejected; the parser was corrected to return `unsupported_field`, and the final 8-test run passes. The entire shared tree's typelint run remains nonzero for 47 diagnostics outside this plan; scoped typelint reports all 12 p06 files clean. No review decision is claimed; formal review and final integration are downstream.

### Session: 2026-10-08 self-check repairs
**Tasks Completed**: Corrected `CompiledWorkflow.step()` to raise `KeyError` for missing IDs; equality and inequality comparisons now return false with `type_mismatch` for unlike JSON kinds; map inline-item errors report the nested JSON pointer; unknown graph routes report their exact field pointers; malformed retry/failure values and negative map failure counts are rejected. Added assertions for these behaviors plus planned validation codes and paths, including duplicate IDs, unknown routes, unreachable steps, send-back and parallel graphs, empty outcomes, map bounds, invalid expressions/templates/durations, and multi-issue reporting.
**Tasks In Progress**: None for p06. Formal independent review and combined-tree integration are downstream workflow steps.
**Blockers**: None within p06. Full-repository typelint exits 1 with 60 diagnostics in other concurrent plan paths; p06 scoped typelint is clean.
**Hashes**: Final source hashes and verification are recorded in `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/final-source-hashes-010-selfcheck.sha256`; this entry's final plan hash is recorded in `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/plan-hash-010-selfcheck.sha256`.
**Verification**: Scoped Ruff format -> exit 0, 12 files already formatted (`/tmp/telescreen-p06-workflow-dsl-selfcheck-format.log`); scoped Ruff check -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-selfcheck-ruff.log`); scoped ty -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-selfcheck-ty.log`); DSL pytest -> exit 0, 11 passed (`/tmp/telescreen-p06-workflow-dsl-selfcheck-pytest-final.log`); scoped typelint -> exit 0, 12 files clean (`/tmp/telescreen-p06-workflow-dsl-selfcheck-typelint-scoped.log`); full typelint -> exit 1, 60 diagnostics outside p06 (`/tmp/telescreen-p06-workflow-dsl-selfcheck-typelint-full.log`), with zero p06 matches (`/tmp/telescreen-p06-workflow-dsl-selfcheck-typelint-filter.log`); prohibited-evaluation grep -> exit 0 (`/tmp/telescreen-p06-workflow-dsl-selfcheck-no-eval.log`); source hash check -> exit 0, 13/13 files match.
**Notes**: The self-check added regression coverage in place of relying on inspection for the named validation rules. The shared-tree typelint failures are in concurrent API, directory, and Google collector paths, not p06. No review decision is claimed; formal review and final integration are downstream.

### Session: 2026-10-09 06:55 JST
**Tasks Completed**: Revalidated the existing p06 workflow DSL implementation and recorded current-source verification evidence for the redispatch.
**Tasks In Progress**: Native test-integrity, adversarial and branch-evidence decisions for DISPATCH3-REVIEW-MISSING-p06 are pending the workflow's downstream native join.
**Blockers**: None for assigned implementation work.
**Reconciliation requests**: None.
**Hashes**: Source hash inventory: `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/final-source-hashes-012-redispatch.sha256`; verification manifest: `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/verification-012-redispatch.json`. Plan hash before this entry: `69d2c17a8827865b9cfbd74ad618bdf0d260c6abd9dd1e064d81222f0f4f436b`.
**Verification**: Scoped format, Ruff, `ty` and DSL pytest exit 0; 11 tests passed. Full typelint exits 1 with 47 diagnostics outside p06; scoped p06 filter exits 0 with 0 diagnostics. Prohibited dynamic-evaluation check exits 0. Complete logs are under `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/` and `/tmp/telescreen-p06-redispatch-*.log`.
**Notes**: No implementation source changes were needed. Source checksum verification passed for 13/13 p06 files; comparison with the before:step6-implement snapshot found 22 files checked and 0 mismatches. This entry does not claim a formal review decision; the three missing native decisions remain downstream.

### Session: 2026-10-09 07:07 JST — P06-ROUNDTRIP-LOSSY repair
**Tasks Completed**: Added shared `_iso.format_duration` and used it in workflow and escalation serialization; zero durations now serialize as `PT0S` and fractional seconds retain microsecond precision. Corrected expression string decoding, normalized malformed string/number errors to `expression_error`, and added exponent-number parsing. Added round-trip tests for omitted/zero/fractional wait durations, zero human SLA, quoted string literals in both quote styles, decimal/exponent floats with evaluation parity, malformed escapes, and zero/fractional escalation tiers.
**Tasks In Progress**: Independent Opus re-review of the Step 7 adversarial finding and branch evidence is pending downstream.
**Blockers**: None for p06 implementation.
**Reconciliation requests**: None.
**Hashes**: Pre-edit/final inventories: `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/pre-edit-hashes-013-roundtrip.txt` and `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/final-source-hashes-013-roundtrip.sha256`. Changed files: `_iso.py` before=absent after=recorded in inventory; `loader.py` before=dfb128a6f4f9bef42d5f5d5179d72488b257ce37884cd95263cd6dfdd846ad3f after=8281855dc04540bc289a9fce702351e719cc507b7252807af04201a980485ec6; `escalation.py` before=d554bcd8770d14961b1967cee559fc282d4cb6bb3a85a00233894b189775b523 after=ade07ad54e5aa5c23e049f12395fb481b7dae1a3c3af29ef2b5a503ab264f3ee; `expressions.py` before=94b6a4ef8411bbf4873bd13c057b6f34bb766d5b274cd99bb1697dc2b8d199fc after=420366dbd4c584e88d36d49692a692a7ae2b4314cc6810cb44a5f203776e8895; DSL tests before=74cbc423a01ad2ec6625751255111cf84085895dd701e765e1bc862148d327c8 after=3e3ca6740c4c1a28cc5db5fd6a6c982e2a27a1a13256919c088fb69c65d4a4da. Plan hash before this entry=4e73535f8b25cba482c6356924a9fb9cac7328bf6a88b1191d1c950b662de47a.
**Verification**: Final Ruff format, Ruff check, `ty`, DSL pytest (22 passed), direct p06 typelint (13 files clean), no-evaluation check, line cap and 14/14 SHA check all exit 0. Commands and complete logs are recorded in `tmp/telescreen-v1-20261008-s319/p06-workflow-dsl/verification-013-roundtrip.json`. Before the fix, the new targeted regression command exited 1 with 10 expected round-trip failures and one malformed-escape test passing; initial format/Ruff checks also exited 1 on formatting-only findings, all resolved before final verification.
**Notes**: `loader.py` remains under 1000 lines (956). The optional low `TEST-GAP-p06-RULES` suggestions (extra slug/map/police/parallel validation cases) remain outside this mid-finding repair. The adversarial decision remains rejected until independent review rechecks the corrected source; no branch-evidence or final combined-tree acceptance is claimed.
