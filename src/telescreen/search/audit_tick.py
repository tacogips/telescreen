"""Scheduled incremental audit indexing."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import SystemClock
from telescreen.db.models import AuditRecordRow
from telescreen.masking.index_guard import IndexGuard
from telescreen.ports.audit import AuditLog
from telescreen.ports.workflow import ScheduleProvider
from telescreen.search.backend import FieldValue, IndexName, SearchBackend, SearchPage, SearchQuery
from telescreen.search.indexer import DocumentIndexer


@dataclass
class AuditIndexSchedule(ScheduleProvider):
    """Index audit records incrementally with a bounded in-memory watermark."""

    sessions: async_sessionmaker[AsyncSession]
    backend: SearchBackend
    guard: IndexGuard
    audit_log: AuditLog
    watermark: int = 0

    async def _initialize(self) -> None:
        """Read the backend's highest indexed sequence on first use."""
        page: SearchPage = await self.backend.search(
            IndexName.AUDIT, SearchQuery("", sort=("seq:desc",), limit=1)
        )
        if page.hits:
            value: FieldValue | tuple[str, ...] | None = page.hits[0].get("seq")
            if isinstance(value, int):
                self.watermark = value

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Index up to 500 later audit records and advance the watermark."""
        del session, now
        if self.watermark == 0:
            await self._initialize()
        async with self.sessions() as read_session:
            rows: list[AuditRecordRow] = list(
                (
                    await read_session.scalars(
                        select(AuditRecordRow)
                        .where(AuditRecordRow.seq > self.watermark)
                        .order_by(AuditRecordRow.seq)
                        .limit(500)
                    )
                ).all()
            )
        indexer: DocumentIndexer = DocumentIndexer(
            self.sessions, self.backend, self.guard, self.audit_log, SystemClock()
        )
        for row in rows:
            await indexer.index_audit_record(row.id)
            self.watermark = row.seq
        return len(rows)
