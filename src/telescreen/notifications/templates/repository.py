"""Persistence helpers for notification template versions."""

from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.db.models.notifications import NotificationTemplateRow


class TemplateRepository:
    """Load and insert template versions inside caller-owned sessions."""

    async def versions(
        self, session: AsyncSession, name: str, lock: bool = False
    ) -> tuple[NotificationTemplateRow, ...]:
        """Return every version of a name ordered by version, optionally row-locked."""
        statement: Select[tuple[NotificationTemplateRow]] = (
            select(NotificationTemplateRow)
            .where(NotificationTemplateRow.name == name)
            .order_by(NotificationTemplateRow.version)
        )
        if lock:
            statement = statement.with_for_update()
        rows: Sequence[NotificationTemplateRow] = (await session.scalars(statement)).all()
        return tuple(_normalized(row) for row in rows)

    async def active(self, session: AsyncSession, name: str) -> NotificationTemplateRow | None:
        """Return the active version of a name, if any."""
        row: NotificationTemplateRow | None = await session.scalar(
            select(NotificationTemplateRow).where(
                NotificationTemplateRow.name == name,
                NotificationTemplateRow.active.is_(True),
            )
        )
        return _normalized(row) if row is not None else None

    async def latest_version(self, session: AsyncSession, name: str) -> int:
        """Return the highest stored version of a name, or 0 when none exists."""
        value: int | None = await session.scalar(
            select(func.max(NotificationTemplateRow.version)).where(
                NotificationTemplateRow.name == name
            )
        )
        return value or 0

    async def insert(self, session: AsyncSession, row: NotificationTemplateRow) -> None:
        """Add a template version and flush it in the caller transaction."""
        session.add(row)
        await session.flush()

    async def names(self, session: AsyncSession) -> tuple[str, ...]:
        """Return every stored template name in sorted order."""
        rows: Sequence[str] = (
            await session.scalars(
                select(NotificationTemplateRow.name)
                .distinct()
                .order_by(NotificationTemplateRow.name)
            )
        ).all()
        return tuple(rows)


def _normalized(row: NotificationTemplateRow) -> NotificationTemplateRow:
    """Normalize a loaded timestamp to UTC."""
    row.created_at = ensure_utc(row.created_at)
    return row
