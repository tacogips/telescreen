"""Post-commit guarded indexing for masked database records."""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind
from telescreen.db.models import (
    AuditRecordRow,
    FindingRow,
    IssueRow,
    MemberRow,
    TeamRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.index_guard import GuardVerdict, IndexGuard
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.search import SearchIndexer
from telescreen.search.backend import IndexName, SearchBackend, SearchDocument
from telescreen.search.documents import (
    audit_document,
    finding_document,
    issue_document,
    run_document,
)

logger: logging.Logger = logging.getLogger(__name__)


def _guard_fields(document: SearchDocument) -> tuple[tuple[str, str], ...]:
    """Flatten every string field and string tuple element for guard inspection."""
    values: list[tuple[str, str]] = []
    for name, value in document.fields:
        if isinstance(value, str):
            values.append((name, value))
        elif isinstance(value, tuple):
            values.extend((name, item) for item in value)
    return tuple(values)


@dataclass(frozen=True)
class DocumentIndexer(SearchIndexer):
    """Build and guard documents before submitting them to the search backend."""

    sessions: async_sessionmaker[AsyncSession]
    backend: SearchBackend
    guard: IndexGuard
    audit: AuditLog
    clock: Clock

    async def _rejected(
        self, index: IndexName, document: SearchDocument, verdict: GuardVerdict
    ) -> None:
        """Audit a rejected document using metadata only."""
        async with self.sessions.begin() as session:
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:indexer",
                    "index_guard.rejected",
                    "search_document",
                    document.id,
                    "Search document rejected by sensitive-data guard",
                    json_object(
                        (
                            ("index", json_str(index.value)),
                            ("document_id", json_str(document.id)),
                            ("field_names", json_str(",".join(verdict.field_names))),
                            ("rule_ids", json_str(",".join(verdict.rule_ids))),
                        )
                    ),
                ),
            )

    async def _submit(self, index: IndexName, document: SearchDocument) -> None:
        """Guard one document and safely absorb backend or database failures."""
        try:
            verdict: GuardVerdict = self.guard.check(_guard_fields(document))
            if not verdict.accepted:
                await self._rejected(index, document, verdict)
                logger.warning(
                    "index document %s rejected",
                    document.id,
                    extra={"index": index.value, "document_id": document.id},
                )
                return
            await self.backend.upsert(index, (document,))
        except Exception:
            logger.warning(
                "index failed for document %s",
                document.id,
                extra={"index": index.value, "document_id": document.id},
            )

    async def index_issue(self, issue_id: str) -> None:
        """Index one issue after its transaction commits."""
        async with self.sessions() as session:
            row: IssueRow | None = await session.get(IssueRow, issue_id)
            if row is None:
                return
            assignee: str | None = None
            if row.assignee_member_id is not None:
                member: MemberRow | None = await session.get(MemberRow, row.assignee_member_id)
                assignee = member.id if member is not None else None
            elif row.assignee_team_id is not None:
                team: TeamRow | None = await session.get(TeamRow, row.assignee_team_id)
                assignee = team.id if team is not None else None
            document: SearchDocument = issue_document(row, assignee)
        await self._submit(IndexName.ISSUES, document)

    async def index_finding(self, finding_id: str) -> None:
        """Index one finding after its transaction commits."""
        async with self.sessions() as session:
            row: FindingRow | None = await session.get(FindingRow, finding_id)
            if row is None:
                return
            document: SearchDocument = finding_document(row)
        await self._submit(IndexName.FINDINGS, document)

    async def index_run(self, run_id: str) -> None:
        """Index one workflow run after its transaction commits."""
        async with self.sessions() as session:
            row: WorkflowRunRow | None = await session.get(WorkflowRunRow, run_id)
            if row is None:
                return
            definition: WorkflowDefinitionRow | None = await session.get(
                WorkflowDefinitionRow, row.definition_id
            )
            document: SearchDocument = run_document(row, definition)
        await self._submit(IndexName.RUNS, document)

    async def index_audit_record(self, record_id: str) -> None:
        """Load and index one persisted audit record through the guard."""
        async with self.sessions() as session:
            row: AuditRecordRow | None = await session.scalar(
                select(AuditRecordRow).where(AuditRecordRow.id == record_id)
            )
            if row is None:
                return
            document: SearchDocument = audit_document(row)
        await self._submit(IndexName.AUDIT, document)
