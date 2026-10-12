"""Regression tests for issue-detail B1, B4, B6, and B7 behavior."""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.routers.issues_common import IssueItemExtras, issue_item_extras
from telescreen.core.vocabulary import ActorDisplayKind, RuleClass
from telescreen.db.models import FindingRow, IssueCommentRow, IssueRow
from telescreen.directory import DirectoryRecipientResolver
from telescreen.directory.actors import DirectoryActorDirectory
from telescreen.issues.activity.comments import CommentService
from telescreen.issues.activity.feed import ActivityItem, ActivityPage, activity_feed
from telescreen.issues.detail.navigation import EventSelection, select_event
from telescreen.issues.detail.stats import StatBucket, issue_stats
from telescreen.issues.detail.tags import Tag, TagDistribution, tag_distribution
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.findings import MaskedFinding, RecordOutcome
from tests.issues.test_issues import (
    IssueHarness,
    make_issue_harness,
    masked_finding,
    resource_scan,
)
from tests.support.samples import Sample, github_token


@dataclass(frozen=True)
class FakeActors:
    """Resolve known members to stable display names in activity tests."""

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Return one display record per requested reference."""
        del session
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.MEMBER, "Alice Example", "alice", True)
            for ref in refs
        )


@pytest.mark.asyncio
async def test_b1_oldest_and_latest_follow_chronological_order() -> None:
    """B1: previous is older and oldest/latest selectors are chronological."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str | None = None
        first_id: str | None = None
        for index in range(3):
            finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, f"b1-{index}")
            async with harness.db.sessions.begin() as session:
                outcome: RecordOutcome = await harness.service.record_scan(
                    session,
                    resource_scan(
                        harness,
                        (finding,),
                        f"b1-scan-{index}",
                        resource_locator="b1-resource",
                    ),
                )
                issue_id = outcome.issue_ids[0]
                rows: tuple[FindingRow, ...] = tuple(
                    await session.scalars(
                        select(FindingRow)
                        .where(FindingRow.issue_id == issue_id)
                        .order_by(FindingRow.observed_at, FindingRow.id)
                    )
                )
                if index == 0:
                    first_id = rows[0].id
            harness.clock.advance(timedelta(minutes=1))
        assert issue_id is not None
        async with harness.db.sessions() as session:
            issue_rows: tuple[FindingRow, ...] = tuple(
                await session.scalars(
                    select(FindingRow)
                    .where(FindingRow.issue_id == issue_id)
                    .order_by(FindingRow.observed_at, FindingRow.id)
                )
            )
            oldest: EventSelection = await select_event(session, (issue_id,), "oldest")
            latest: EventSelection = await select_event(session, (issue_id,), "latest")
        assert first_id == issue_rows[0].id
        assert oldest.event.id == first_id
        assert oldest.previous_id is None
        assert oldest.next_id == issue_rows[1].id
        assert oldest.position == 1
        assert oldest.total == 3
        assert latest.event.id == issue_rows[2].id
        assert latest.previous_id == issue_rows[1].id
        assert latest.next_id is None
        assert latest.position == 3
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b7_tag_distribution_and_stats_include_all_120_findings() -> None:
    """B7: tag percentages and statistics count beyond the former 50-row limit."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "b7-first")
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_scan(
                session,
                resource_scan(harness, (finding,), "b7-scan-1", resource_locator="b7-resource"),
            )
        issue_id: str = outcome.issue_ids[0]
        harness.clock.advance(timedelta(minutes=1))
        middle: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "b7-second")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(harness, (middle,), "b7-scan-2", resource_locator="b7-resource"),
            )
        harness.clock.advance(timedelta(minutes=1))
        remaining: tuple[MaskedFinding, ...] = tuple(
            masked_finding(harness, RuleClass.CREDENTIAL, f"b7-{index}") for index in range(118)
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(harness, remaining, "b7-scan-3", resource_locator="b7-resource"),
            )
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert issue is not None
            tags: TagDistribution = await tag_distribution(
                session, (issue_id,), (None, None), False
            )
            stats: tuple[StatBucket, ...] = await issue_stats(
                session,
                (issue_id,),
                (issue.first_seen, harness.clock.now() + timedelta(days=1)),
                "day",
                harness.clock.now(),
            )
        detector: Tag = next(tag for tag in tags.tags if tag.key == "detector")
        assert tags.total == 120
        assert detector.values[0].count == 120
        assert detector.values[0].percent == 100.0
        assert sum(item.count for item in stats) == 120
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b4_assignee_display_uses_actor_directory() -> None:
    """B4: issue assignee displays the directory name instead of the member id."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "b4")
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "b4-scan")
            )
            issue: IssueRow | None = await session.get(IssueRow, outcome.issue_ids[0])
            assert issue is not None
            issue.assignee_member_id = "member-raw-id"
            extras: tuple[IssueItemExtras, ...] = await issue_item_extras(
                session, (issue,), "viewer-1", FakeActors()
            )
        assert extras[0].assignee is not None
        assert extras[0].assignee.display_name == "Alice Example"
        assert extras[0].assignee.display_name != "member-raw-id"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b6_comment_is_redacted_persisted_and_has_actor_display() -> None:
    """B6: a posted comment survives reload and the activity feed resolves its actor."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "b6")
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "b6-scan")
            )
        issue_id: str = outcome.issue_ids[0]
        sample: Sample = github_token()
        comment_service: CommentService = CommentService(
            harness.redactor,
            harness.audit,
            harness.clock,
            None,
            DirectoryRecipientResolver(),
            FakeActors(),
        )
        async with harness.db.sessions.begin() as session:
            comment: IssueCommentRow = await comment_service.create(
                session, issue_id, f"Reviewed {sample.value}", "member:alice"
            )
            comment_id: str = comment.id
        async with harness.db.sessions() as session:
            page: ActivityPage = await activity_feed(
                session, issue_id, None, 50, FakeActors(), "member:alice", False
            )
        activity: ActivityItem = next(item for item in page.items if item.id == comment_id)
        assert activity.comment is not None
        assert activity.comment.body != f"Reviewed {sample.value}"
        assert sample.value not in activity.comment.body
        assert activity.actor.display_name == "Alice Example"
        assert activity.kind == "commented"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_system_actors_are_named_instead_of_unknown() -> None:
    """Scanner and workflow history rows show the system actor name, not "Unknown"."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "sys-actor")
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "sys-actor-scan")
            )
        issue_id: str = outcome.issue_ids[0]
        async with harness.db.sessions() as session:
            page: ActivityPage = await activity_feed(
                session, issue_id, None, 50, DirectoryActorDirectory(), "member:alice", False
            )
        assert page.items
        system_items: tuple[ActivityItem, ...] = tuple(
            item for item in page.items if item.actor.ref.startswith("system:")
        )
        assert system_items
        assert all(item.actor.display_name == "Telescreen" for item in system_items)
        assert all(item.actor.kind is ActorDisplayKind.SYSTEM for item in system_items)
    finally:
        await harness.close()
