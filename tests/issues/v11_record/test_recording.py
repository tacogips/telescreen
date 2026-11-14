"""Focused issue record-path coverage for v1.1 exposure and policy behavior."""

from dataclasses import dataclass, replace
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    DetectorKind,
    EngineTaskKind,
    IssueEventKind,
    IssueStatus,
    PolicyEffect,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.db.models import FindingRow, IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.policy_apply import PolicyReevaluateHandler
from telescreen.ports.collectors import Exposure
from telescreen.ports.findings import (
    ExternalFinding,
    ExternalScan,
    LocatorExposure,
    MaskedFinding,
    ResourceScan,
)
from telescreen.ports.policy import PolicyDecision, PolicySubject
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)


@dataclass(frozen=True)
class StaticDecider:
    """Return one fixed safe policy decision for record-path tests."""

    decision: PolicyDecision

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Return the configured decision."""
        del session, subject, now
        return self.decision


@pytest.mark.asyncio
async def test_public_exposure_sets_critical_then_narrowing_restores_base() -> None:
    """Exposure raises credential severity and narrower visibility records history."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "credential-public")
        first: ResourceScan = resource_scan(harness, (finding,), "scan-public")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                replace(first, exposure=Exposure(Visibility.PUBLIC)),
            )
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            assert issue.base_severity == Severity.HIGH.value
            assert issue.severity == Severity.CRITICAL.value
        second: ResourceScan = resource_scan(
            harness,
            (masked_finding(harness, RuleClass.CREDENTIAL, "credential-internal"),),
            "scan-internal",
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                replace(second, exposure=Exposure(Visibility.INTERNAL)),
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.severity == Severity.HIGH.value
        assert any(row.kind == "exposure_changed" for row in history)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_allow_creates_allowed_issue_without_event() -> None:
    """A policy-allowed new finding is recorded without issue-created events."""
    harness: IssueHarness = await make_issue_harness()
    try:
        harness.service.decider = StaticDecider(
            PolicyDecision(PolicyEffect.ALLOW, "hr-records", 1, None, False)
        )
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-allowed")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "scan-allow")
            )
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.ALLOWED.value
        assert harness.events.events == []
        repeated: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-allowed-again")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (repeated,), "scan-allow-again")
            )
        assert harness.events.events == []
    finally:
        await harness.close()


