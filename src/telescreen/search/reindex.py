"""Full search index rebuild operation."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import SystemClock
from telescreen.db.models import (
    AuditRecordRow,
    FindingRow,
    IssueRow,
    WorkflowRunRow,
)
from telescreen.masking.index_guard import IndexGuard
from telescreen.ports.audit import AuditLog
from telescreen.search.backend import IndexName, SearchBackend
from telescreen.search.indexer import DocumentIndexer


@dataclass(frozen=True)
class ReindexCounts:
    """Number of source rows visited and accepted for each index."""

    issues: int
    findings: int
    runs: int
    audit: int


async def reindex_all(
    sessions: async_sessionmaker[AsyncSession],
    backend: SearchBackend,
    guard: IndexGuard,
    audit_log: AuditLog,
) -> ReindexCounts:
    """Rebuild all four indexes in bounded 1000-row pages."""
    await backend.ensure_indexes()
    indexer: DocumentIndexer = DocumentIndexer(sessions, backend, guard, audit_log, SystemClock())
    issues: int = await _reindex_ids(sessions, indexer, IndexName.ISSUES)
    findings: int = await _reindex_ids(sessions, indexer, IndexName.FINDINGS)
    runs: int = await _reindex_ids(sessions, indexer, IndexName.RUNS)
    audit: int = await _reindex_ids(sessions, indexer, IndexName.AUDIT)
    return ReindexCounts(issues, findings, runs, audit)


async def _reindex_ids(
    sessions: async_sessionmaker[AsyncSession], indexer: DocumentIndexer, index: IndexName
) -> int:
    """Page ids from one source table and submit them to its index."""
    offset: int = 0
    count: int = 0
    while True:
        async with sessions() as session:
            if index is IndexName.ISSUES:
                ids: list[str] = list(
                    (
                        await session.scalars(
                            select(IssueRow.id).order_by(IssueRow.id).offset(offset).limit(1000)
                        )
                    ).all()
                )
            elif index is IndexName.FINDINGS:
                ids = list(
                    (
                        await session.scalars(
                            select(FindingRow.id).order_by(FindingRow.id).offset(offset).limit(1000)
                        )
                    ).all()
                )
            elif index is IndexName.RUNS:
                ids = list(
                    (
                        await session.scalars(
                            select(WorkflowRunRow.id)
                            .order_by(WorkflowRunRow.id)
                            .offset(offset)
                            .limit(1000)
                        )
                    ).all()
                )
            else:
                ids = list(
                    (
                        await session.scalars(
                            select(AuditRecordRow.id)
                            .order_by(AuditRecordRow.seq)
                            .offset(offset)
                            .limit(1000)
                        )
                    ).all()
                )
        if not ids:
            break
        for record_id in ids:
            if index is IndexName.ISSUES:
                await indexer.index_issue(record_id)
            elif index is IndexName.FINDINGS:
                await indexer.index_finding(record_id)
            elif index is IndexName.RUNS:
                await indexer.index_run(record_id)
            else:
                await indexer.index_audit_record(record_id)
            count += 1
        offset += len(ids)
        if len(ids) < 1000:
            break
    return count
