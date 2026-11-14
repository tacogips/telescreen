# V11-P05 Issues Record Path: Exposure, Policy Decisions, Status Machine, Owner Routing, Risk

**Status**: Completed (integration-accepted at 809b02d; archived 2026-10-11)
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 3, 4 (record-time evaluation and re-evaluation), 5 (record path, auto-resolution, user transitions), 7 (assignment), 9 (distinct fingerprints), 10 (when computed), 11 (`until_regressed` and closing requests), 12 (policy maintenance)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p05-issues-record`
- planPath: `impl-plans/active/v11-p05-issues-record.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`
- writePaths:
  - `impl-plans/active/v11-p05-issues-record.md`
  - `src/telescreen/issues/service.py`
  - `src/telescreen/issues/recording.py` (new)
  - `src/telescreen/issues/exposure.py` (new)
  - `src/telescreen/issues/routing.py` (new)
  - `src/telescreen/issues/policy_apply.py` (new)
  - `src/telescreen/issues/scan.py`
  - `src/telescreen/issues/transitions.py`
  - `src/telescreen/issues/repository.py`
  - `tests/issues/test_issues.py`
  - `tests/issues/test_issues_api.py`
  - `tests/issues/v11_record` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan implements the issue side of F1, F2, F3 (record path), F5 (routing), F8 (when
risk is stored) and the `until_regressed` part of F9. Sibling wave-2 plans work in the
same package, but in different files:

- v11-p06 owns `issues/{lifecycle,exceptions,occurrences,verification,digest,maintenance}.py`;
- v11-p07 owns `issues/policy/**` and `issues/risk.py`.

Do not import from their modules. Use only the ports `PolicyDecider`, `RiskScorer` and
`OwnerDirectory` (`telescreen/ports/policy.py`, `ports/directory.py`), which v11-p01 pins.

Current code to read first:

- `issues/service.py:IssueService.record_scan` (547-line file);
- `issues/scan.py:ResourceScannerImpl._resource_scan`;
- `issues/repository.py` (open-status queries);
- `issues/transitions.py:USER_TRANSITIONS`.

## Non-goals

- No API router changes (v11-p17). No remediation, resolve, exception or occurrence
  endpoints or operations (v11-p06). No policy storage or parsing (v11-p07).
- No `ingest.py` change: `record_external` lives in `service.py`.
- Do not touch `wiring.py` (v11-p19).

## Invariants

- `IssueService` keeps every public method and its signature. New dataclass fields are
  appended with defaults:
  - `decider: PolicyDecider | None = None` (None -> always violate);
  - `scorer: RiskScorer | None = None` (None -> risk 0 and asset tier None);
  - `owners: OwnerDirectory | None = None`;
  - `owner_routing: bool = False`;
  - `default_team: str | None = None`.
- With all defaults (as `wiring.py` builds it today), v1 behavior is unchanged, except
  that resolutions are now `verified_removed` / `resource_deleted` instead of `auto`, and
  the PATCH table follows design section 5.
- Resulting files stay under 1000 lines each. Move the per-class record logic out of
  `service.py` into `recording.py`, and the exposure and severity helpers into
  `exposure.py`.
- No raw value, file name or email enters any new column: exposure is enums and counts,
  placement and owner are keyed refs (already keyed by `scan.py`).

## Deliverables

1. **`scan.py` (`ResourceScannerImpl._resource_scan`)** builds the new `ResourceScan`
   fields:
   - `exposure=content.exposure`;
   - `owner_ref=self.fingerprinter.owner_ref(content.owner)` when present;
   - `placement_keys=tuple(self.fingerprinter.placement_key(p.kind, p.value) for p in
     content.placement)`;
   - `modified_at=content.modified_at`;
   - `removed=content.removed`.

   `complete` is false whenever `content.coverage` is set and its state is not `scanned`.
   Oversized content stays the existing `skipped` path.
2. **`exposure.py`**:
   - `merge_visibility(stored, new)`: unknown never overwrites known;
   - `severity_floor(rule_class, visibility) -> Severity | None`, per design section 3;
   - `effective_severity(base, floor)` using `severity_rank`;
   - `exposure_json(Exposure) -> str` via `telescreen.core.json.dump_json` (canonical,
     enums and counts only).
3. **`recording.py`**: the per-(resource, rule_class) logic, following the design
   section 5 record-path table exactly, including:
   - the `policy_changed` vs `exposure_reduced` rule (compare the stored `policy_name` and
     `policy_version` with the decision);
   - `pending_verification` + finding -> `unresolved` with history `verification_failed`;
   - `allowed` -> violate -> `unresolved` with history `policy_reevaluated`, an
     `ISSUE_CREATED` event and routing;
   - new allowed issues emit no event;
   - `until_regressed`: an `ignored` issue with an `active` `IssueExceptionRow` of
     duration `until_regressed` receives a fingerprint that is new within the same key
     version (only when the issue already has findings of that version) -> exception
     `expired` (`decided_by="system"`), issue `regressed`, `regression_count += 1`,
     history `exception_expired` plus `regressed`, and an `ISSUE_REGRESSED` event.

   Subject for the decider: `PolicySubject(source, rule_class, placement_keys,
   merged_visibility, age_reference)`, where:
   - `placement_keys` = the scan keys, else the stored keys;
   - `age_reference` = scan `modified_at`, else stored `resource_modified_at`, else
     `first_seen` (or `observed_at` for a new issue).

   Store on the issue: `base_severity`, `severity` (effective), `exposure_visibility`,
   `exposure_json`, `exposure_changed_at`, `placement_keys_json` (dump_json list),
   `resource_modified_at`, `owner_ref`, `policy_name`, `policy_version`, `asset_tier`
   (highest `scorer.asset_tier(rule_id, rule_class)` seen; rank by tier order
   `cloud_root > deploy_token > personal_token > secret > pii_high > pii`) and
   `risk_score` (`scorer.score`).

   `distinct_fingerprints` counts distinct fingerprints of the highest
   `fingerprint_key_version` present on the issue (update
   `repository.distinct_fingerprints`). `FindingRow` gets `fingerprint_key_version` from
   the `MaskedFinding`.
4. **Resource-level updates in `IssueService.record_scan`**, after the per-class loop:
   - Exposure change for all issues of the resource: when the merged visibility differs
     from the stored one, each issue in `RECONCILED_STATUSES` gets history
     `exposure_changed` (from and to visibility values only), and its severity, decision
     and risk are recomputed. Decision flips use the same table.
   - Complete scan auto-resolution: affects `RECONCILED_STATUSES` issues of absent
     classes. Resolution `resource_deleted` when `scan.removed`, else
     `verified_removed`. History `auto_resolved`.
   - `resolve_unchecked` (sweep reconciliation) uses `RECONCILED_STATUSES` and
     `resource_deleted`.
   - Whenever an issue leaves `OPEN_STATUSES` here (resolved or allowed), set its
     `requested` exception rows to `rejected` with `decided_by="system"`. This is a
     direct `IssueExceptionRow` update in this plan's repository.
   - `RecordOutcome.issue_ids` includes every issue whose status, severity, exposure or
     risk changed, so `scan.py` re-indexes them.
5. **`routing.py`** `route(session, issue, decision, now)`:
   - Runs only when both assignee ids are null.
   - Order:
     1. When `decision.route_to_owner` (or `owner_routing` for the no-policy default) and
        `owners.member_for_owner(owner_ref)` returns a ref, assign it.
     2. Else assign `team:<decision.owner_team>`.
     3. Else assign `team:<default_team>`.
     4. Else leave it unassigned.
   - Assign through the existing `self.resolver.assignee_of`.
   - History `assigned` with actor `system:owner-routing` and an audit entry.
   - Unknown recipient errors are swallowed and the issue stays unassigned. Log the code
     only.
6. **`transitions.py`**: replace `USER_TRANSITIONS` with the design section 5 table.
   PATCH to `resolved` or `ignored` -> `INVALID_TRANSITION`. `change_status`:
   - leaving `ignored` -> `unresolved` revokes the active exception (row status
     `revoked`, history `exception_revoked`);
   - `pending_verification` -> `unresolved` is allowed;
   - `resolution` and `resolved_at` are cleared when leaving `resolved`.
7. **`policy_apply.py`**:
   - `PolicyReevaluateHandler` (TaskHandler, `kind = EngineTaskKind.POLICY_REEVALUATE`;
     imitate `collectors/tasks.py:CollectorScanHandler` for the TaskHandler shape).
     Payload: none needed. It re-evaluates every `RECONCILED_STATUSES` issue in batches
     of 200, each batch in its own `sessions.begin()`. It applies only open -> `allowed`
     (`policy_changed`, history `policy_reevaluated`) and `allowed` -> `unresolved`
     (history, `ISSUE_CREATED` event, routing). It never writes `verified_removed`.
     After commit it calls `indexer.index_issue` per changed issue.
   - `IssuePolicyMaintenance` (ScheduleProvider) `tick(session, now) -> int`: at most once
     per hour per process (in-memory `_last_run`). It:
     - recomputes `risk_score` for `OPEN_STATUSES` issues (age factor);
     - re-evaluates `allowed` issues (the `max_age_days` path).

     It returns the count changed. Search documents refresh on the next indexing. The API
     sorts by the DB column.
8. **`IssueService`**:
   - `get()` fills `IssueSnapshot.assignee_ref` via `owners.member_ref` / `owners.team_ref`
     when `owners` is set, plus `risk_score` and `visibility`.
   - New `open_issues_for_resources` implements `ResourceIssueReader`
     (`OPEN_STATUSES`, ordered by number).
   - `open_resource_refs` uses `RECONCILED_STATUSES`.
   - `record_external` maps `ExternalScan.exposures` per locator onto each per-locator
     `ResourceScan.exposure` (locator not listed -> None).
   - `record_external` builds each `MaskedFinding` (positional construction at about
     `service.py:243`) with `fingerprint_key_version=self.fingerprinter.key_version`
     (keyword argument). Without it, external fingerprints re-keyed with the active
     version 2 key would be stored as version 1 and link wrongly (design section 9).

## Pitfalls

- Order matters: compute the merged visibility before the per-class decisions, so a scan
  that both changes exposure and has findings decides once with the new exposure.
- `unknown` exposure must not trigger `exposure_changed`, and must not lower a known
  visibility.
- Do not emit `ISSUE_CREATED` for issues born `allowed`, or for `allowed` issues that get
  more findings.
- An issue that has `ignored` without an exception row (v1 legacy) only bumps counters.
- Never resolve on `complete=False`. Removal only with `scan.removed` and
  `complete=True`.
- Keep history notes free of locators. Exposure history holds visibility enum values only.

## Tests

Update the existing tests:

- In `tests/issues/test_issues.py`, change expectations to `verified_removed` (lines
  near 297-298 assert `"auto"`).
- The test that PATCHes to `IGNORED` (near 334) must now assert `INVALID_TRANSITION`.
- Fix `tests/issues/test_issues_api.py` if any PATCH to `resolved`/`ignored` exists.

New tests under `tests/issues/v11_record/` (fakes for `PolicyDecider`, `RiskScorer`,
`OwnerDirectory`; SQLite via `tests.support.db.make_sqlite_harness`):

- public exposure + credential -> severity `critical`, base `high`;
- the same resource later `internal` -> severity `high` and history `exposure_changed`;
- unknown never overwrites public;
- fake decider allow -> new issue `allowed`, no event, no assignee;
- an open issue then allowed with the same policy -> `exposure_reduced`; with a new
  policy version -> `policy_changed`;
- `allowed` then violate -> `unresolved` plus an `ISSUE_CREATED` event plus routing to
  the mapped owner member;
- routing fallbacks in order: owner -> owner_team -> default team -> none; an existing
  assignee is never overridden;
- `pending_verification` + finding -> `unresolved` with history `verification_failed`;
  `pending_verification` + complete clean scan -> `resolved` / `verified_removed`;
- `removed=True` complete scan -> `resource_deleted`; `resolve_unchecked` ->
  `resource_deleted`; an incomplete scan never resolves;
- `until_regressed`: a new fingerprint expires the exception and sets `regressed`; a
  fingerprint of a NEW key version alone does not;
- distinct fingerprints count only the highest key version;
- `record_external` with a Fingerprinter whose active `key_version` is 2 -> the stored
  `FindingRow.fingerprint_key_version` is 2; `ExternalScan.exposures` for one locator ->
  that issue's `exposure_visibility` is set and other locators stay `unknown`;
- `requested` exception rows are rejected when the issue auto-resolves;
- `PolicyReevaluateHandler` flips both ways and never writes `verified_removed`;
- `IssuePolicyMaintenance` skips a second tick within an hour and refreshes risk;
- `scan.py`: placement and owner are keyed (no raw value in `ResourceScan`; check
  `repr` and fields), and `removed` and coverage propagate.

## Verification

Conventions section 7 with the scoped paths limited to the files this plan owns.
Sibling wave-2 plans v11-p06 and v11-p07 edit other files in the same package at the same
time, so never use `src/telescreen/issues` or `tests/issues` as a required scope:

- `<src>` = `src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py`
- `<tests>` = `tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record`
- typelint filter (must print nothing):
  `grep -E '^(src/telescreen/issues/(service|recording|exposure|routing|policy_apply|scan|transitions|repository)\.py|tests/issues/(test_issues\.py|test_issues_api\.py|v11_record/))' /tmp/telescreen-v11-p05-issues-record-typelint.log`

These scoped ruff format, ruff check, ty, pytest and typelint checks must exit 0 (grep:
no output). Also run, as an informational wide run only (not a required gate and not a
step `verification` entry):
`uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/e2e tests/workflow > /tmp/telescreen-v11-p05-issues-record-pytest-wide.log 2>&1; echo "exit=$?"`.
Failures there in files owned by other active wave-2 plans are informational. Record them
in the Progress Log with the failing test ids. A wide-run failure in a test file this plan
owns blocks completion.

## Completion Criteria

- [x] Every deliverable implemented; `service.py`, `recording.py` and `exposure.py` are
      each under 1000 lines.
- [x] The design section 5 table is covered by tests row by row.
- [x] Scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)

### Session 2026-10-10 17:21 JST — Step 6 implementation and author self-check

- Implemented the exposure-aware issue record path, status transitions, keyed placement
  and owner routing, policy reevaluation and maintenance, risk scoring, scan coverage,
  exception expiry, and external fingerprint-version persistence. Added focused tests
  under `tests/issues/v11_record/`; updated legacy resolution and PATCH assertions.
- Self-check corrections: `resolved` plus policy `allow` now becomes `allowed` without a
  new issue event; `ignored` -> `unresolved` revokes any active exception, including a
  time-limited exception. Regression tests cover both behaviors.
- Scoped gates (all exit 0):
  - `uv run --frozen ruff format --check --no-cache src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — 11 files formatted.
  - `uv run --frozen ruff check --no-cache src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — all checks passed.
  - `uv run --frozen ty check src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — all checks passed.
  - `uv run --frozen pytest -p no:cacheprovider tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — exit 0, 26 passed; complete log `/tmp/telescreen-v11-p05-issues-record-pytest-final.log`.
  - `uv run --frozen python tools/typelint.py` plus the plan filter `grep -E '^(src/telescreen/issues/(service|recording|exposure|routing|policy_apply|scan|transitions|repository)\.py|tests/issues/(test_issues\.py|test_issues_api\.py|v11_record/))' /tmp/telescreen-v11-p05-issues-record-typelint-final.log` — exit 0, no matching diagnostics; complete log `/tmp/telescreen-v11-p05-issues-record-typelint-final.log`.
- Informational wide run: `uv run --frozen pytest -p no:cacheprovider tests/issues tests/collectors tests/e2e tests/workflow` — exit 2 during collection because sibling-owned `tests/collectors/core/test_v11_coverage_health.py` imports missing `EventCheckKind` from `telescreen.core.vocabulary`; complete log `/tmp/telescreen-v11-p05-issues-record-pytest-wide.log`. The assigned scoped test suite passed.
- Every Python file in the assigned source/test paths is below 1000 lines; largest is
  `src/telescreen/issues/service.py` at 971 lines. Plan file remains under 400 lines.
- Current source hashes: `service.py` f6134e3d3a537109092b865d625c3a63e31cd7423dd90af93da00e474a48df35;
  `recording.py` c29ffb39407710b68b63bdd09a49ba7be45b7d891e07c3c8be08546d69133f5a;
  `exposure.py` 54f44349200562491dfbe33e6b90cd2ee5313a4d44430fbbbf1616314d0e7e97;
  `routing.py` f578fb2b25fe89863d7ca8eb52a1b71abe1d0c2441d0a5221609bdc7535bc98c;
  `policy_apply.py` b66db365d7013ec1798a3da7e7f5c97393ecd4768bad6d650ef659d30f5e37b3;
  `scan.py` c8485c24c4a8bb740ae032f683eff606de1caf618aa9523a10d972c921d99400;
  `transitions.py` 6420ed0679e21780c0b8afc77a28b409219a7352396a645cd22ed717b49425c7;
  `repository.py` e25389d9477bfb80e2d14783278841daa542b153b069c805e8ea4474e8bb7f31;
  `test_issues.py` a84523516750cb4092b0b57a252cafbfd4ba2051dce54c36f57f99058505db46;
  `test_issues_api.py` b9019c02389e179c63bc39467b243655b1d26c8f13d239481ffc3a68941c0ef1;
  `v11_record/test_recording.py` c7823a336bd33691e9cdcecdefaef29297488cbd5fad8d6add29284964b24cc4.

### Session 2026-10-10 17:45 JST — test-integrity repair (P05-TI-001 / P05-TI-002)

- Added `tests/issues/v11_record/test_v11_record_integrity.py` covering allowed-to-violate
  notification and owner routing, owner/team/default/no-recipient routing order, existing
  assignee preservation, resolver-error fallback, policy-version changes, keyed scan
  metadata and raw-canary absence, highest-version fingerprint counts, rotation-safe
  exception expiry, requested-exception rejection, hourly maintenance, and risk/tier
  persistence. The existing policy-reevaluation test now asserts the event and absence
  of remediation resolution; `test_resolve_unchecked_only_resolves_old_sweep_covered_issues`
  asserts `resource_deleted`.
- The first expanded test run found two test setup errors (40 passed, 2 failed): missing
  event enum import and unset keyed owner ref in the direct router case. Both were fixed.
  Complete initial log: `/tmp/telescreen-v11-p05-issues-record-pytest-revision-1.log`.
- Final scoped gates all exit 0. Pytest: 42 passed, 0 failed; complete log
  `/tmp/telescreen-v11-p05-issues-record-pytest-final-review.log`. Ruff format check:
  12 files already formatted. Ruff check and ty report all checks passed. Typelint grep
  is empty; complete log `/tmp/telescreen-v11-p05-issues-record-typelint-final-review.log`.
- Current hashes: `service.py` f6134e3d3a537109092b865d625c3a63e31cd7423dd90af93da00e474a48df35;
  `repository.py` e25389d9477bfb80e2d14783278841daa542b153b069c805e8ea4474e8bb7f31;
  `test_issues.py` c7856c5daa442f8fab6031829a0ea56ac08261c280a62f039eaf37e18b2e53ce;
  `test_recording.py` 97365818425e8cf408be1974f39fb812ed68696a3843aed72574e55c259d3b46;
  `test_v11_record_integrity.py` 8a45fc7ff86a3e5b474c4994b9923a963b445937ecfd8cdcf29f8721c1e7da08.
- Completion criteria remain checked based on these current-source gates and coverage.
  The p05 plan remains under 400 lines; assigned Python files remain below 1000 lines.

### Session 2026-10-10 17:29 JST — final manifest-exact rerun

- Re-ran all six manifest gates on the final source. Each exited 0: scoped ruff format
  (12 files already formatted), scoped ruff check, scoped ty check, scoped pytest
  (42 passed, 0 failed; complete canonical log `/tmp/telescreen-v11-p05-issues-record-pytest.log`),
  typelint with the exact plan filter (no matching diagnostics; canonical log
  `/tmp/telescreen-v11-p05-issues-record-typelint.log`), and the completion checkbox gate
  (zero unchecked criteria). Earlier canonical logs were preserved as `*-pre-review.log`.
- Current p05-owned hashes: `service.py` f6134e3d3a537109092b865d625c3a63e31cd7423dd90af93da00e474a48df35;
  `recording.py` c29ffb39407710b68b63bdd09a49ba7be45b7d891e07c3c8be08546d69133f5a;
  `exposure.py` 54f44349200562491dfbe33e6b90cd2ee5313a4d44430fbbbf1616314d0e7e97;
  `routing.py` f578fb2b25fe89863d7ca8eb52a1b71abe1d0c2441d0a5221609bdc7535bc98c;
  `policy_apply.py` b66db365d7013ec1798a3da7e7f5c97393ecd4768bad6d650ef659d30f5e37b3;
  `scan.py` c8485c24c4a8bb740ae032f683eff606de1caf618aa9523a10d972c921d99400;
  `transitions.py` 6420ed0679e21780c0b8afc77a28b409219a7352396a645cd22ed717b49425c7;
  `repository.py` e25389d9477bfb80e2d14783278841daa542b153b069c805e8ea4474e8bb7f31;
  `test_issues.py` c7856c5daa442f8fab6031829a0ea56ac08261c280a62f039eaf37e18b2e53ce;
  `test_issues_api.py` b9019c02389e179c63bc39467b243655b1d26c8f13d239481ffc3a68941c0ef1;
  `test_recording.py` 97365818425e8cf408be1974f39fb812ed68696a3843aed72574e55c259d3b46;
  `test_v11_record_integrity.py` 8a45fc7ff86a3e5b474c4994b9923a963b445937ecfd8cdcf29f8721c1e7da08.

### Session 2026-10-10 — adversarial repair (P05-ADV-001)

- Added `reopen_allowed_issue` in `src/telescreen/issues/policy_apply.py`, shared by
  `PolicyReevaluateHandler` and `IssuePolicyMaintenance.tick`. The maintenance violation
  now writes `policy_reevaluated`, emits one `ISSUE_CREATED`, routes through the owner
  router, updates policy name/version and timestamp, and recomputes risk after reopening.
  It writes no remediation resolution; same-hour throttling and changed-id de-duplication
  remain intact.
- Added `test_issue_policy_maintenance_reopens_allowed_issue_with_full_transition` in
  `tests/issues/v11_record/test_v11_record_integrity.py`. It asserts exact history values
  and actors, one event, team assignment, policy metadata, updated time, risk refresh, no
  remediation reason, and no duplicate event on the throttled tick. Existing handler and
  throttle tests remain unchanged and pass.
- Strict typing inspection found that this environment's typelint diagnostics use
  absolute paths while the manifest grep is anchored to relative paths. Added missing
  local annotations in the p05-owned policy/test files. The literal manifest typelint
  command exits 0; a corrected absolute-path scan also finds zero diagnostics in p05
  paths. The full typelint log contains unrelated diagnostics outside this plan.
- An initial format check returned 1 for line wrapping in the added tests; only the
  changed test files were formatted, and the final manifest format check exits 0.
- Final manifest gates on the current source all exit 0:
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — 12 files already formatted.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — all checks passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/issues/service.py src/telescreen/issues/recording.py src/telescreen/issues/exposure.py src/telescreen/issues/routing.py src/telescreen/issues/policy_apply.py src/telescreen/issues/scan.py src/telescreen/issues/transitions.py src/telescreen/issues/repository.py tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record` — all checks passed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues/test_issues.py tests/issues/test_issues_api.py tests/issues/v11_record 2>&1 | tee /tmp/telescreen-v11-p05-issues-record-pytest.log` — exit 0, 43 passed, 0 failed.
  - `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p05-issues-record-typelint.log 2>&1; ! grep -E '^(src/telescreen/issues/(service|recording|exposure|routing|policy_apply|scan|transitions|repository)\.py|tests/issues/(test_issues\.py|test_issues_api\.py|v11_record/))' /tmp/telescreen-v11-p05-issues-record-typelint.log` — exit 0; corrected absolute-path filter found zero p05 diagnostics.
  - `test "$(grep -c '^- \[ \]' impl-plans/active/v11-p05-issues-record.md)" -eq 0` — exit 0 after this entry was appended.
- Focused regression run: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/issues/v11_record/test_v11_record_integrity.py::test_issue_policy_maintenance_reopens_allowed_issue_with_full_transition 2>&1 | tee /tmp/telescreen-v11-p05-issues-record-adv001-focused.log` — exit 0, 1 passed.
- Line counts: `policy_apply.py` 196, `service.py` 971, `test_recording.py` 478,
  `test_v11_record_integrity.py` 689, plan 377 after this entry; all remain below limits.
- Updated hashes: `policy_apply.py` b94031190b6f120fb20794fe3bef974d05adbd85947de0cf7e9082bd99005a81;
  `test_recording.py` 7881fd61b5bfcd0163a081bec6168691ec8d6356f3398622a04df5edbba12d35;
  `test_v11_record_integrity.py` 8fa387af78285a3d3da7f0c15e09a1c448535ab8112879128507cd78fee2abc0.
