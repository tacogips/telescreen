"""Batched marker seeding for projection recovery and cutover."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.db.models import (
    ApiTokenRow,
    CollectorConfigRow,
    IssueRow,
    MemberIdentityRow,
    MemberRow,
    ProjectionChangeRow,
    TeamRow,
)


async def resync(sessions: async_sessionmaker[AsyncSession], clock: Clock, batch: int = 500) -> int:
    """Insert one marker per current projection entity, committing each bounded batch."""
    size: int = max(1, batch)
    changes: list[tuple[str, str]] = []
    async with sessions() as session:
        for row_type, kind, prefix in (
            (MemberRow, "directory", "member"),
            (TeamRow, "directory", "team"),
            (MemberIdentityRow, "directory", "identity"),
            (ApiTokenRow, "directory", "api_token"),
            (CollectorConfigRow, "directory", "collector"),
            (IssueRow, "issue_link", "issue"),
        ):
            identifiers: Sequence[str] = tuple(
                (await session.scalars(select(row_type.id).order_by(row_type.id))).all()
            )
            changes.extend((kind, f"{prefix}:{identifier}") for identifier in identifiers)
        changes.append(("directory", "ownership_rules:rules"))
    created: int = 0
    for start in range(0, len(changes), size):
        chunk: tuple[tuple[str, str], ...] = tuple(changes[start : start + size])
        async with sessions.begin() as session:
            for kind, entity_ref in chunk:
                session.add(
                    ProjectionChangeRow(
                        id=new_id(), kind=kind, entity_ref=entity_ref, recorded_at=clock.now()
                    )
                )
        created += len(chunk)
    return created
