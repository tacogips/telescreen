"""The assembled API and worker use physically separate owner stores."""

from pathlib import Path

import pytest

from tests.e2e.harness import E2EWorld, OutboundRecorder, make_world
from tests.e2e.inventory_split_flow import assert_split_runtime
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_stores", [False, True], ids=["injected", "configured"])
async def test_api_auth_enrichment_inventory_delivery_audit_and_retention_use_disjoint_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_stores: bool
) -> None:
    """Exercise production composition with disjoint tables and independent transactions."""
    world: E2EWorld = await make_world(
        tmp_path,
        (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),),
        OutboundRecorder(),
        separate_inventory=True,
        configured_stores=configured_stores,
    )
    await assert_split_runtime(world, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_runtime_with_restricted_postgres_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_postgres: OwnedPostgres
) -> None:
    """Use migrated complete stores, configured URLs and separate restricted logins."""
    world: E2EWorld = await make_world(
        tmp_path,
        (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),),
        OutboundRecorder(),
        store_urls=(owned_postgres.main_url, owned_postgres.inventory_url),
    )
    await assert_split_runtime(world, monkeypatch)
