# V11-P05 Issues Record Path: Exposure, Policy Decisions, Status Machine, Owner Routing, Risk

**Status**: Ready
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

- [ ] Every deliverable implemented; `service.py`, `recording.py` and `exposure.py` are
      each under 1000 lines.
- [ ] The design section 5 table is covered by tests row by row.
- [ ] Scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
