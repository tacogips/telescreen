"""Queries and persistence helpers for workflow-owned database rows."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import (
    EngineTaskRow,
    EscalationPolicyRow,
    HumanTaskRow,
    StepRunRow,
    TransitionRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)


class WorkflowRepository:
    """Provide stable query operations for workflow services and routers."""

    async def definition(
        self, session: AsyncSession, definition_id: str
    ) -> WorkflowDefinitionRow | None:
        """Load one immutable workflow version by id."""
        return await session.get(WorkflowDefinitionRow, definition_id)

    async def definition_by_name(
        self, session: AsyncSession, name: str, version: int | None = None
    ) -> WorkflowDefinitionRow | None:
        """Load a requested version or the latest active version by name."""
        query: Select[tuple[WorkflowDefinitionRow]] = select(WorkflowDefinitionRow).where(
            WorkflowDefinitionRow.name == name
        )
        if version is not None:
            query = query.where(WorkflowDefinitionRow.version == version)
        else:
            query = query.where(WorkflowDefinitionRow.active.is_(True))
            query = query.order_by(WorkflowDefinitionRow.version.desc())
        return await session.scalar(query.limit(1))

    async def definitions(self, session: AsyncSession) -> Sequence[WorkflowDefinitionRow]:
        """Return the latest active version of each workflow name."""
        rows: Sequence[WorkflowDefinitionRow] = tuple(
            (
                await session.scalars(
                    select(WorkflowDefinitionRow)
                    .where(WorkflowDefinitionRow.active.is_(True))
                    .order_by(WorkflowDefinitionRow.name, WorkflowDefinitionRow.version.desc())
                )
            ).all()
        )
        latest: list[WorkflowDefinitionRow] = []
        names: set[str] = set()
        for row in rows:
            if row.name not in names:
                latest.append(row)
                names.add(row.name)
        return tuple(latest)

    async def run(self, session: AsyncSession, run_id: str) -> WorkflowRunRow | None:
        """Load a run by id."""
        return await session.get(WorkflowRunRow, run_id)

    async def step_run(self, session: AsyncSession, step_run_id: str) -> StepRunRow | None:
        """Load a step visit by id."""
        return await session.get(StepRunRow, step_run_id)

    async def steps(self, session: AsyncSession, run_id: str) -> Sequence[StepRunRow]:
        """Return run steps in creation order."""
        return tuple(
            (
                await session.scalars(
                    select(StepRunRow)
                    .where(StepRunRow.run_id == run_id)
                    .order_by(StepRunRow.started_at, StepRunRow.id)
                )
            ).all()
        )

    async def transitions(self, session: AsyncSession, run_id: str) -> Sequence[TransitionRow]:
        """Return transition history in timestamp order."""
        return tuple(
            (
                await session.scalars(
                    select(TransitionRow)
                    .where(TransitionRow.run_id == run_id)
                    .order_by(TransitionRow.at, TransitionRow.id)
                )
            ).all()
        )

    async def human_tasks(self, session: AsyncSession, run_id: str) -> Sequence[HumanTaskRow]:
        """Return human tasks for a run."""
        return tuple(
            (
                await session.scalars(
                    select(HumanTaskRow)
                    .where(HumanTaskRow.run_id == run_id)
                    .order_by(HumanTaskRow.id)
                )
            ).all()
        )

    async def escalation_policy(
        self, session: AsyncSession, name: str
    ) -> EscalationPolicyRow | None:
        """Load an escalation policy by its unique name."""
        return await session.scalar(
            select(EscalationPolicyRow).where(EscalationPolicyRow.name == name)
        )

    async def task(self, session: AsyncSession, task_id: str) -> EngineTaskRow | None:
        """Load a durable engine task by id."""
        return await session.get(EngineTaskRow, task_id)
