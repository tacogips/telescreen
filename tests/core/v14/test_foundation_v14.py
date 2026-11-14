"""v1.4 foundation vocabulary, permissions, configuration and error mapping."""

from dataclasses import dataclass

import pytest

from telescreen.api.common.errors import http_status_for
from telescreen.core.config import load_settings
from telescreen.core.errors import ConfigError
from telescreen.core.permissions import Permission, role_permissions
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    DataAssetKind,
    DataAssetOrigin,
    DataAssetState,
    DataAssetTab,
    DataEventKind,
    DataFieldRole,
    DataFieldType,
    DataItemKind,
    ErrorCode,
    InventoryMode,
    Role,
)


@dataclass(frozen=True)
class FakeEnv(EnvSource):
    """Provide configured values for settings tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return a configured environment value."""
        return next((value for name, value in self.values if key == name), None)


def _env(*values: tuple[str, str]) -> FakeEnv:
    """Prefix test values with the Telescreen environment namespace."""
    return FakeEnv(tuple((f"TELESCREEN_{name}", value) for name, value in values))


def test_inventory_vocabulary_values() -> None:
    """Expose the values fixed by the v1.4 design."""
    assert tuple(item.value for item in InventoryMode) == ("sensitive_only", "all", "off")
    assert tuple(item.value for item in DataAssetOrigin) == ("discovered", "registered")
    assert tuple(item.value for item in DataAssetState) == ("present", "deleted", "unknown")
    assert tuple(item.value for item in DataAssetKind) == (
        "file",
        "table",
        "list",
        "channel",
        "object",
        "folder",
        "other",
    )
    assert tuple(item.value for item in DataEventKind) == (
        "discovered",
        "registered",
        "items_changed",
        "values_changed",
        "deleted",
        "restored",
        "metadata_changed",
        "manual_change",
        "tracking_stopped",
        "tracking_resumed",
    )
    assert tuple(item.value for item in DataItemKind) == ("item", "value")
    assert tuple(item.value for item in DataFieldType) == (
        "text",
        "long_text",
        "integer",
        "decimal",
        "boolean",
        "date",
        "single_select",
        "multi_select",
        "reference",
        "url",
    )
    assert tuple(item.value for item in DataFieldRole) == ("purpose", "owner", "deletion_due")
    assert tuple(item.value for item in DataAssetTab) == (
        "all",
        "present",
        "deleted",
        "incomplete",
        "overdue",
    )


def test_roles_and_statuses_include_inventory_permissions() -> None:
    """Grant inventory permissions by role and map schema errors safely."""
    viewer: frozenset[Permission] = role_permissions(Role.VIEWER)
    responder: frozenset[Permission] = role_permissions(Role.RESPONDER)
    admin: frozenset[Permission] = role_permissions(Role.ADMIN)
    assert Permission.DATA_READ in viewer and Permission.DATA_WRITE not in viewer
    assert Permission.DATA_READ in responder and Permission.DATA_WRITE in responder
    assert Permission.DATA_FIELDS_ADMIN not in responder
    assert {Permission.DATA_READ, Permission.DATA_WRITE, Permission.DATA_FIELDS_ADMIN} <= admin
    assert http_status_for(ErrorCode.FIELD_IN_USE) == 409
    assert http_status_for(ErrorCode.REQUIRED_FIELD_MISSING) == 422
    assert http_status_for(ErrorCode.INVALID_FIELD_VALUE) == 422


def test_data_overdue_cron_default_disable_and_validation() -> None:
    """Load the overdue schedule, allow disabling it, and reject invalid cron."""
    assert load_settings(FakeEnv()).data_overdue_cron == "0 9 * * *"
    assert load_settings(_env(("DATA_OVERDUE_CRON", ""))).data_overdue_cron is None
    with pytest.raises(ConfigError, match="TELESCREEN_DATA_OVERDUE_CRON is invalid"):
        load_settings(_env(("DATA_OVERDUE_CRON", "bad cron")))
