"""SQLite database harness for repository and service tests."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from telescreen.db.base import Base
from telescreen.db.models import AuditChainHeadRow, CounterRow
from telescreen.db.session import create_sessionmaker


@dataclass
class SqliteHarness:
    """An in-memory SQLite engine, session factory and cleanup operation."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def close(self) -> None:
        """Dispose of the harness engine."""
        await self.engine.dispose()


async def make_sqlite_harness() -> SqliteHarness:
    """Create all tables and seed the issue counter and audit chain genesis."""
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions.begin() as session:
        session.add(CounterRow(name="issue_number", value=0))
        session.add(AuditChainHeadRow(id=1, seq=0, hash="0" * 64))
    return SqliteHarness(engine=engine, sessions=sessions)
