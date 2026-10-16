"""Inventory replica, configuration, and import-boundary regressions."""

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from telescreen.api.common.auth import hash_token
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope
from telescreen.db.models.inventory_links import InventoryIssueLinkRow
from telescreen.inventory_service.app import build_inventory_runtime
from telescreen.inventory_service.lookups import ReplicaIssueLookup
from telescreen.ports.asset_context import ResourceIssueRef
from tests.e2e.harness import PairEnv
from tests.inventory_service.conftest import InventoryWorld


async def test_issue_links_are_served_from_inventory_projection(
    inventory_world: InventoryWorld,
) -> None:
    """Open issue enrichment reads only the inventory-owned issue-link projection."""
    world: InventoryWorld = inventory_world
    async with world.harness.sessions.begin() as session:
        session.add(
            InventoryIssueLinkRow(
                issue_id="issue-1",
                resource_key="resource-1",
                short_id="ISSUE-1",
                status="unresolved",
                owner_ref=None,
            )
        )
    lookup: ReplicaIssueLookup = ReplicaIssueLookup(world.harness.sessions)
    result: tuple[ResourceIssueRef, ...] = await lookup.open_issues_for_resources(("resource-1",))
    assert result == (ResourceIssueRef("resource-1", "issue-1", "ISSUE-1"),)


def test_inventory_config_requires_notification_endpoint_and_token() -> None:
    """Missing Notification settings fail before any database or network resource opens."""
    settings: TelescreenSettings = TelescreenSettings(
        inventory_database_url="sqlite+aiosqlite:///:memory:",
        inventory_service_token_ref="env:INVENTORY",
        notification_inventory_token_ref="env:NOTIFICATION",
        fingerprint_key_ref="env:FINGERPRINT",
    )
    env: EnvSource = PairEnv(
        (
            ("INVENTORY", "inventory-token"),
            ("NOTIFICATION", "notification-token"),
            ("FINGERPRINT", "f" * 32),
        )
    )
    with pytest.raises(ConfigError):
        build_inventory_runtime(settings, env, httpx.AsyncClient())


def test_inventory_config_requires_notification_inventory_token() -> None:
    """Notification delivery cannot be configured without its inventory credential."""
    settings: TelescreenSettings = TelescreenSettings(
        inventory_database_url="sqlite+aiosqlite:///:memory:",
        inventory_service_token_ref="env:INVENTORY",
        notification_service_url="http://notification",
        fingerprint_key_ref="env:FINGERPRINT",
    )
    env: EnvSource = PairEnv((("INVENTORY", "inventory-token"), ("FINGERPRINT", "f" * 32)))
    with pytest.raises(ConfigError):
        build_inventory_runtime(settings, env, httpx.AsyncClient())


def test_inventory_cli_and_app_import_without_telescreen_composition() -> None:
    """The process entry points do not load Telescreen wiring or domain owners."""
    script: str = (
        "import importlib, sys; "
        "importlib.import_module('telescreen.inventory_service.cli'); "
        "importlib.import_module('telescreen.inventory_service.app'); "
        "forbidden=('telescreen.wiring','telescreen.inventory_service.'"
        "'wiring','telescreen.inventory_service.bureau',"
        "'telescreen.directory','telescreen.issues',"
        "'telescreen.notifications'); "
        "assert not any(name == prefix or name.startswith(prefix + '.') "
        "for name in sys.modules for prefix in forbidden)"
    )
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr.decode()


async def test_revoked_replica_token_is_rejected(inventory_world: InventoryWorld) -> None:
    """A token revoked in the local replica is rejected without a Telescreen lookup."""
    world: InventoryWorld = inventory_world
    from telescreen.ports.directory_projection import (
        ApiTokenEntity,
        DirectoryEntity,
        DirectoryEntityKind,
    )

    revoked: ApiTokenEntity = ApiTokenEntity(
        "revoked-token",
        "revoked",
        hash_token("revoked-user-token"),
        (ApiScope.READ.value,),
        None,
        True,
    )
    async with world.harness.sessions.begin() as session:
        await world.runtime.replica.apply(
            session,
            DirectoryEntity(DirectoryEntityKind.API_TOKEN, revoked.id, 1, True, revoked),
        )
    response: httpx.Response = await world.client.get(
        "/api/data/assets",
        headers=(
            ("authorization", "Bearer revoked-user-token"),
            ("x-telescreen-service-token", "inventory-service-secret"),
        ),
    )
    assert response.status_code == 401


def test_no_source_file_has_accidental_telescreen_imports() -> None:
    """The scoped package keeps dependency direction within the accepted plan gate."""
    path: Path = Path("src/telescreen/inventory_service/app.py")
    content: str = path.read_text()
    assert "telescreen.wiring" not in content
    assert "telescreen.directory" not in content
    assert "telescreen.issues" not in content
    assert "telescreen.notifications" not in content
