# V11-P13 Workflow: Loader Split, owner_review Human Task, detect Inconclusive, Escalation min_risk

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 5 (`detect` node), 7 (`owner_review` human task, delivery), 10 (escalation `min_risk`), 14 (loader split)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p13-workflow`
- planPath: `impl-plans/active/v11-p13-workflow.md`
- wave: 2
- dependsOn: `v11-p01-foundation`
- writePaths:
  - `impl-plans/active/v11-p13-workflow.md`
  - `src/telescreen/workflow/dsl`
  - `src/telescreen/workflow/executors/human.py`
  - `src/telescreen/workflow/executors/detect.py`
  - `src/telescreen/workflow/escalation`
  - `tests/workflow/test_v11_workflow.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

- `workflow/dsl/loader.py` is 956 lines and must be split before it grows. Move the
  compiled JSON serialization (`compiled_to_json`, `compiled_from_json` and the
  `_*_json` helpers, from line ~647 on) into a new `workflow/dsl/compiled.py`, and
  re-export the two public functions from `loader.py` so all imports keep working.
- A human task gets an optional `kind: owner_review`: fixed outcomes, the assignee
  reference `issue:assignee` resolved from the issue snapshot, and owner-friendly
  delivery.
- The `detect` executor maps `RescanKind.INCONCLUSIVE` to outcome `error` with
  `scan_incomplete`.
- Escalation tiers accept `min_risk`, and the SLA handler stops escalating below it.

The completion side effects of `owner_review` (verification, exception, release) are done
by the human-tasks router in v11-p18 through the `IssueRemediation` port, not here.

## Non-goals

- No change to the engine (`workflow/engine/**`), queue, map-reduce, triggers (v11-p01
  owns the triggers `severity_rank` fix) or the API routers.
- No new step type: `owner_review` is a `human_task` attribute.

## Invariants

- Existing workflow definitions (YAML and JSON in `examples/workflows/**`, test
  fixtures) load and compile byte-identically. Compiled JSON for steps without `kind`
  omits the key.
- `compiled_from_json` of v1-stored `compiled_json` still works (no `kind` -> approval).
- `loader.py` and every new file stay under 1000 lines.

## Deliverables

1. **Loader split**: new `workflow/dsl/compiled.py` containing the moved serialization
   code unchanged. `loader.py` imports and re-exports `compiled_to_json` and
   `compiled_from_json`. Check every importer with
   `git grep -n "compiled_to_json\|compiled_from_json\|json_float_value"` and keep all
   import paths valid.
2. **`model.py`**: `HumanTaskStep` gains `kind: HumanTaskKind = HumanTaskKind.APPROVAL`,
   appended after `notify_on_assign`.
3. **`loader.py` `_read_step`**, human task branch:
   - Parse optional `kind` (`approval` or `owner_review` only; other values ->
     `ValidationIssue(path/kind, "invalid_value")`).
   - For `owner_review`, the outcome names must be exactly `{remove, restrict, exception,
     not_mine}`; otherwise `ValidationIssue(path/outcomes, "owner_review_outcomes")`.
   - `assignee` may be the literal `issue:assignee`. Allow it in any validation that
     checks recipient-ref syntax (see `validate.py` and `_RECIPIENT` patterns).
   - `trigger.match.min_severity` accepts `critical` (enum parsing already uses
     `Severity`).
4. **`compiled.py`**: serialize `kind` for owner_review steps only.
5. **`workflow-v1.schema.json`**: add `kind` (enum `approval`, `owner_review`) to the
   human_task step properties if the schema lists step properties, and `critical` to any
   severity enum. Keep `tests/workflow/dsl` schema tests passing.
6. **`escalation.py` (dsl)**: `EscalationTier` gains `min_risk: int | None = None`.
   - `_tier` parses an optional integer from 0 to 100; a bad value -> `ValidationIssue`.
   - `escalation_to_json` and `escalation_from_json` handle it (omitted when None).
7. **`executors/human.py`**:
   - The `execute` HumanTaskStep branch uses `step.kind`.
   - When `assignee == "issue:assignee"`, it resolves through an optional dataclass field
     `issues: IssueReader | None = None`: `snapshot.assignee_ref`. A missing reader, issue
     or assignee -> `StepOutcome.failed(ErrorCode.RECIPIENT_UNKNOWN, False)`.
   - The task row stores the resolved ref.
   - For `owner_review`, the notification uses a fixed subject `"Telescreen: review
     TELE-<n>"`. The body is short id, rule class, severity, visibility, risk and the
     link `<public_base_url>/my-tasks` (new optional field `public_base_url: str | None =
     None`; omit the link when None). It never uses the rendered title or the locator.
   - Channel choice for owner_review member assignees: Slack when the resolved recipient
     has slack user ids, else email (one notification, not both). Team assignees keep the
     v1 behavior.
8. **`executors/detect.py`**: `RescanKind.INCONCLUSIVE` -> outcome `error` route with
   output `{result: "error", error_code: "scan_incomplete"}`. FOUND, CLEAN and ERROR are
   unchanged.
9. **`escalation/sla.py`** `SlaCheckHandler`:
   - New optional field `reader: IssueReader | None = None`.
   - Before applying a tier with `min_risk` set: when the run has an issue and
     `reader.get(...).risk_score < tier.min_risk`, write history `sla_breached` with the
     note "below risk threshold", audit `sla.breached`, and stop. No reassignment, no
     notification, no re-enqueue.
   - When the reader is None, `min_risk` is ignored (v1 behavior).

## Pitfalls

- Moving code must not change behavior. Move it verbatim, then fix imports. Run the
  existing DSL tests before adding features.
- `issue:assignee` is not a recipient ref. Never pass it to the resolver or notifications.
- `kind` default must keep v1 compiled JSON unchanged (do not emit `"kind":"approval"`).
- The owner_review notification must not include `render(step.title, ...)`, because
  titles can contain file names.

## Tests (`tests/workflow/test_v11_workflow.py`)

- every file in `examples/workflows/*.yaml` loads; `compiled_to_json` output for one of
  them equals the output before the split (compute it in the test from the loader and
  compare against a stored canonical string generated once from main, or compare
  `compiled_from_json(compiled_to_json(x)) == x`);
- owner_review with the 4 outcomes -> ok; missing `not_mine` -> issue
  `owner_review_outcomes`; `kind: foo` -> invalid_value;
- `min_severity: critical` parses;
- escalation tier `min_risk: 50` round-trips; `150` -> issue;
- human executor:
  - `issue:assignee` with a fake reader whose snapshot assignee is `member:alice` ->
    task row `assignee_ref == "member:alice"`; no assignee -> failed
    `recipient_unknown`;
  - an owner_review notification body contains `TELE-` and not the issue title (use a
    title fixture string and assert absence); exactly one channel for a member with a
    slack id;
- detect: a fake rescanner returning INCONCLUSIVE -> outcome `error` with error_code
  `scan_incomplete`;
- SLA: a tier with `min_risk=80` and issue risk 40 -> no reassignment, history note,
  no new SLA task; risk 90 -> escalates as in v1.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation`
and `<tests>` = `tests/workflow`. The whole `tests/workflow` directory must pass,
including the existing DSL, engine and executor tests. Also
`wc -l src/telescreen/workflow/dsl/*.py > /tmp/telescreen-v11-p13-workflow-wc.log; echo "exit=$?"`:
every file under 1000 lines.

## Completion Criteria

- [ ] Loader split done; every importer works; all files under 1000 lines.
- [ ] owner_review, `issue:assignee`, detect inconclusive and `min_risk` implemented as
      specified.
- [ ] Tests pass; scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
