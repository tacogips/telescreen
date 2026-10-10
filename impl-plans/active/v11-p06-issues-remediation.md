# V11-P06 Issues Remediation: Verification Requests, Reasoned Resolution, Exceptions, Occurrences, Digest

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 5 (mark remediated, verify_rescan, resolve with a reason), 7 (release_to_team, weekly digest), 9 (linked occurrences), 11 (exceptions), 12 (exception maintenance)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p06-issues-remediation`
- planPath: `impl-plans/active/v11-p06-issues-remediation.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`
- writePaths:
  - `impl-plans/active/v11-p06-issues-remediation.md`
  - `src/telescreen/issues/lifecycle.py` (new)
  - `src/telescreen/issues/exceptions.py` (new)
  - `src/telescreen/issues/occurrences.py` (new)
  - `src/telescreen/issues/verification.py` (new)
  - `src/telescreen/issues/digest.py` (new)
  - `src/telescreen/issues/maintenance.py` (new)
  - `tests/issues/v11_remediation` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

These are the owner and responder operations that move an issue out of the open states
with a reason, plus the scheduled exception and digest work. They run in parallel with
v11-p05 (record path, `service.py`) and v11-p07 (policy and risk) in the same package.

Rules:

- Do NOT import `service.py`, `recording.py`, `routing.py`, `policy_apply.py`,
  `issues/policy/**` or `issues/risk.py`.
- Write `IssueRow`, `IssueHistoryRow`, `IssueExceptionRow` and `FindingRow` through
  SQLAlchemy directly, with your own small helpers.
- Use only ports for other capabilities: `AuditLog`, `TaskQueue`, `ResourceRescanner`,
  `NotificationSender`, `RecipientResolver`, `OwnerDirectory`, `PolicyDecider`.

Imitate:

- `issues/service.py:IssueService._history` and `_audit` (redaction of free text,
  count-only audit details);
- `collectors/tasks.py:CollectorScanHandler` (TaskHandler);
- `police/maintenance.py:PoliceMaintenance` (ScheduleProvider);
- `collectors/scheduler.py:_latest_fire` (cron).

## Non-goals

- No routers (v11-p17, v11-p18), no wiring (v11-p19).
- No change to the record path: the verification rescan resolves or reopens issues
  through `record_scan` (v11-p05). This plan handles only the inconclusive and error
  results.
- No provider calls of any kind; nothing writes to sources.

## Invariants

- No raw values: notes and reasons pass `Redactor.redact`. Notes are truncated to 4000
  characters, reasons to 2000.
- Notifications (verification inconclusive, exception approval request, exception
  expired, digest) contain only short ids (`TELE-<n>`), rule class, severity, visibility,
  risk and counts. Never titles, display locators, reasons or emails.
- Every state change writes history and an audit entry in the caller transaction.
- Functions that take a `session` never commit.

## Deliverables

1. **`lifecycle.py`** `IssueRemediationService` (dataclass; implements the
   `IssueRemediation` port plus `resolve_with_reason`):
   - `request_verification(session, issue_id, action, include_linked, actor_ref, note) ->
     RemediationReceipt`:
     - Allowed from `unresolved`, `acknowledged`, `in_progress`, `regressed`; otherwise
       `TelescreenError(INVALID_TRANSITION)`.
     - Sets `pending_verification`; history `verification_requested` (to_value =
       action value); audit `issue.remediation_requested`.
     - When `resource_ref_json` and `collector_config_id` are both set, it enqueues
       `TaskSpec(kind=VERIFY_RESCAN, ref_id=issue_id, payload=json_object((("issue_id",
       json_str(issue_id)),)), available_at=now, idempotency_key=f"verify:{issue_id}:{history_id}")`.
     - With `include_linked=True` and the issue's rule class in `CREDENTIAL_CLASSES`, it
       applies the same to each eligible linked issue from `occurrences.linked`. Their
       note is `"linked to TELE-<n>"`; issues not in the eligible statuses are skipped.
     - `rescans_enqueued` counts the actual enqueues.
   - `resolve_with_reason(session, issue_id, reason: IssueResolution, note, actor_ref,
     is_admin) -> None`:
     - `reason` must be `false_positive` or `manual_unverified`, else
       `VALIDATION_ERROR`; `manual_unverified` requires `is_admin`, else `FORBIDDEN`.
     - An empty or blank note -> `VALIDATION_ERROR`.
     - Allowed from `OPEN_STATUSES`.
     - Sets `resolved` with `resolution` and `resolved_at`; history `status_changed`
       with the note; audit `issue.resolved`; rejects `requested` exceptions
       (`decided_by="system"`).
   - `release_to_team(session, issue_id, actor_ref)`:
     - Clears `assignee_member_id`.
     - Team = `decider.decide(...)` `owner_team` (subject from the stored issue columns),
       else `default_team`.
     - Assigns via `RecipientResolver.assignee_of(f"team:{slug}")`; history `assigned`
       to_value `team:<slug>`. With no team, the issue stays unassigned with history
       `assigned` to_value `none`.
   - `request_exception(...)` delegates to `ExceptionService.request`.
2. **`exceptions.py`** `ExceptionService`:
   - `request(session, issue_id, reason, duration, actor_ref, is_admin) -> str`.
   - `approve(session, exception_id, actor_ref, member_id, is_admin, note)`, `reject(...)`
     and `revoke(session, exception_id, actor_ref, member_id, is_admin)`.
   - `list(session, status, issue_id, approver_ref, limit, cursor) -> (items,
     next_cursor)`.
   - `expire_due(session, now) -> int`.
   - Rules exactly as in design section 11:
     - one `requested` or `active` exception per issue -> `CONFLICT`;
     - `forever` needs admin;
     - the approver is the team lead of the deciding team (decision `owner_team`, else
       the assignee team slug via `owners.team_ref`, else `default_team`), else None
       (admin-only approval);
     - an admin requester makes the exception active at once;
     - a non-admin requester cannot approve their own request;
     - approving a request whose issue is not in `OPEN_STATUSES` -> `CONFLICT`;
     - `expires_at` = decision time + 7, 30 or 90 days, or None.
   - Approve: issue `ignored`, resolution `accepted_risk`, history `exception_approved`.
   - Expiry: `expired`, issue `ignored` -> `unresolved`, resolution cleared, history
     `exception_expired`, notification to the assignee ref.
   - Revoke: issue -> `unresolved`.
   - Approver authorization: `is_admin`, or `member_id` set and
     `resolver.is_member_of(member_id, approver_ref)`.
   - Notify the approver on request through `NotificationSender` when it is configured
     (optional dataclass field, default None).
3. **`occurrences.py`** `linked(session, issue_id, limit=100) -> OccurrenceResult(items:
   tuple[LinkedIssue, ...], key_versions: tuple[int, ...])`:
   - Only for credential classes.
   - Other issues (`status != resolved`) that share any `(fingerprint_key_version,
     fingerprint)` with the issue's findings.
   - Uses the index `ix_findings_key_fingerprint`.
   - `LinkedIssue(issue_id, short_id, source, display_locator, status, severity,
     exposure_visibility)`.
4. **`verification.py`** `VerifyRescanHandler` (`kind = VERIFY_RESCAN`):
   1. Loads the issue in its own transaction. Unless it is `pending_verification`,
      return done.
   2. Calls `rescanner.rescan(issue_id)` outside the transaction.
   3. `FOUND` or `CLEAN` -> done (the record path already applied the outcome).
   4. `INCONCLUSIVE` or `ERROR` -> new transaction: history `verification_inconclusive`
      (to_value = `coverage` value or error code value), audit, and a notification to
      the assignee ref when one exists. The issue stays `pending_verification`.

   Exceptions from the rescanner -> treated as `ERROR` with `FETCH_FAILED`.
5. **`digest.py`** `OwnerDigestHandler` (`kind = OWNER_DIGEST`):
   - For each `assignee_member_id` with open issues, take `ref = owners.member_ref(id)`
     (skip None).
   - Build the body: counts by status; up to 20 lines `TELE-<n> <rule_class> <severity>
     <visibility> risk <score>` ordered by `risk_score desc`; short ids of `requested`
     exceptions whose `approver_ref == ref`; short ids of active exceptions on the
     member's issues that expire within 7 days.
   - Members with only approvals also get a digest.
   - Channel: `SLACK` when `resolver.resolve(ref).slack_user_ids` is non-empty, else
     `EMAIL` when emails exist, else skip.
   - One `NotificationSender.send` per member, actor `system:digest`.
6. **`maintenance.py`** `ExceptionMaintenanceProvider` (ScheduleProvider):
   - `tick(session, now)`, guarded to once per 60 s per process.
   - Calls `exceptions.expire_due(session, now)`.
   - When `digest_cron` is set and the latest fire is within the last hour, enqueues
     `OWNER_DIGEST` with `idempotency_key=f"digest:{int(fire.timestamp()) // 60}"`.
     A fire older than one hour (for example at startup) is never enqueued.

## Pitfalls

- The idempotency key must be per history entry, so a second remediation request after a
  reopen enqueues a new rescan.
- `include_linked` must not recurse: apply only to the direct linked set.
- Do not resolve in `VerifyRescanHandler`; that is the record path's job.
- Exception `reason` is never put into notifications or audit details.
- Expiry and approval must re-check the issue status inside the transaction.
  `ignored` -> `unresolved` happens only if the issue is still `ignored`.

## Tests (`tests/issues/v11_remediation/`, SQLite harness, fakes for ports)

- remediation from `unresolved` -> `pending_verification`, one task enqueued, history
  `verification_requested`; from `resolved` -> `INVALID_TRANSITION`;
- an issue without `resource_ref_json` -> pending, 0 rescans;
- include_linked with 2 linked credential issues -> 3 pending, 3 tasks; a PII issue
  ignores include_linked;
- resolve `false_positive` without a note -> `VALIDATION_ERROR`; `manual_unverified` by a
  non-admin -> `FORBIDDEN`; by an admin -> resolved with that resolution;
- exception request by a member -> `requested` and the approver notified with a short id
  only; a second request -> `CONFLICT`; `forever` by a non-admin -> `FORBIDDEN`;
- approve by the lead -> `ignored` / `accepted_risk` with expires_at = +30d; approve by
  the requester -> `FORBIDDEN`; approve after the issue was resolved -> `CONFLICT`;
- revoke -> `unresolved`;
- `expire_due` at +31d -> `expired` and the issue `unresolved`, history
  `exception_expired`;
- occurrences: shared fingerprint across 3 issues, a different key version is not
  linked, a resolved issue is excluded;
- VerifyRescanHandler: fake rescanner `INCONCLUSIVE` with coverage `too_large` -> history
  `verification_inconclusive` (to_value `too_large`), notification sent, status
  unchanged; `CLEAN` -> no change by this handler;
- digest: body contains short ids and no `display_title` / `display_locator` text
  (assert the fixture title string is absent); Slack is chosen when the member has a
  slack id;
- maintenance: a fire 2 h ago -> no enqueue; a fire 5 min ago -> one enqueue; the same
  fire twice -> the queue reports a duplicate (idempotency).

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/occurrences.py src/telescreen/issues/verification.py src/telescreen/issues/digest.py src/telescreen/issues/maintenance.py`
and `<tests>` = `tests/issues/v11_remediation`.

## Completion Criteria

- [x] All six modules exist with the stated classes; each file is under 1000 lines.
- [x] Every test case above passes; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:20 JST
**Tasks Completed**: Implemented the six remediation modules and SQLite behavior tests for verification requests, reasoned resolution, exceptions, linked occurrences, rescan handling, owner digest and maintenance. The record-path `until_regressed` transition remains owned by v11-p05 as specified in that plan; p06 does not edit or import its record-path modules.
**Tasks In Progress**: None in this plan.
**Blockers**: None.
**Reconciliation requests**: None; the accepted v11-p05 plan directly implements same-key-version `until_regressed` handling in its record path.
**Hashes**: `src/telescreen/issues/lifecycle.py` before=absent after=51dd504de8debee55ebf86fac0b7cb26e77bdafa423fe5d153e645b3778bd9a1; `src/telescreen/issues/exceptions.py` before=absent after=d572ca83ed3fc3d5759e659bebec5d33485b7cd6324f3575171eea361a295735; `src/telescreen/issues/occurrences.py` before=absent after=56614d97cb03d35c5cc760e248faf51339d66fb2daa4377af7b50dcd97135226; `src/telescreen/issues/verification.py` before=absent after=cd5c9a9af3c2e23fe8df57df7ec21f81205b52f291c76cc883325abb66de252c; `src/telescreen/issues/digest.py` before=absent after=a488413f215d558584fb95ee52e6731b94e403ee7f518844798f50326b57788c; `src/telescreen/issues/maintenance.py` before=absent after=4cb46b360f051ad7b9c35c7aed1f5f1639e7d4680ad9414497d2315bd750ae5a; `tests/issues/v11_remediation/test_remediation.py` before=absent after=a9f0b82052049f1bb9e7fe1a2095c771528aa51a3614aef3cbb9cb0e8bdab829.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/occurrences.py src/telescreen/issues/verification.py src/telescreen/issues/digest.py src/telescreen/issues/maintenance.py tests/issues/v11_remediation` -> exit 0 (log: `/tmp/telescreen-v11-p06-issues-remediation-format-check-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/occurrences.py src/telescreen/issues/verification.py src/telescreen/issues/digest.py src/telescreen/issues/maintenance.py tests/issues/v11_remediation` -> exit 0 (log: `/tmp/telescreen-v11-p06-issues-remediation-ruff-check-final.log`).
- `uv run --frozen ty check src/telescreen/issues/lifecycle.py src/telescreen/issues/exceptions.py src/telescreen/issues/occurrences.py src/telescreen/issues/verification.py src/telescreen/issues/digest.py src/telescreen/issues/maintenance.py tests/issues/v11_remediation` -> exit 0 (log: `/tmp/telescreen-v11-p06-issues-remediation-ty-check-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/issues/v11_remediation` -> exit 0, 11 passed, 0 failed (log: `/tmp/telescreen-v11-p06-issues-remediation-pytest-final.log`).
- `uv run --frozen python tools/typelint.py` plus the p06 path filter `grep -E '(^|/)(src/telescreen/issues/(lifecycle|exceptions|occurrences|verification|digest|maintenance)\.py|tests/issues/v11_remediation/)' /tmp/telescreen-v11-p06-issues-remediation-typelint-final-2.log` -> scoped grep has no matches (overall scoped gate exit 0); whole-repository typelint exits 1 with 61 diagnostics in sibling-owned paths (complete log: `/tmp/telescreen-v11-p06-issues-remediation-typelint-final-2.log`).
- `wc -l` of the six modules and test file -> 297, 443, 78, 122, 158, 49 and 745 lines respectively; all are below 1000.
- `git status --short` scoped to this plan's write paths -> only the plan, six new modules and new tests directory are changed.
**Notes**: Superseded source iterations had these failures, all resolved by the final source-matched passing gates above: scoped `uv run --frozen ruff check --no-cache <src> <tests>` exited 1 with 22 lint findings (log: `/tmp/telescreen-v11-p06-issues-remediation-ruff-initial.log`); scoped `uv run --frozen ty check <src> <tests>` exited 1 with 6 diagnostics due to the wrong `RescanKind` import (log: `/tmp/telescreen-v11-p06-issues-remediation-ty-3.log`); scoped `uv run --frozen pytest -p no:cacheprovider tests/issues/v11_remediation` exited 1 with 7 passed and 3 failed on interim fixture/timestamp/cron assumptions (log: `/tmp/telescreen-v11-p06-issues-remediation-pytest-3.log`). The current-source focused suite is 11 passed, 0 failed; no tests are skipped, xfailed or weakened.
