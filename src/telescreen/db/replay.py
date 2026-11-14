"""Database-backed replay nonce storage."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import ReplayNonceRow


@dataclass(frozen=True)
class ReplayNonceStore:
    """Persist scoped nonces until their expiry time."""

    async def remember(
        self,
        session: AsyncSession,
        scope: str,
        nonce: str,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Store a new nonce, returning false when an unexpired one already exists."""
        await session.execute(
            delete(ReplayNonceRow).where(
                ReplayNonceRow.scope == scope,
                ReplayNonceRow.expires_at <= now,
            )
        )
        existing: ReplayNonceRow | None = await session.scalar(
            select(ReplayNonceRow).where(
                ReplayNonceRow.scope == scope,
                ReplayNonceRow.nonce == nonce,
            )
        )
        if existing is not None:
            return False
        try:
            async with session.begin_nested():
                session.add(ReplayNonceRow(scope=scope, nonce=nonce, expires_at=expires_at))
                await session.flush()
        except IntegrityError:
            return False
        return True
