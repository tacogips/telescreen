"""Identity enrichment and reference validation use identity-owned transactions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.db.base import Base
from telescreen.db.models import DataFieldRow, MemberRow, TeamRow
from telescreen.db.session import create_sessionmaker
from telescreen.directory.actors import DirectoryActorDirectory
from telescreen.directory.inventory_context import IdentityActorLookup, IdentityAssigneeLookup
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.inventory.values import ValueInput, ValueIssue, ValueValidator
from telescreen.masking.redact import Redactor
from telescreen.ports.identity_context import ActorDisplay


@pytest.mark.asyncio
async def test_identity_reads_do_not_use_or_rollback_inventory_transaction(tmp_path: Path) -> None:
    """Real identity SQL works while uncommitted inventory data remains intact."""
    inventory: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'inventory.sqlite'}"
    )
    identity: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'identity.sqlite'}"
    )
    now: datetime = datetime(2026, 10, 14, tzinfo=UTC)
    try:
        async with inventory.begin() as connection:
            await connection.run_sync(Base.metadata.tables["data_fields"].create)
        async with identity.begin() as connection:
            for name in ("members", "teams", "api_tokens"):
                await connection.run_sync(Base.metadata.tables[name].create)
        async with create_sessionmaker(identity).begin() as session:
            session.add(
                MemberRow(
                    id="member-id",
                    handle="alice",
                    display_name="Alice",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                TeamRow(id="team-id", slug="ops", name="Operations", created_at=now, updated_at=now)
            )
        actors: IdentityActorLookup = IdentityActorLookup(
            create_sessionmaker(identity), DirectoryActorDirectory()
        )
        validator: ValueValidator = ValueValidator(
            IdentityAssigneeLookup(create_sessionmaker(identity), DirectoryRecipientResolver()),
            Redactor.default(),
        )
        field: DataFieldRow = DataFieldRow(
            id="owner-field",
            key="owner",
            label="Owner",
            field_type="reference",
            required=False,
            position=0,
            created_by="member:alice",
            created_at=now,
            updated_at=now,
        )
        async with create_sessionmaker(inventory).begin() as session:
            session.add(field)
            await session.flush()
            result: tuple[ActorDisplay, ...] = await actors.display(
                ("member:alice", "team:ops", "member:missing", "member:alice")
            )
            assert tuple(item.display_name for item in result) == (
                "Alice",
                "Operations",
                "Unknown",
                "Alice",
            )
            assert result[0].kind is ActorDisplayKind.MEMBER
            assert result[2].active is False
            assert await actors.display(()) == ()
            for ref in ('"member:alice"', '"team:ops"'):
                assert await validator.validate((field,), (ValueInput("owner", ref),)) == ()
            issues: tuple[ValueIssue, ...] = await validator.validate(
                (field,), (ValueInput("owner", '"member:missing"'),)
            )
            assert issues == (ValueIssue("owner", "unknown_reference"),)
            assert await session.scalar(select(func.count()).select_from(DataFieldRow)) == 1
        async with create_sessionmaker(inventory)() as session:
            assert await session.get(DataFieldRow, "owner-field") is not None
    finally:
        await inventory.dispose()
        await identity.dispose()
