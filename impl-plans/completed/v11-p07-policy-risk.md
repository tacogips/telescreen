# V11-P07 Placement Policies (Store, Loader, Decider) and Risk Scorer

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 4 (F2/F10) and 10 (F8); design-docs/specs/design-data-model.md (`placement_policies`)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p07-policy-risk`
- planPath: `impl-plans/active/v11-p07-policy-risk.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`
- writePaths:
  - `impl-plans/active/v11-p07-policy-risk.md`
  - `src/telescreen/issues/policy` (new package)
  - `src/telescreen/issues/risk.py` (new)
  - `tests/issues/v11_policy` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan implements the `PolicyDecider` and `RiskScorer` ports (`telescreen/ports/policy.py`,
pinned by v11-p01) plus the policy version store that the policies router (v11-p17) calls.
The record path (v11-p05) and the remediation services (v11-p06) consume these only
through the ports.

Imitate:

- `workflow/dsl/loader.py:load_workflow` and `_Reader` (JsonValue reading with path-only
  validation issues);
- `workflow/engine/definitions.py` (versioned definitions with an active flag; read it);
- `core/json.py` `parse_json` / `parse_yaml` / `dump_json`.

## Non-goals

- No router, no wiring. No re-evaluation of issues: the store only enqueues the
  `POLICY_REEVALUATE` task, and v11-p05's handler applies it.
- No glob forms other than `<owner>/*`, no folder ancestry, no path selectors.

## Invariants

- Policy documents are operator configuration. `source_text` is rejected (422
  `RAW_VALUE_DETECTED`) if `RuleDetector` finds anything; imitate the workflow upload
  check (search for `raw_value_detected` in `src/telescreen/workflow/engine/definitions.py`
  or the workflows router to see the pattern). `rationale` is stored redacted inside
  `compiled_json`.
- Validation errors carry JSON paths and codes only, never values.
- The decider is pure apart from reading `placement_policies`. It never writes.

## Deliverables

1. **`issues/policy/model.py`**, frozen dataclasses:
   - `PolicyLocations(gdrive_drives, gdrive_folders, slack_channels, github_repos:
     tuple[str, ...])`;
   - `PolicyDefinition(name, effect: PolicyEffect, rule_classes: tuple[RuleClass, ...],
     sources: tuple[CollectorKind, ...], locations: PolicyLocations, max_exposure:
     Visibility | None, max_age_days: int | None, route_to_owner: bool, owner_team: str |
     None, rationale: str)`;
   - `PolicyVersionView(name, version, effect, enabled, active, owner_team, source_format,
     source_text, created_by, created_at)`.
2. **`issues/policy/loader.py`**:
   - `load_policy(text, source_format) -> PolicyDefinition` raising
     `PolicyValidationError(issues)`, imitating `WorkflowValidationError`.
   - Rules from design section 4:
     - name regex;
     - unknown keys rejected at every level;
     - `condition` only with effect `allow`;
     - `max_exposure` must not be `unknown`;
     - `max_age_days` from 1 to 36500;
     - `github_repos` items must be `owner/repo` or `owner/*` (lowercased);
     - rationale at most 2000 characters;
     - `route_to_owner` defaults to true.
   - `policy_to_json` / `policy_from_json` for `compiled_json`.
3. **`issues/policy/store.py`** `PolicyStore` (dataclass: `redactor`, `detector`
   (RuleDetector), `audit`, `queue: TaskQueue`, `resolver: RecipientResolver`, `clock`):
   - `validate(text, fmt) -> tuple[ValidationIssue, ...]`.
   - `create(session, text, fmt, actor_ref) -> PolicyVersionView`: new version =
     max + 1, inactive, enabled true. `owner_team` existence is checked through
     `resolver.assignee_of(f"team:{slug}")`; a `RECIPIENT_UNKNOWN` becomes a validation
     issue at `/owner_team`. Audit `policy.created`.
   - `activate(session, name, version, actor_ref)`: this version becomes the only active
     one, and the name is enabled.
   - `disable(session, name, actor_ref)`: all versions inactive, enabled false.
   - Both audit (`policy.activated` / `policy.disabled`) and enqueue
     `TaskSpec(kind=POLICY_REEVALUATE, ref_id=policy_version_id, payload=json_object((("name",
     json_str(name)),)), available_at=now, idempotency_key=f"policy:{name}:{version}:{action}:{audit_seq}")`,
     where `action` is `activated` or `disabled`, `version` is the activated version (for
     disable: the version that was active, else `0`), and `audit_seq` is the `int` returned
     by `AuditLog.append` for the audit entry written in the same transaction (design
     section 4). Append the audit entry first, then enqueue.
   - `list(session)` (one item per name: active version or latest), `versions(session,
     name)`, `version(session, name, v)`.
   - Unknown name or version -> `NOT_FOUND`.
4. **`issues/policy/decider.py`** `PlacementPolicyDecider` (implements `PolicyDecider`;
   fields `fingerprinter: Fingerprinter`, `owner_routing_default: bool`):
   - `decide(session, subject, now)` loads the active, enabled versions (one query per
     call; no cache), parses `compiled_json`, and applies the 3-step algorithm of design
     section 4 exactly:
     1. violate wins (first by name);
     2. allow when the conditions hold;
     3. otherwise violate, with owner_team and route_to_owner from the first matching
        allow policy whose condition failed, or `owner_routing_default` when nothing
        matched.
   - Matching: empty `rule_classes` / `sources` / locations match all. A location
     selector matches when `fingerprinter.placement_key(kind, value)` is in
     `subject.placement_keys`. `owner/*` maps to `(github_owner, owner)` and `owner/repo`
     to `(github_repo, owner/repo)`; GitHub values are lowercased.
   - `max_exposure`: `visibility_rank(subject.visibility)` not None and <= the rank of
     the limit.
   - `max_age_days`: `now - subject.age_reference <= timedelta(days=n)`.
5. **`issues/risk.py`** `DefaultRiskScorer(weights: RiskWeights)` (implements
   `RiskScorer`):
   - `asset_tier(rule_id, rule_class)`: the rule id table of design section 10, then the
     class fallback.
   - `score(inputs)` = `round(100 * S * E * A * G)` clamped to 0-100. Age buckets: <1 day,
     <7 days, <30 days, otherwise. A None tier uses the `pii` weight.

## Pitfalls

- Selector values are keyed at evaluation time with the same Fingerprinter as `scan.py`
  (identity key). Never store keyed selectors in `compiled_json`, and never store raw
  placement values on issues (that is v11-p05's job).
- Unknown visibility never satisfies `max_exposure` (fail closed).
- Deterministic order: sort candidate policies by name. Never rely on DB order.
- Do not rely on `**` unpacking or dict-typed values; parse into dataclasses.

## Tests (`tests/issues/v11_policy/`)

- Loader:
  - the design YAML example parses;
  - unknown key at `/match/foo` -> issue path `/match/foo`;
  - `condition` on violate -> error;
  - `max_exposure: unknown` -> error;
  - `github_repos: ["org/a*"]` -> error;
  - JSON format equivalent.
- Store:
  - create -> versions 1 and 2 inactive;
  - activate(2) -> only 2 active, one `POLICY_REEVALUATE` task enqueued, audit entries
    present;
  - disable -> none active and a task enqueued;
  - `source_text` containing a sample secret (`tests.support.samples.github_token()`) ->
    `RAW_VALUE_DETECTED`, nothing stored;
  - unknown owner_team -> validation issue.
- Decider:
  - no policies -> violate with route_to_owner = default;
  - allow on a gdrive folder with a matching placement key -> allow;
  - the same allow with visibility public and `max_exposure: internal` -> violate with
    the allow policy's owner_team;
  - an age reference 400 days ago with `max_age_days` 365 -> violate;
  - a violate and an allow both matching -> violate;
  - `owner/*` matches a `github_owner` key;
  - a rule class filter excludes.
- Risk:
  - a public AWS key 10 days old -> 90;
  - private PII under 1 day -> `round(100*0.45*0.3*0.4*0.7)` = 4;
  - weights override applied;
  - the asset tier table and fallbacks.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/issues/policy src/telescreen/issues/risk.py` and `<tests>` =
`tests/issues/v11_policy`.

## Completion Criteria

- [x] Loader, store, decider and scorer implemented as specified.
- [x] All tests above pass; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:21 JST
**Tasks Completed**: Added frozen policy DTOs, path-only YAML/JSON validation and compiled JSON round-trip; added versioned store, owner validation, redacted rationale, audit sequencing and POLICY_REEVALUATE enqueue; implemented deterministic pure policy decisions and design-specified risk scoring; added loader, store, decider and scorer tests.
**Tasks In Progress**: None for v11-p07. Router, issue-record/remediation consumption, wiring, integration review and shared docs remain assigned to downstream workflow/plans.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/issues/policy/__init__.py` before=absent after=7ae8f1427826f753e83e49574950ff0964d602cf08784dfa88a8093c23c7fe05; `src/telescreen/issues/policy/model.py` before=absent after=0d740754604d40be69525c5b06ec78b83a0a66e748cc10afcdf4d4b20d243862; `src/telescreen/issues/policy/loader.py` before=absent after=979d405e79ca3dde732ea1216b3153d7d79d6c84fc6a4dd6fdf9ee09552e170d; `src/telescreen/issues/policy/store.py` before=absent after=610e09b404399db3b11fcd07d85c2befae2a2097ddbbb3b5fed4cb0da0a9bc86; `src/telescreen/issues/policy/decider.py` before=absent after=83b6e5b017cc535e6d189639748215260cdbc3c3fb8b8deb28b3e18a9d1f44e4; `src/telescreen/issues/risk.py` before=absent after=96d0ac3905086c1a3be58d14f80002bf4fa08d3f5394bec877c303ee2e6c7bbb; `tests/issues/v11_policy/test_loader.py` before=absent after=5e3f78e37675bf803fa928eca7681201b21e8a3004d73ac2df8150f9525656bb; `tests/issues/v11_policy/test_store.py` before=absent after=43a1d106518e9cfbb15173b7bb572ee5d31f56d9b626bf0077d94dae02f08034; `tests/issues/v11_policy/test_decider.py` before=absent after=95dd8da99c8c5223f7e0d65137d333dbb7fb2c69014a41e213159ce401409226; `tests/issues/v11_policy/test_risk.py` before=absent after=70608f544db8dfbb28d697df2e9a69b8bfbc8d05e40c1f99f757837f993a647a.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-ruff-final.log`); `uv run --frozen ty check src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/issues/v11_policy` -> exit 0, 19 passed (log: `/tmp/telescreen-v11-p07-policy-risk-pytest-final.log`); whole-repository typelint ran (log: `/tmp/telescreen-v11-p07-policy-risk-typelint-final.log`) and the p07 path filter was empty (filter output: `/tmp/telescreen-v11-p07-policy-risk-typelint-scoped-final.log`). The whole-repository invocation exits 1 due diagnostics in concurrent sibling-owned collector coverage, issue record, policy apply and workflow DSL paths; these were not edited by p07 and are tolerated fanout findings.
**Notes**: Accepted dependency readiness was confirmed for v11-p01-foundation and v11-p02-db-migration. The verification output is from the final p07 source. Independent reviews and downstream integration remain pending their workflow steps.

### Session: 2026-10-10 17:22 JST
**Tasks Completed**: Reconciled a cross-plan storage limit found during author self-check.
**Tasks In Progress**: Policy activation for all valid policy names.
**Blockers**: Valid policy names allow up to 64 characters, and the assigned contract requires `TaskSpec.ref_id=name`. The shared `EngineTaskRow.ref_id` column is `String(36)` (`src/telescreen/db/models/workflow.py`), and `SqlTaskQueue.enqueue` persists `spec.ref_id` directly (`src/telescreen/workflow/queue/sql_queue.py`). PostgreSQL therefore rejects activation of policies whose name exceeds 36 characters. SQLite scoped tests do not enforce this length.
**Reconciliation requests**: Database owner/serial integration: widen `engine_tasks.ref_id` to at least 64 characters through a new idempotent Alembic revision (do not edit existing migrations), update model/migration parity tests, and verify the shared queue accepts a 64-character policy name. Then rerun p07 store activation and database migration/queue tests. p07 cannot edit the model/migration outside its committed writePaths without a scoped plan amendment.
**Hashes**: Plan before=ab3aae693dfd6583ca94a7d377d0ace099c278303413c56afb0e55cf56ab4a15; source/test after-hashes are recorded in the 17:21 JST session above.
**Verification**: No new commands; the focused and scoped gates in the 17:21 JST session pass for names up to 36 characters. The cross-plan PostgreSQL column limit remains unresolved.
**Notes**: The 17:21 session's original `Status: Completed`, `Tasks In Progress: None`, and `Reconciliation requests: None` are superseded by this concrete storage-boundary finding. The implementation remains on the same branch and its passing evidence is retained.

**Owner resolution (2026-10-10)**: Do NOT widen `engine_tasks.ref_id`. Use the policy's id (UUID string, 36 characters, the `placement_policies.id` of the activated version) as `TaskSpec.ref_id` for POLICY_REEVALUATE, and keep the policy `name` in the payload (idempotency key may use the name). This fits the existing String(36) column on PostgreSQL. Add a test that activates a 64-character policy name and asserts the enqueued ref_id length is at most 36. This supersedes the plan text `ref_id=name`.

### Session: 2026-10-10 17:52 JST
**Tasks Completed**: Applied the owner resolution: policy reevaluation task references now use the version row UUID, while the payload and idempotency key retain the policy name. Added a 64-character policy-name regression test checking both activation and disable task references and payloads. Marked all implementation criteria complete.
**Tasks In Progress**: None for v11-p07. Formal review and serial integration remain downstream workflow work.
**Blockers**: None.
**Reconciliation requests**: None. The previous `String(36)` concern is resolved within p07 writePaths using the owner-directed version UUID reference; no database model or migration changed.
**Hashes**: `src/telescreen/issues/policy/store.py` before=610e09b404399db3b11fcd07d85c2befae2a2097ddbbb3b5fed4cb0da0a9bc86 after=b4d9b4d0f00e910e1bc557308f8297693b919dcbbec7a5ef3d8704a979e7fb49; `tests/issues/v11_policy/test_store.py` before=43a1d106518e9cfbb15173b7bb572ee5d31f56d9b626bf0077d94dae02f08034 after=73507d5b3a39cdde04305739987dd83b06c7c85bfbe754c9130200648453e799.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-format-step6-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-ruff-step6-final.log`); `uv run --frozen ty check src/telescreen/issues/policy src/telescreen/issues/risk.py tests/issues/v11_policy` -> exit 0 (log: `/tmp/telescreen-v11-p07-policy-risk-ty-step6-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/issues/v11_policy` -> exit 0, 20 passed (log: `/tmp/telescreen-v11-p07-policy-risk-pytest-step6-final.log`); whole-repository typelint exit 1 due to concurrent sibling-owned diagnostics, with 0 p07 matches (log: `/tmp/telescreen-v11-p07-policy-risk-typelint-step6-final.log`; scoped output: `/tmp/telescreen-v11-p07-policy-risk-typelint-scoped-step6-final.log`).
**Notes**: Dependencies `v11-p01-foundation` and `v11-p02-db-migration` were accepted in `acceptedPlanIds`. All verification is against the current source. Independent review and serial integration are pending their workflow steps.
