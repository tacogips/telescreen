"""Transport selection and workflow-engine handoff for Police tasks."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.engine import Row, ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, parse_json
from telescreen.core.vocabulary import EngineTaskKind, PoliceTaskStatus
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.police.repository import PoliceRepository
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest
from telescreen.ports.workflow import TaskQueue, TaskSpec


class PoliceTransport(Protocol):
    """Transport interface hidden behind PoliceGateway."""

    async def deliver(self, session: AsyncSession, task: PoliceTaskRow) -> None:
        """Schedule delivery for one task when the transport requires it."""
        ...


@dataclass(frozen=True)
class PullQueueTransport:
    """Leave pull tasks queued for a worker claim."""

    async def deliver(self, session: AsyncSession, task: PoliceTaskRow) -> None:
        """Keep the task available in the pull queue."""
        del session, task


@dataclass(frozen=True)
class WebhookTransport:
    """Queue a durable push delivery attempt."""

    queue: TaskQueue
    clock: Clock

    async def deliver(self, session: AsyncSession, task: PoliceTaskRow) -> None:
        """Enqueue one engine task for repeated delivery attempts."""
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.POLICE_PUSH_DELIVERY,
                task.id,
                parse_json("{}"),
                self.clock.now(),
                f"police-push:{task.id}:1",
            ),
        )


@dataclass(frozen=True)
class RoutingPoliceGateway(PoliceGateway):
    """Persist tasks and route each to an eligible push or pull worker."""

    pull: PullQueueTransport
    webhook: WebhookTransport
    clock: Clock
    repository: PoliceRepository = PoliceRepository()

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        """Create a queued task and schedule it with a matching transport."""
        task_id: str = new_id()
        task: PoliceTaskRow = PoliceTaskRow(
            id=task_id,
            task_type=request.task_type,
            labels=",".join(request.labels),
            input_json=dump_json(request.input),
            status=PoliceTaskStatus.QUEUED.value,
            worker_id=None,
            attempt=0,
            max_attempts=request.max_attempts,
            deadline=request.deadline,
            step_run_id=request.step_run_id,
            collector_config_id=request.collector_config_id,
            created_at=self.clock.now(),
        )
        session.add(task)
        await session.flush()
        workers: tuple[PoliceWorkerRow, ...] = await self.repository.matching_push_workers(
            session, request.task_type, request.labels
        )
        if workers:
            selected: PoliceWorkerRow = workers[0]
            task.worker_id = selected.id
            await self.webhook.deliver(session, task)
        else:
            await self.pull.deliver(session, task)
        return task_id

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        """Cancel a task unless it already reached a terminal status."""
        task: PoliceTaskRow | None = await self.repository.task(session, task_id)
        if task is None or task.status in {
            PoliceTaskStatus.COMPLETED.value,
            PoliceTaskStatus.FAILED.value,
            PoliceTaskStatus.EXPIRED.value,
            PoliceTaskStatus.CANCELLED.value,
        }:
            return
        task.status = PoliceTaskStatus.CANCELLED.value
        await self.webhook.queue.cancel_by_ref(session, task_id)


@dataclass(frozen=True)
class Capability:
    """Worker capability sets decoded from normalized columns."""

    task_types: frozenset[str]
    labels: frozenset[str]


def capability(worker: PoliceWorkerRow) -> Capability:
    """Build a worker's capability sets."""
    return Capability(
        frozenset(filter(None, worker.task_types.split(","))),
        frozenset(filter(None, worker.labels.split(","))),
    )


async def matching_pull_tasks(
    session: AsyncSession, worker: PoliceWorkerRow, task_types: tuple[str, ...], limit: int
) -> tuple[PoliceTaskRow, ...]:
    """Lock and return queued tasks compatible with a pull worker."""
    worker_capability: Capability = capability(worker)
    allowed: frozenset[str] = worker_capability.task_types.intersection(task_types)
    if not allowed:
        return ()
    candidates: Sequence[Row[tuple[str, str]]] = (
        await session.execute(
            select(PoliceTaskRow.id, PoliceTaskRow.labels)
            .where(
                PoliceTaskRow.status == PoliceTaskStatus.QUEUED.value,
                PoliceTaskRow.task_type.in_(allowed),
                (PoliceTaskRow.worker_id.is_(None)) | (PoliceTaskRow.worker_id == worker.id),
            )
            .order_by(PoliceTaskRow.created_at, PoliceTaskRow.id)
        )
    ).all()
    matching_ids: list[str] = []
    for task_id, labels in candidates:
        if frozenset(filter(None, labels.split(","))).issubset(worker_capability.labels):
            matching_ids.append(task_id)
            if len(matching_ids) == limit:
                break
    if not matching_ids:
        return ()
    result: ScalarResult[PoliceTaskRow] = await session.scalars(
        select(PoliceTaskRow)
        .where(
            PoliceTaskRow.id.in_(matching_ids),
            PoliceTaskRow.status == PoliceTaskStatus.QUEUED.value,
            (PoliceTaskRow.worker_id.is_(None)) | (PoliceTaskRow.worker_id == worker.id),
        )
        .order_by(PoliceTaskRow.created_at, PoliceTaskRow.id)
        .with_for_update(skip_locked=True)
    )
    return tuple(result.all())
