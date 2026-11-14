"""PostgreSQL handoff fixture with independently owned schemas and login roles."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.core.clock import FakeClock
from telescreen.core.ids import new_id
from telescreen.db.base import Base
from telescreen.db.models import CollectorConfigRow
from telescreen.db.session import create_sessionmaker
from telescreen.integrations.inventory_dispatch import InventoryDispatcher
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.inventory.handoff.apply import InventoryOperationApplier
from telescreen.inventory.handoff.inbox import InventoryInbox
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.repository import InventoryRepository
from tests.inventory.handoff.conftest import NOW, HandoffWorld


@dataclass(frozen=True)
class PgHandoff:
    """A real handoff plus administrator-only observation of database lock waits."""

    world: HandoffWorld
    admin: AsyncEngine
    producer_schema: str
    inventory_schema: str


@pytest_asyncio.fixture
async def pg_handoff() -> AsyncIterator[PgHandoff]:
    """Isolate every test; neither module's login can access the other schema."""
    configured: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if configured is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL with PostgreSQL role creation")
    url: URL = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.skip("requires PostgreSQL")
    admin: AsyncEngine = create_async_engine(url)
    async with admin.connect() as connection:
        capable: bool = bool(
            await connection.scalar(
                text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
            )
        )
    if not capable:
        await admin.dispose()
        pytest.skip("PostgreSQL ownership test requires permission to create isolated test roles")
    suffix: str = new_id().replace("-", "")[:16]
    password: str = new_id().replace("-", "")
    producer_schema: str = f"ts_prod_{suffix}"
    inventory_schema: str = f"ts_inv_{suffix}"
    producer_engine: AsyncEngine = create_async_engine(
        url.set(username=producer_schema, password=password),
        connect_args={"server_settings": {"search_path": producer_schema}},
    )
    inventory_engine: AsyncEngine = create_async_engine(
        url.set(username=inventory_schema, password=password),
        connect_args={"server_settings": {"search_path": inventory_schema}},
    )
    try:
        async with admin.begin() as connection:
            for schema in (producer_schema, inventory_schema):
                await connection.execute(
                    text(f"CREATE ROLE \"{schema}\" LOGIN PASSWORD '{password}'")
                )
                await connection.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{schema}"'))
        async with producer_engine.begin() as connection:
            for name in (
                "inventory_outbox_streams",
                "inventory_outbox",
                "issues",
                "collector_configs",
                "engine_tasks",
                "audit_chain_head",
                "audit_records",
                "audit_receipts",
                "retention_notification_receipts",
                "members",
                "teams",
                "team_memberships",
                "notifications",
                "notification_settings",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        async with inventory_engine.begin() as connection:
            for name in (
                "inventory_audit_outbox",
                "inventory_retention_outbox",
                "inventory_inbox_streams",
                "inventory_inbox",
                "inventory_counters",
                "data_assets",
                "data_asset_events",
                "data_asset_items",
                "data_fields",
                "data_field_values",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        producer: async_sessionmaker[AsyncSession] = create_sessionmaker(producer_engine)
        inventory: async_sessionmaker[AsyncSession] = create_sessionmaker(inventory_engine)
        async with producer.begin() as session:
            session.add(
                CollectorConfigRow(
                    id="config-id",
                    name="input",
                    kind="custom",
                    trigger_mode="event",
                    selector_json="{}",
                    created_at=NOW,
                    updated_at=NOW,
                    inventory_mode="all",
                )
            )
        clock: FakeClock = FakeClock(NOW)
        repository: InventoryRepository = InventoryRepository()
        applier: InventoryOperationApplier = InventoryOperationApplier(
            InventoryObserverImpl(repository),
            InventoryReconcilerImpl(repository, clock),
        )
        outbox: InventoryOutbox = InventoryOutbox()
        inbox: InventoryInbox = InventoryInbox(inventory, applier, clock)
        world: HandoffWorld = HandoffWorld(
            producer,
            inventory,
            clock,
            outbox,
            applier,
            inbox,
            InventoryDispatcher(producer, outbox, inbox, clock),
        )
        yield PgHandoff(world, admin, producer_schema, inventory_schema)
    finally:
        await producer_engine.dispose()
        await inventory_engine.dispose()
        async with admin.begin() as connection:
            for schema in (producer_schema, inventory_schema):
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{schema}"'))
        await admin.dispose()
