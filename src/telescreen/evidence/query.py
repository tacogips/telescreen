"""Evidence query port implementation."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import EvidenceVerdict
from telescreen.db.models import EvidenceRow
from telescreen.ports.evidence import EvidenceQuery


@dataclass(frozen=True)
class EvidenceQueryImpl(EvidenceQuery):
    """Count accepted evidence rows scoped to an issue and step run."""

    async def accepted_count(self, session: AsyncSession, issue_id: str, step_run_id: str) -> int:
        """Count accepted evidence attached to both supplied identifiers."""
        count: int | None = await session.scalar(
            select(func.count(EvidenceRow.id)).where(
                EvidenceRow.issue_id == issue_id,
                EvidenceRow.step_run_id == step_run_id,
                EvidenceRow.verdict == EvidenceVerdict.ACCEPTED.value,
            )
        )
        return count or 0
