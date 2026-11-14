"""Evidence query port."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class EvidenceQuery(Protocol):
    """Read accepted evidence counts for workflow gates."""

    async def accepted_count(self, session: AsyncSession, issue_id: str, step_run_id: str) -> int:
        """Count accepted evidence attached to an issue and step run."""
        ...
