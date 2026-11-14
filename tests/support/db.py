"""SQLite database harness for repository and service tests."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from telescreen.db.base import Base
from telescreen.db.models import AuditChainHeadRow, CounterRow, NotificationAuditChainHeadRow
from telescreen.db.ownership import Store, main_database_tables, tables_for
from telescreen.db.session import create_sessionmaker


@dataclass
class SqliteHarness:
    """A SQLite engine, session factory and cleanup operation."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def close(self) -> None:
        """Dispose of the harness engine."""
        await self.engine.dispose()


async def make_sqlite_harness(
    database_path: Path | None = None, *, store: Store | None = None
) -> SqliteHarness:
    """Create all tables and seed the issue counter and audit chain genesis."""
    engine: AsyncEngine = (
        create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        if database_path is None
        else create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    )
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[
                Base.metadata.tables[name]
                for name in (main_database_tables() if store is Store.MAIN else tables_for(store))
            ]
            if store is not None
            else None,
        )
    if store is Store.NOTIFICATION:
        async with sessions.begin() as session:
            session.add(NotificationAuditChainHeadRow(id=1, seq=0, hash="0" * 64))
    elif store is not Store.INVENTORY:
        async with sessions.begin() as session:
            session.add(CounterRow(name="issue_number", value=0))
            session.add(AuditChainHeadRow(id=1, seq=0, hash="0" * 64))
    return SqliteHarness(engine=engine, sessions=sessions)
