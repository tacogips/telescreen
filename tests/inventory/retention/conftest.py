"""Disjoint inventory and notification stores around real metadata and delivery logic."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null
from telescreen.core.vocabulary import DataFieldRole, DlpPolicy, EngineTaskKind
from telescreen.db.base import Base
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow, MemberRow, TeamRow
from telescreen.db.session import create_sessionmaker
from telescreen.detection import RuleDetector
from telescreen.directory.inventory_context import IdentityAssigneeLookup
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.inventory.audit import InventoryAuditPublisher
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.retention import DataOverdueHandler
from telescreen.inventory.retention_outbox import InventoryRetentionLookup, RetentionOutbox
from telescreen.inventory.values import ValueInput, ValueService, ValueValidator
from telescreen.masking.redact import Redactor
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.retention import RetentionNotificationReceiver
from telescreen.notifications.sender import NotificationService
from telescreen.ports.retention import RetentionCommand
from telescreen.ports.workflow import ClaimedTask
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.handoff.conftest import NOW
from tests.inventory.retention.helpers import FakeOwners, _asset, _field


@dataclass(frozen=True)
class RetentionWorld:
    """The inventory login needs no identity, notification, audit chain or workflow tables."""

    inventory: async_sessionmaker[AsyncSession]
    main: async_sessionmaker[AsyncSession]
    clock: FakeClock
    publisher: DataOverdueHandler
    receiver: RetentionNotificationReceiver
    outbox: RetentionOutbox
    values: ValueService
    owners: FakeOwners

    async def seed(
        self,
        asset_id: str = "asset",
        number: int = 1,
        due: str = "2026-10-12",
        owner: str | None = None,
    ) -> None:
        """Create only inventory rows, with an optional registered master owner."""
        async with self.inventory.begin() as session:
            session.add(_asset(asset_id, number, self.clock.now()))
            session.add(
                DataFieldValueRow(
                    asset_id=asset_id,
                    field_id="due-field",
                    value_json=f'"{due}"',
                    value_date=due,
                    updated_by="member:admin",
                    updated_at=self.clock.now(),
                )
            )
            if owner:
                session.add(
                    DataFieldValueRow(
                        asset_id=asset_id,
                        field_id="owner-field",
                        value_json=f'"{owner}"',
                        updated_by="member:admin",
                        updated_at=self.clock.now(),
                    )
                )

    async def publish(self) -> tuple[RetentionCommand, ...]:
        """Run the real scheduled handler and detach pending obligations."""
        await self.publisher.handle(
            ClaimedTask(
                "check", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due-check"
            )
        )
        async with self.inventory() as session:
            identifiers: tuple[str, ...] = await self.outbox.pending_ids(session, 1000)
            commands: list[RetentionCommand] = []
            for event_id in identifiers:
                command: RetentionCommand | None = await self.outbox.get_pending(session, event_id)
                assert command is not None
                commands.append(command)
        return tuple(commands)

    async def change_due(self, due: str | None, asset_id: str = "asset") -> None:
        """Use real metadata edits, including revision rotation and marker clearing."""
        async with self.inventory.begin() as session:
            asset: DataAssetRow | None = await session.get(
                DataAssetRow, asset_id, with_for_update=True
            )
            assert asset is not None
            fields: tuple[DataFieldRow, ...] = tuple(
                (await session.scalars(select(DataFieldRow))).all()
            )
            await self.values.put(
                session,
                asset,
                fields,
                (ValueInput("deletion_due", f'"{due}"' if due is not None else None),),
                "member:admin",
            )


@pytest_asyncio.fixture
async def retention_world(tmp_path: Path) -> AsyncIterator[RetentionWorld]:
    """Install each owner's tables in a separate file and wire production services."""
    inventory: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    main: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'notifications.db'}")
    try:
        async with inventory.begin() as connection:
            for name in (
                "data_assets",
                "data_fields",
                "data_field_values",
                "data_asset_events",
                "inventory_retention_outbox",
                "inventory_audit_outbox",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        async with main.begin() as connection:
            for name in (
                "members",
                "teams",
                "team_memberships",
                "notification_settings",
                "notifications",
                "audit_records",
                "audit_chain_head",
                "retention_notification_receipts",
                "engine_tasks",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        inv_sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(inventory)
        main_sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(main)
        yield await make_retention_world(inv_sessions, main_sessions, FakeClock(NOW))
    finally:
        await inventory.dispose()
        await main.dispose()


async def make_retention_world(
    inv_sessions: async_sessionmaker[AsyncSession],
    main_sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> RetentionWorld:
    """Assemble real services and seed owner-local metadata and directory rows."""
    redactor: Redactor = Redactor.default()
    audit: AuditLogService = AuditLogService(clock, redactor)
    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    sender: NotificationService = NotificationService(
        DlpGate(RuleDetector(), redactor, DlpPolicy.BLOCK),
        resolver,
        SqlTaskQueue(clock),
        audit,
        clock,
        NotificationRepository(),
    )
    owners: FakeOwners = FakeOwners("member:alice")
    async with main_sessions.begin() as session:
        session.add(
            MemberRow(
                id="member-id",
                handle="alice",
                display_name="Alice",
                email="alice@example.invalid",
                slack_user_id="U123456789",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            TeamRow(
                id="team-id",
                slug="ops",
                name="Operations",
                email="ops@example.invalid",
                slack_channel="C123456789",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    async with inv_sessions.begin() as session:
        session.add(_field("due-field", "deletion_due", DataFieldRole.DELETION_DUE, "date"))
        session.add(_field("owner-field", "owner", DataFieldRole.OWNER, "reference"))
    return RetentionWorld(
        inv_sessions,
        main_sessions,
        clock,
        DataOverdueHandler(inv_sessions, redactor, clock, "https://telescreen.example"),
        RetentionNotificationReceiver(
            main_sessions,
            InventoryRetentionLookup(inv_sessions, clock),
            sender,
            sender,
            resolver,
            owners,
            audit,
            clock,
        ),
        RetentionOutbox(),
        ValueService(
            InventoryRepository(),
            ValueValidator(IdentityAssigneeLookup(main_sessions, resolver), redactor),
            InventoryAuditPublisher(clock, redactor),
            clock,
        ),
        owners,
    )
