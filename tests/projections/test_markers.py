"""Behavior tests for transactional projection change markers and readers."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import IssueStatus
from telescreen.db.models import (
    ApiTokenRow,
    IssueRow,
    MemberRow,
    ProjectionChangeRow,
)
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
)
from telescreen.ports.inventory_commands import IssueLinkState
from telescreen.projections.entities import read_directory_entity, read_issue_link
from telescreen.projections.markers import install_projection_markers, uninstall_projection_markers
from telescreen.projections.resync import resync
from tests.support.db import SqliteHarness, make_sqlite_harness

_NOW: datetime = datetime(2026, 10, 16, tzinfo=UTC)


def _issue(issue_id: str, status: str = IssueStatus.UNRESOLVED.value) -> IssueRow:
    return IssueRow(
        id=issue_id,
        number=41,
        grouping_key=f"group-{issue_id}",
        source="github",
        resource_key="resource-key",
        display_locator="safe-locator",
        display_title="safe title",
        rule_class="credential",
        severity="high",
        status=status,
        first_seen=_NOW,
        last_seen=_NOW,
        last_checked_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_markers_track_projected_fields_only_and_rollback_with_source() -> None:
    """Capture only projected changes and keep markers in the source transaction."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    other: async_sessionmaker[AsyncSession] = async_sessionmaker(
        harness.engine, expire_on_commit=False
    )
    install_projection_markers(harness.sessions, clock)
    try:
        async with harness.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="member-1",
                    handle="alice",
                    display_name="Alice",
                    active=True,
                    role="admin",
                    email=None,
                    slack_user_id=None,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 1
        async with harness.sessions.begin() as session:
            member: MemberRow = await session.get_one(MemberRow, "member-1")
            member.last_sign_in_at = _NOW
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 1
            member.display_name = "Alice A."
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 2
            session.add(_issue("issue-1"))
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 3
            issue: IssueRow = await session.get_one(IssueRow, "issue-1")
            issue.last_seen = datetime(2026, 10, 17, tzinfo=UTC)
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 3
            issue.status = IssueStatus.RESOLVED.value
            await session.flush()
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 4
        async with other.begin() as session:
            session.add(
                MemberRow(
                    id="member-2",
                    handle="bob",
                    display_name="Bob",
                    active=True,
                    role=None,
                    email=None,
                    slack_user_id=None,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        async with harness.sessions.begin() as session:
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 4
        with pytest.raises(RuntimeError):
            async with harness.sessions.begin() as session:
                member = await session.get_one(MemberRow, "member-1")
                member.display_name = "Rolled back"
                await session.flush()
                raise RuntimeError("rollback")
        async with harness.sessions() as session:
            assert len((await session.scalars(select(ProjectionChangeRow))).all()) == 4
    finally:
        uninstall_projection_markers(harness.sessions)
        await harness.close()


@pytest.mark.asyncio
async def test_entity_readers_and_resync_publish_safe_typed_values() -> None:
    """Read approved fields and seed one marker for every current projection entity."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                [
                    MemberRow(
                        id="member-1",
                        handle="alice",
                        display_name="Alice",
                        active=False,
                        role="admin",
                        email="alice@example.invalid",
                        slack_user_id="U123",
                        created_at=_NOW,
                        updated_at=_NOW,
                    ),
                    ApiTokenRow(
                        id="token-1",
                        label="read-only",
                        token_hash="a" * 64,
                        scopes="issues:read,data:read",
                        member_id="member-1",
                        created_at=_NOW,
                        revoked_at=None,
                    ),
                    _issue("issue-1"),
                ]
            )
        async with harness.sessions() as session:
            member: DirectoryEntity = await read_directory_entity(
                session, DirectoryEntityKind.MEMBER, "member-1", 1
            )
            assert member.present is True
            assert isinstance(member.payload, MemberEntity)
            assert member.payload.active is False
            token: DirectoryEntity = await read_directory_entity(
                session, DirectoryEntityKind.API_TOKEN, "token-1", 2
            )
            assert isinstance(token.payload, ApiTokenEntity)
            assert token.payload.label == "read-only"
            assert token.payload.scopes == ("issues:read", "data:read")
            missing: DirectoryEntity = await read_directory_entity(
                session, DirectoryEntityKind.MEMBER, "gone", 3
            )
            assert missing.present is False and missing.payload is None
            link: IssueLinkState = await read_issue_link(session, "issue-1")
            assert link.short_id == "TELE-41" and link.present is True
        count: int = await resync(harness.sessions, clock, batch=2)
        assert count == 4
        async with harness.sessions() as session:
            markers: tuple[ProjectionChangeRow, ...] = tuple(
                (await session.scalars(select(ProjectionChangeRow))).all()
            )
            assert {marker.entity_ref for marker in markers} == {
                "member:member-1",
                "api_token:token-1",
                "issue:issue-1",
                "ownership_rules:rules",
            }
    finally:
        await harness.close()
