"""Pure service topology selection from validated settings."""

from enum import StrEnum

from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError


class Topology(StrEnum):
    """Supported runtime compositions."""

    SINGLE_PROCESS = "single_process"
    SPLIT = "split"


def topology_of(settings: TelescreenSettings) -> Topology:
    """Choose single-process or full-split mode and reject partial configurations."""
    inventory_url: bool = settings.inventory_service_url is not None
    notification_url: bool = settings.notification_service_url is not None
    if inventory_url != notification_url:
        raise ConfigError("inventory and notification service URLs must be configured together")
    if not inventory_url:
        if (
            settings.notification_database_url is not None
            and settings.notification_database_url != settings.database_url
        ):
            raise ConfigError(
                "notification database URL must match main database in single-process mode"
            )
        return Topology.SINGLE_PROCESS
    if settings.inventory_service_token_ref is None:
        raise ConfigError("inventory service token is required in split mode")
    if settings.notification_service_token_ref is None:
        raise ConfigError("notification service token is required in split mode")
    return Topology.SPLIT
