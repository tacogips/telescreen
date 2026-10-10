# V11-P07 Placement Policies (Store, Loader, Decider) and Risk Scorer

**Status**: Ready
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
     `TaskSpec(kind=POLICY_REEVALUATE, ref_id=name, payload=json_object((("name",
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

- [ ] Loader, store, decider and scorer implemented as specified.
- [ ] All tests above pass; scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
