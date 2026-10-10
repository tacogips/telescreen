# V11-P13 Workflow: Loader Split, owner_review Human Task, detect Inconclusive, Escalation min_risk

**Status**: Completed
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

- [x] Loader split done; every importer works; all files under 1000 lines.
- [x] owner_review, `issue:assignee`, detect inconclusive and `min_risk` implemented as
      specified.
- [x] Tests pass; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-10 17:21
**Tasks Completed**: Moved compiled workflow JSON serialization to `dsl/compiled.py` and re-exported the public helpers from `loader.py`; added owner-review DSL kind/outcome validation and issue-assignee resolution; added privacy-safe owner-review delivery, inconclusive detect mapping, and SLA `min_risk`; added focused v1.1 workflow regression tests.
**Tasks In Progress**: Required `tests/workflow` aggregate gate is blocked by the pre-existing 10,000-shard map-reduce timing assertion; p13-owned focused tests and scoped static checks pass.
**Blockers**: `tests/workflow/mapreduce/test_mapreduce.py::test_dispatch_caps_ten_thousand_shards_at_thirty_two` fails its 60-second guard on the current combined tree (63.57 seconds isolated; 68.00 seconds in the final full workflow run). This test is outside p13 `writePaths` and was not modified. A passing full workflow test gate is required by this plan.
**Reconciliation requests**: Serial integration/owner: investigate and resolve or explicitly disposition the 60-second timing guard in `tests/workflow/mapreduce/test_mapreduce.py::test_dispatch_caps_ten_thousand_shards_at_thirty_two`; the current p13 writePaths permit only the new `tests/workflow/test_v11_workflow.py`, so p13 did not edit the existing map-reduce test.
**Hashes**: `loader.py` before=8281855dc04540bc289a9fce702351e719cc507b7252807af04201a980485ec6 after=d3bd9483dd51eb292192b5fab1ac566d974c4791fa5014f8082ed1cd6037aa2f; `model.py` before=8b8cc24c438d9cbc21bd166f90b714505427bf5cd1ede34d183c9a80255b36dd after=8f0e0b6df830e4602a938c4bd120338d07dab42ddabaee2f2a40e8123e343a1c; `escalation.py` before=ade07ad54e5aa5c23e049f12395fb481b7dae1a3c3af29ef2b5a503ab264f3ee after=f8dcaf27d448a4e25489f91f05d49a3b4fd435a4008f78a27b4750cf3a203863; `validate.py` before=0c67b4b676965c18220fb68ae9311013f9c1e131dfdb69b1480f17fe6c0ac9ce after=de104000697ca64f7b64f48f4c30621ef61dad5097491789b582e24b84d455bc; `workflow-v1.schema.json` before=106d23c20162c0f164c794aa4c6c4abc9963cdc6a99dd00aba65a5188685c0b5 after=96de3c399457c66a42b9fbe19bb73e5ef066bd15417cf1cba5140820d6e23446; `human.py` before=653bff3f00d0df56beb315e1b65b4698521cfc9cf10b5c246b93c442cc285c7f after=d95472ca9287591237b5c708357cec1aa660442af483e1dcca41a066a4e6be77; `detect.py` before=2f752fec02777f10908e3d5f4a36571819a30652d83041384736b64c66014c9d after=2c11bf107b96a3105588fb94766cbc3930196fed97f44cb9ba93d81949ad27e0; `sla.py` before=0bc917f1c2294d969d5c83d282b31ec33e41fadcc3a163c4f3d8f118b44edb0c after=e2b9f991414a02b106797341c1626fafd46e94a8ac6c12b7e5011599cad4d732; `compiled.py` before=absent after=a36d74fb0a5f1271dbfa292b382dd4d8dd1bf64305b28458d9dc8589bc296622; `test_v11_workflow.py` before=absent after=ce7be5d4215cd2b44f4f4c77d6ec0131572a7bbc2270155d9fd437c109cfcaef.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-format-final2.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-ruff-final2.log`).
- `uv run --frozen ty check src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-ty-final2.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/workflow/test_v11_workflow.py` -> exit 0, 13 passed (log: `/tmp/telescreen-v11-p13-workflow-focused-pytest-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/workflow` -> exit 1, 102 passed, 1 failed, 1 skipped; failure is the out-of-scope map-reduce timing guard (log: `/tmp/telescreen-v11-p13-workflow-pytest-final.log`).
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p13-workflow-typelint-final2.log 2>&1; ! grep -E '^(src/telescreen/workflow/(dsl|executors|escalation)/|tests/workflow/)' /tmp/telescreen-v11-p13-workflow-typelint-final2.log` -> exit 0; p13 grep is empty. The global linter log has diagnostics in sibling-owned collector and issues tests.
- `wc -l src/telescreen/workflow/dsl/*.py > /tmp/telescreen-v11-p13-workflow-wc-final2.log; ! awk '$2 != "total" && $1 >= 1000' /tmp/telescreen-v11-p13-workflow-wc-final2.log | grep -q .` -> exit 0; all DSL files are under 1000 lines.
- `git grep -n -E 'compiled_to_json|compiled_from_json|json_float_value'` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-importers-final.log`); existing loader import paths remain present.
**Notes**: The first focused p13 test run exposed two fixture issues (canonical route ordering and YAML's special `on` key); they were corrected, and the final focused test run passes. Full-workflow failures are unchanged and outside p13-owned test paths. `tests/workflow` must pass after serial reconciliation before this plan can be marked Completed.

**Owner resolution (2026-10-10)**: The only failing gate was the throughput guard of the unchanged 10,000-shard test, which exceeds 60 s under heavy machine load. The owner relaxed that guard to 240 s in `tests/workflow/mapreduce/test_mapreduce.py`. Re-run `tests/workflow`; it is not a p13 blocker. Report per conventions 7a.

### Session: 2026-10-10 18:05
**Tasks Completed**: Re-ran the required workflow aggregate after the owner increased the 10,000-shard throughput guard to 240 s. The complete `tests/workflow` suite now passes; the loader split/import compatibility, owner-review behavior, inconclusive detect mapping, `min_risk`, scoped quality gates, typelint filter, and DSL file-size checks all pass on the current combined tree.
**Tasks In Progress**: None for p13. Formal independent reviews and later workflow finalization remain downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `impl-plans/active/v11-p13-workflow.md` before=2cb5db0de7241f02391d07a5feb4e8b03d3f611f7ae410413251cbc14f683c62; final after-hash is recorded in `tmp/telescreen-v11-20261010-c1/v11-p13-workflow/step6-rerun-20261010/plan-after.sha256`; p13 source hashes unchanged from the prior verified session.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0, 33 files already formatted (log: `/tmp/telescreen-v11-p13-workflow-format-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-ruff-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/workflow/dsl src/telescreen/workflow/executors src/telescreen/workflow/escalation tests/workflow` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-ty-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/workflow` -> exit 0, 103 passed, 1 skipped (offline PostgreSQL test), 0 failed (log: `/tmp/telescreen-v11-p13-workflow-pytest-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p13-workflow-typelint-rerun.log 2>&1; linter=$?; grep -E '(^|/)(src/telescreen/workflow/(dsl|executors|escalation)/|tests/workflow/)' /tmp/telescreen-v11-p13-workflow-typelint-rerun.log > /tmp/telescreen-v11-p13-workflow-typelint-scoped-rerun.log; filter=$?; if [ -s /tmp/telescreen-v11-p13-workflow-typelint-scoped-rerun.log ]; then cat /tmp/telescreen-v11-p13-workflow-typelint-scoped-rerun.log; printf 'typelint_exit=%s scoped_filter_exit=%s owned_diagnostics=present exit=1\n' "$linter" "$filter"; exit 1; else printf 'typelint_exit=%s scoped_filter_exit=%s owned_diagnostics=0 exit=0\n' "$linter" "$filter"; fi` -> exit 0; whole typelint exit 0 and 0 p13 diagnostics (logs: `/tmp/telescreen-v11-p13-workflow-typelint-rerun.log`, `/tmp/telescreen-v11-p13-workflow-typelint-scoped-rerun.log`).
- `set -o pipefail; wc -l src/telescreen/workflow/dsl/*.py > /tmp/telescreen-v11-p13-workflow-wc-rerun.log; awk '$2 != "total" && $1 >= 1000 { print; bad=1 } END { if (bad) exit 1; print "all DSL Python files under 1000 lines" }' /tmp/telescreen-v11-p13-workflow-wc-rerun.log` -> exit 0 (log: `/tmp/telescreen-v11-p13-workflow-wc-rerun.log`).
- `set -o pipefail; git grep -n -E 'compiled_to_json|compiled_from_json|json_float_value' > /tmp/telescreen-v11-p13-workflow-importers-rerun.log; code=$?; cat /tmp/telescreen-v11-p13-workflow-importers-rerun.log; printf 'exit=%s\n' "$code"` -> exit 0; all importers remain available through `loader.py` (log: `/tmp/telescreen-v11-p13-workflow-importers-rerun.log`).
**Notes**: The prior aggregate failure remains in the historical 17:21 entry and was resolved by the owner-owned threshold adjustment. `tests/workflow/mapreduce/test_mapreduce.py` is outside p13 `writePaths`; this session did not edit it. All p13 completion criteria are now checked.
