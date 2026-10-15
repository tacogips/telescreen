"""The HTTP service boundary works under real disjoint PostgreSQL login roles."""

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from telescreen.db.session import create_sessionmaker
from tests.inventory_service import test_runtime as flows
from tests.inventory_service.conftest import RemoteWorld, remote_services
from tests.support.db import SqliteHarness
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "flow",
    [
        flows.test_public_api_authentication_and_independent_ownership,
        flows.test_collection_outage_then_lost_reply_replays_once,
        flows.test_audit_and_retention_survive_callback_outage,
    ],
)
async def test_http_runtime_under_restricted_postgres_owners(
    tmp_path: Path,
    owned_postgres: OwnedPostgres,
    flow: Callable[[RemoteWorld], Awaitable[None]],
) -> None:
    """Use production configured pools and migrations; each login sees only its owner tables."""
    main: SqliteHarness = SqliteHarness(
        owned_postgres.main, create_sessionmaker(owned_postgres.main)
    )
    inventory: SqliteHarness = SqliteHarness(
        owned_postgres.inventory, create_sessionmaker(owned_postgres.inventory)
    )
    async with remote_services(
        tmp_path,
        main,
        inventory,
        owned_postgres.main_url,
        owned_postgres.inventory_url,
        configured=True,
    ) as world:
        await flow(world)
