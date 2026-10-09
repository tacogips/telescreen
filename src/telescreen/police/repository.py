"""Queries and updates for Police worker and task rows."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow


@dataclass(frozen=True)
class PoliceRepository:
    """Small session-scoped repository for police persistence."""

    async def worker(self, session: AsyncSession, worker_id: str) -> PoliceWorkerRow | None:
        """Load one worker by its public id."""
        return await session.get(PoliceWorkerRow, worker_id)

    async def task(self, session: AsyncSession, task_id: str) -> PoliceTaskRow | None:
        """Load one task by its public id."""
        return await session.get(PoliceTaskRow, task_id)

    async def workers(self, session: AsyncSession) -> tuple[PoliceWorkerRow, ...]:
        """Return registered workers ordered by registration time."""
        result: ScalarResult[PoliceWorkerRow] = await session.scalars(
            select(PoliceWorkerRow).order_by(PoliceWorkerRow.created_at)
        )
        return tuple(result.all())

    async def matching_push_workers(
        self, session: AsyncSession, task_type: str, labels: tuple[str, ...]
    ) -> tuple[PoliceWorkerRow, ...]:
        """Find active push workers whose capabilities satisfy a task."""
        result: ScalarResult[PoliceWorkerRow] = await session.scalars(
            select(PoliceWorkerRow)
            .where(PoliceWorkerRow.mode == "push", PoliceWorkerRow.status == "active")
            .order_by(PoliceWorkerRow.last_delivery_at, PoliceWorkerRow.created_at)
        )
        requested: frozenset[str] = frozenset(labels)
        return tuple(
            worker
            for worker in result.all()
            if task_type in worker.task_types.split(",")
            and requested.issubset(frozenset(filter(None, worker.labels.split(","))))
        )

    async def active_workers(self, session: AsyncSession) -> tuple[PoliceWorkerRow, ...]:
        """Return active workers for admin or capability selection."""
        result: ScalarResult[PoliceWorkerRow] = await session.scalars(
            select(PoliceWorkerRow)
            .where(PoliceWorkerRow.status == "active")
            .order_by(PoliceWorkerRow.last_delivery_at, PoliceWorkerRow.created_at)
        )
        return tuple(result.all())