@pytest.mark.parametrize(
    "prior_status",
    (
        IssueStatus.UNRESOLVED,
        IssueStatus.ACKNOWLEDGED,
        IssueStatus.IN_PROGRESS,
        IssueStatus.REGRESSED,
        IssueStatus.PENDING_VERIFICATION,
    ),
)
@pytest.mark.asyncio
async def test_open_statuses_move_to_allowed_when_policy_allows(
    prior_status: IssueStatus,
) -> None:
    """Each open status follows the allow row and records exposure_reduced."""
    harness: IssueHarness = await make_issue_harness()
    try:
        first: MaskedFinding = masked_finding(
            harness, RuleClass.EMAIL, f"email-{prior_status.value}-one"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (first,), f"scan-{prior_status.value}-one")
            )
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = prior_status.value
            issue.policy_name = "hr-records"
            issue.policy_version = 1
        harness.service.decider = StaticDecider(
            PolicyDecision(PolicyEffect.ALLOW, "hr-records", 1, None, False)
        )
        second: MaskedFinding = masked_finding(
            harness, RuleClass.EMAIL, f"email-{prior_status.value}-two"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (second,), f"scan-{prior_status.value}-two")
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.ALLOWED.value
        assert issue.resolution == "exposure_reduced"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_resolved_issue_can_be_allowed_without_created_event() -> None:
    """A resolved issue allowed by current policy remains event-silent."""
    harness: IssueHarness = await make_issue_harness()
    try:
        first: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-resolved-allow-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (first,), "scan-resolved-one")
            )
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = IssueStatus.RESOLVED.value
            issue.resolution = "verified_removed"
        harness.service.decider = StaticDecider(
            PolicyDecision(PolicyEffect.ALLOW, "hr-records", 1, None, False)
        )
        next_finding: MaskedFinding = masked_finding(
            harness, RuleClass.EMAIL, "email-resolved-allow-two"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (next_finding,), "scan-resolved-two")
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.ALLOWED.value
        assert issue.resolution == "policy_changed"
        assert len(harness.events.events) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_external_record_stores_active_fingerprint_version_and_exposure() -> None:
    """External fingerprints keep the active version and locator exposure mapping."""
    harness: IssueHarness = await make_issue_harness()
    try:
        from telescreen.masking.fingerprint import Fingerprinter

        harness.service.fingerprinter = Fingerprinter(
            b"z" * 32, "key-v2", key_version=2, identity_secret=b"x" * 32
        )
        external: ExternalScan = ExternalScan(
            "custom:test",
            "collector-1",
            "external-job",
            DetectorKind.POLICE,
            (
                ExternalFinding(
                    "resource:one",
                    "credential.rule",
                    RuleClass.CREDENTIAL,
                    "masked",
                    "segment:1",
                    0.9,
                    "external-fingerprint",
                ),
            ),
            ("resource:one",),
            True,
            harness.clock.now(),
            (LocatorExposure("resource:one", Exposure(Visibility.PUBLIC)),),
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_external(session, external)
        async with harness.db.sessions() as session:
            finding: FindingRow | None = await session.scalar(select(FindingRow))
            issue: IssueRow | None = await session.scalar(select(IssueRow))
        assert finding is not None
        assert issue is not None
        assert finding.fingerprint_key_version == 2
        assert issue.exposure_visibility == Visibility.PUBLIC.value
        assert issue.severity == Severity.CRITICAL.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_until_regressed_exception_expires_on_new_same_version_fingerprint() -> None:
    """A same-version new observation expires an active until-regressed exception."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-before-exception")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = IssueStatus.IGNORED.value
            session.add(
                IssueExceptionRow(
                    id="exception-v11-record",
                    issue_id=issue.id,
                    reason="temporary acceptance",
                    duration="until_regressed",
                    status="active",
                    requested_by="member:owner",
                    approver_ref="member:lead",
                    decided_by="member:lead",
                    decided_at=harness.clock.now(),
                    expires_at=None,
                    created_at=harness.clock.now(),
                )
            )
        next_finding: MaskedFinding = masked_finding(
            harness, RuleClass.EMAIL, "email-after-exception"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (next_finding,), "scan-2")
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
            exception: IssueExceptionRow | None = await session.get(
                IssueExceptionRow, "exception-v11-record"
            )
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.status == IssueStatus.REGRESSED.value
        assert exception is not None and exception.status == "expired"
        assert exception.decided_by == "system"
        assert any(row.kind == "exception_expired" for row in history)
        assert any(row.kind == "regressed" for row in history)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_manual_reopen_revokes_any_active_exception() -> None:
    """Reopening an ignored issue revokes even a time-limited exception."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-manual-reopen")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "scan-manual-reopen")
            )
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = IssueStatus.IGNORED.value
            session.add(
                IssueExceptionRow(
                    id="exception-manual-reopen",
                    issue_id=issue.id,
                    reason="temporary approval",
                    duration="30d",
                    status="active",
                    requested_by="member:owner",
                    approver_ref="member:lead",
                    decided_by="member:lead",
                    decided_at=harness.clock.now(),
                    expires_at=None,
                    created_at=harness.clock.now(),
                )
            )
        async with harness.db.sessions.begin() as session:
            issue = await harness.service.change_status(
                session, issue.id, IssueStatus.UNRESOLVED, "member:operator"
            )
        async with harness.db.sessions() as session:
            exception: IssueExceptionRow | None = await session.get(
                IssueExceptionRow, "exception-manual-reopen"
            )
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert exception is not None and exception.status == "revoked"
        assert exception.decided_by == "member:operator"
        assert any(row.kind == "exception_revoked" for row in history)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_policy_reevaluate_applies_both_status_flips_without_resolving() -> None:
    """Policy re-evaluation routes both status directions and indexes changes."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-policy-flip")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "scan-policy")
            )
        indexer: RecordingIndexer = RecordingIndexer([], [])
        handler: PolicyReevaluateHandler = PolicyReevaluateHandler(
            harness.db.sessions, harness.service, indexer, harness.clock
        )
        task: ClaimedTask = ClaimedTask(
            "task-policy",
            EngineTaskKind.POLICY_REEVALUATE,
            "policy",
            json_null(),
            1,
            5,
            "policy-test",
        )
        event_count: int = len(harness.events.events)
        harness.service.decider = StaticDecider(
            PolicyDecision(PolicyEffect.ALLOW, "hr-records", 2, None, False)
        )
        first_result: TaskResult = await handler.handle(task)
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert first_result.kind is TaskResultKind.DONE
        assert issue.status == IssueStatus.ALLOWED.value
        assert issue.resolution == "policy_changed"
        harness.service.decider = StaticDecider(
            PolicyDecision(PolicyEffect.VIOLATE, "hr-records", 3, None, False)
        )
        second_result: TaskResult = await handler.handle(task)
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
        issue: IssueRow = issues[0]
        assert second_result.kind is TaskResultKind.DONE
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert issue.resolution is None
        assert len(indexer.issue_ids) == 2
        assert len(harness.events.events) == event_count + 1
        assert harness.events.events[-1].kind is IssueEventKind.ISSUE_CREATED
        assert all(row.resolution != "verified_removed" for row in issues)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_pending_verification_reopens_on_findings_and_resolves_when_clean() -> None:
    """Verification findings reopen; a later complete clean scan verifies removal."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-before-verify")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = IssueStatus.PENDING_VERIFICATION.value
        repeated: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-still-present")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (repeated,), "scan-2")
            )
        async with harness.db.sessions.begin() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
            assert issue.status == IssueStatus.UNRESOLVED.value
            issue.status = IssueStatus.PENDING_VERIFICATION.value
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (), "scan-clean"))
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.status == IssueStatus.RESOLVED.value
        assert issue.resolution == "verified_removed"
        assert any(row.kind == "verification_failed" for row in history)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_unknown_exposure_does_not_overwrite_public_visibility() -> None:
    """Unknown scan exposure leaves a previously known visibility unchanged."""
    harness: IssueHarness = await make_issue_harness()
    try:
        first: ResourceScan = resource_scan(
            harness,
            (masked_finding(harness, RuleClass.CREDENTIAL, "credential-public-known"),),
            "scan-public",
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, replace(first, exposure=Exposure(Visibility.PUBLIC))
            )
        next_scan: ResourceScan = resource_scan(
            harness,
            (masked_finding(harness, RuleClass.CREDENTIAL, "credential-unknown"),),
            "scan-unknown",
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, replace(next_scan, exposure=Exposure(Visibility.UNKNOWN))
            )
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.exposure_visibility == Visibility.PUBLIC.value
        assert issue.severity == Severity.CRITICAL.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_incomplete_scan_never_resolves_and_removed_scan_uses_deleted_reason() -> None:
    """Incomplete coverage is not clean; a complete removed resource is deleted."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-resource")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, replace(resource_scan(harness, (), "scan-partial", complete=False))
            )
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.UNRESOLVED.value
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                replace(resource_scan(harness, (), "scan-removed"), removed=True),
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.RESOLVED.value
        assert issue.resolution == "resource_deleted"
    finally:
        await harness.close()
