"""Persistence adapter for latest integration checks."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.vocabulary import ErrorCode, IntegrationKind
from telescreen.db.models import IntegrationCheckRow


@dataclass(frozen=True)
class IntegrationCheck:
    """Safe latest-check state for one integration kind."""

    kind: IntegrationKind
    checked_at: datetime
    ok: bool
    error_code: ErrorCode | None


@dataclass(frozen=True)
class IntegrationCheckRepository:
    """Read and write the existing integration_checks table."""

    async def get_all(self, session: AsyncSession) -> tuple[IntegrationCheck, ...]:
        """Return all latest check rows in stable integration order."""
        rows: tuple[IntegrationCheckRow, ...] = tuple(
            (
                await session.scalars(
                    select(IntegrationCheckRow).order_by(IntegrationCheckRow.kind)
                )
            ).all()
        )
        return tuple(
            IntegrationCheck(
                IntegrationKind(row.kind),
                ensure_utc(row.checked_at),
                row.ok,
                ErrorCode(row.error_code) if row.error_code else None,
            )
            for row in rows
        )

    async def upsert(
        self,
        session: AsyncSession,
        kind: IntegrationKind,
        checked_at: datetime,
        ok: bool,
        error_code: ErrorCode | None,
    ) -> None:
        """Insert or replace one integration's latest check result."""
        row: IntegrationCheckRow | None = await session.get(IntegrationCheckRow, kind.value)
        if row is None:
            session.add(
                IntegrationCheckRow(
                    kind=kind.value,
                    checked_at=ensure_utc(checked_at),
                    ok=ok,
                    error_code=error_code.value if error_code else None,
                )
            )
            return
        row.checked_at = ensure_utc(checked_at)
        row.ok = ok
        row.error_code = error_code.value if error_code else None
