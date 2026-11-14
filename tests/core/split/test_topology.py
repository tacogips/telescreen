"""Tests for service topology configuration and notification settings."""

from dataclasses import dataclass

import pytest

from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError
from telescreen.core.topology import Topology, topology_of


@dataclass(frozen=True)
class FakeEnv:
    """Small environment source for topology setting tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return one configured environment value."""
        return next((value for name, value in self.values if name == key), None)


def test_topology_requires_paired_urls_and_split_tokens() -> None:
    """Select supported topologies and reject incomplete split configurations."""
    assert topology_of(TelescreenSettings()) is Topology.SINGLE_PROCESS
    with pytest.raises(ConfigError):
        topology_of(TelescreenSettings(inventory_service_url="http://inventory"))


def test_topology_rejects_single_service_url_and_missing_tokens() -> None:
    """Partial service configuration fails before resources are constructed."""
    with pytest.raises(ConfigError):
        topology_of(TelescreenSettings(inventory_service_url="http://inventory"))
    with pytest.raises(ConfigError):
        topology_of(
            TelescreenSettings(
                inventory_service_url="http://inventory",
                notification_service_url="http://notification",
            )
        )
    split: TelescreenSettings = TelescreenSettings(
        inventory_service_url="http://inventory",
        notification_service_url="http://notification",
        inventory_service_token_ref="env:INVENTORY_TOKEN",
        notification_service_token_ref="env:NOTIFICATION_TOKEN",
    )
    assert topology_of(split) is Topology.SPLIT


def test_notification_settings_and_single_process_database_constraint() -> None:
    """Load notification settings and reject a separate single-process store."""
    settings: TelescreenSettings = load_settings(
        FakeEnv((("TELESCREEN_NOTIFICATION_REPLICA_GRACE_SECONDS", "42"),))
    )
    assert settings.notification_replica_grace_seconds == 42
    assert load_settings(FakeEnv()).notification_replica_grace_seconds == 600
    with pytest.raises(ConfigError):
        load_settings(FakeEnv((("TELESCREEN_NOTIFICATION_REPLICA_GRACE_SECONDS", "0"),)))
    with pytest.raises(ConfigError):
        topology_of(
            TelescreenSettings(
                database_url="sqlite:///main.db",
                notification_database_url="sqlite:///notification.db",
            )
        )
