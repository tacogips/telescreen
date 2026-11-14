"""SQL-backed durable task queue for workflow execution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Result, Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, parse_json
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus, ErrorCode
from telescreen.db.models import EngineTaskRow
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind, TaskSpec

# Column limits of engine_tasks; SQLite does not enforce varchar lengths, so check here.
REF_ID_MAX_LENGTH: int = 36
IDEMPOTENCY_KEY_MAX_LENGTH: int = 256


@dataclass
class SqlTaskQueue:
    """Persist task requests and lease ready work to workers."""

    clock: Clock

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Insert a task once; an idempotency collision returns false."""
        if len(spec.ref_id) > REF_ID_MAX_LENGTH:
            raise ValueError(f"task ref_id exceeds {REF_ID_MAX_LENGTH} characters")
        if len(spec.idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH:
            raise ValueError(
                f"task idempotency_key exceeds {IDEMPOTENCY_KEY_MAX_LENGTH} characters"
            )
        existing: str | None = await session.scalar(
            select(EngineTaskRow.id).where(EngineTaskRow.idempotency_key == spec.idempotency_key)
        )
        if existing is not None:
            return False
        row: EngineTaskRow = EngineTaskRow(
            id=new_id(),
            kind=spec.kind.value,
            ref_id=spec.ref_id,
            payload_json=dump_json(spec.payload),
            status=EngineTaskStatus.READY.value,
            available_at=ensure_utc(spec.available_at),
            attempts=0,
            max_attempts=spec.max_attempts,
            idempotency_key=spec.idempotency_key,
            created_at=self.clock.now(),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            return False
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Cancel ready or leased work associated with a reference."""
        result: Result[tuple[str]] = await session.execute(
            update(EngineTaskRow)
            .where(
                EngineTaskRow.ref_id == ref_id,
                EngineTaskRow.status.in_(
                    (EngineTaskStatus.READY.value, EngineTaskStatus.LEASED.value)
                ),
            )
            .values(
                status=EngineTaskStatus.CANCELLED.value,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(EngineTaskRow.id)
        )
        cancelled_ids: Sequence[str] = result.scalars().all()
        return len(cancelled_ids)

    async def claim(
        self,
        session: AsyncSession,
        worker_id: str,
        kinds: tuple[EngineTaskKind, ...],
        limit: int,
        lease: timedelta,
    ) -> list[ClaimedTask]:
        """Lease due tasks in stable order, skipping rows held by other workers."""
        if limit <= 0 or lease <= timedelta(0):
            return []
        now: datetime = self.clock.now()
        query: Select[tuple[EngineTaskRow]] = (
            select(EngineTaskRow)
            .where(
                EngineTaskRow.status == EngineTaskStatus.READY.value,
                EngineTaskRow.available_at <= now,
            )
            .order_by(EngineTaskRow.available_at, EngineTaskRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if kinds:
            query = query.where(EngineTaskRow.kind.in_(tuple(kind.value for kind in kinds)))
        rows: list[EngineTaskRow] = list((await session.scalars(query)).all())
        claimed: list[ClaimedTask] = []
        for row in rows:
            row.status = EngineTaskStatus.LEASED.value
            row.lease_owner = worker_id
            row.lease_expires_at = now + lease
            row.attempts += 1
            claimed.append(
                ClaimedTask(
                    id=row.id,
                    kind=EngineTaskKind(row.kind),
                    ref_id=row.ref_id,
                    payload=parse_json(row.payload_json),
                    attempt=row.attempts,
                    max_attempts=row.max_attempts,
                    idempotency_key=row.idempotency_key,
                )
            )
        await session.flush()
        return claimed

    async def apply_result(
        self, session: AsyncSession, task_id: str, result: TaskResult
    ) -> EngineTaskStatus | None:
        """Complete, retry or dead-letter a leased task."""
        row: EngineTaskRow | None = await session.get(EngineTaskRow, task_id)
        if row is None or row.status != EngineTaskStatus.LEASED.value:
            return None
        row.lease_owner = None
        row.lease_expires_at = None
        if result.kind is TaskResultKind.DONE:
            row.status = EngineTaskStatus.DONE.value
        elif result.kind is TaskResultKind.RETRY and result.error_code is ErrorCode.CONFLICT:
            row.status = EngineTaskStatus.READY.value
            row.available_at = ensure_utc(result.available_at or self.clock.now())
            row.last_error_code = ErrorCode.CONFLICT.value
            row.attempts = max(row.attempts - 1, 0)
        elif result.kind is TaskResultKind.RETRY and row.attempts < row.max_attempts:
            row.status = EngineTaskStatus.READY.value
            row.available_at = ensure_utc(result.available_at or self.clock.now())
            row.last_error_code = result.error_code.value if result.error_code else None
        else:
            row.status = EngineTaskStatus.DEAD.value
            row.last_error_code = result.error_code.value if result.error_code else None
        await session.flush()
        return EngineTaskStatus(row.status)

    async def reclaim_expired(self, session: AsyncSession, now: datetime) -> int:
        """Return expired leases to the ready queue."""
        now = ensure_utc(now)
        result: Result[tuple[str]] = await session.execute(
            update(EngineTaskRow)
            .where(
                EngineTaskRow.status == EngineTaskStatus.LEASED.value,
                EngineTaskRow.lease_expires_at <= now,
            )
            .values(
                status=EngineTaskStatus.READY.value,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(EngineTaskRow.id)
        )
        reclaimed_ids: Sequence[str] = result.scalars().all()
        return len(reclaimed_ids)
