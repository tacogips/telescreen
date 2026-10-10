# V11-P06 Issues Remediation: Verification Requests, Reasoned Resolution, Exceptions, Occurrences, Digest

**Status**: Ready
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

- [ ] All six modules exist with the stated classes; each file is under 1000 lines.
- [ ] Every test case above passes; scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
