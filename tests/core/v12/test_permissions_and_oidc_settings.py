"""v1.2 permission matrix and OIDC environment parsing tests."""

from dataclasses import dataclass

import pytest

from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.oidc_settings import OidcSettings, load_oidc_settings
from telescreen.core.permissions import (
    ALL_PERMISSIONS,
    INGEST_PERMISSIONS,
    MACHINE_PERMISSIONS,
    READ_PERMISSIONS,
    ROLE_PERMISSIONS,
    SCOPE_PERMISSIONS,
    WRITE_PERMISSIONS,
    Permission,
    effective_token_permissions,
    highest_role,
    parse_role,
    role_permissions,
    scope_permissions,
)
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope, ErrorCode, OidcProvisioning, Role


@dataclass(frozen=True)
class FakeEnv(EnvSource):
    """Read a small immutable environment mapping in settings tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return a configured value by key."""
        return next((value for name, value in self.values if name == key), None)


def _env(*values: tuple[str, str]) -> FakeEnv:
    """Prefix input names with the application environment namespace."""
    return FakeEnv(tuple((f"TELESCREEN_{name}", value) for name, value in values))


def _oidc_env(*values: tuple[str, str]) -> FakeEnv:
    """Prefix input names with the OIDC environment namespace."""
    return FakeEnv(tuple((f"TELESCREEN_OIDC_{name}", value) for name, value in values))


def test_scope_and_role_permission_sets_match_v1_contract() -> None:
    """Scope and role grants preserve v1 behavior and intended exclusions."""
    assert scope_permissions(frozenset((ApiScope.READ,))) == READ_PERMISSIONS
    assert Permission.ISSUES_READ in READ_PERMISSIONS
    assert Permission.ISSUES_WRITE not in READ_PERMISSIONS
    assert Permission.USERS_READ not in READ_PERMISSIONS
    assert Permission.TOKENS_READ not in READ_PERMISSIONS
    assert scope_permissions(frozenset((ApiScope.ADMIN,))) == ALL_PERMISSIONS
    assert scope_permissions(frozenset((ApiScope.INGEST,))) == INGEST_PERMISSIONS
    assert frozenset((Permission.INGEST_PUSH, Permission.AUTH_SELF)) == INGEST_PERMISSIONS
    assert role_permissions(Role.VIEWER) == READ_PERMISSIONS
    assert role_permissions(Role.RESPONDER) == WRITE_PERMISSIONS
    assert role_permissions(Role.ADMIN) == ALL_PERMISSIONS - MACHINE_PERMISSIONS
    assert (
        next(grants for scope, grants in SCOPE_PERMISSIONS if scope is ApiScope.WRITE)
        == WRITE_PERMISSIONS
    )
    assert next(grants for role, grants in ROLE_PERMISSIONS if role is Role.ADMIN) == (
        ALL_PERMISSIONS - MACHINE_PERMISSIONS
    )


def test_effective_permissions_preserve_machine_ingest_and_bound_intersection() -> None:
    """Bound tokens intersect with member role while machine grants remain available."""
    assert (
        effective_token_permissions(frozenset((ApiScope.WRITE,)), Role.VIEWER, True)
        == READ_PERMISSIONS
    )
    assert (
        effective_token_permissions(frozenset((ApiScope.ADMIN,)), Role.RESPONDER, True)
        == WRITE_PERMISSIONS | MACHINE_PERMISSIONS
    )
    assert effective_token_permissions(frozenset((ApiScope.INGEST,)), None, True) == frozenset(
        (Permission.INGEST_PUSH,)
    )
    assert effective_token_permissions(frozenset((ApiScope.READ,)), None, False) == READ_PERMISSIONS
    assert highest_role((Role.VIEWER, Role.ADMIN)) is Role.ADMIN
    assert highest_role(()) is None
    assert parse_role("none") is None
    assert parse_role("") is None
    assert parse_role("viewer") is Role.VIEWER
    with pytest.raises(TelescreenError) as error:
        parse_role("superuser")
    assert error.value.code is ErrorCode.VALIDATION_ERROR


def test_oidc_settings_defaults_and_optional_disable() -> None:
    """OIDC stays disabled without an issuer and loads design defaults when enabled."""
    assert load_oidc_settings(FakeEnv()) is None
    settings: OidcSettings | None = load_oidc_settings(
        _oidc_env(("ISSUER", "http://127.0.0.1:8080/realms/telescreen"))
    )
    assert settings is not None
    assert settings.client_id == "telescreen-ui"
    assert settings.audience == settings.client_id
    assert settings.scopes == ("openid", "profile", "email")
    assert settings.algorithms == ("RS256",)
    assert settings.leeway_seconds == 60
    assert settings.jwks_cache_seconds == 300
    assert settings.role_claim == "realm_access.roles"
    assert len(settings.role_map) == 3
    assert settings.default_role is None
    assert settings.group_claim == "groups"
    assert settings.team_map == ()
    assert settings.provisioning is OidcProvisioning.LINK


def test_oidc_settings_parse_maps_and_overrides() -> None:
    """OIDC maps, role, and provisioning overrides parse into typed values."""
    settings: OidcSettings | None = load_oidc_settings(
        _oidc_env(
            ("ISSUER", "https://idp.example.test/realm"),
            ("TEAM_MAP", "security=secops,dev=dev"),
            ("DEFAULT_ROLE", "viewer"),
            ("PROVISIONING", "create"),
        )
    )
    assert settings is not None
    assert tuple((item.group, item.team_slug) for item in settings.team_map) == (
        ("security", "secops"),
        ("dev", "dev"),
    )
    assert settings.default_role is Role.VIEWER
    assert settings.provisioning is OidcProvisioning.CREATE


@pytest.mark.parametrize(
    "values",
    (
        (("ISSUER", "not-a-url"),),
        (("ISSUER", "https://idp.example.test"), ("ALGORITHMS", "none")),
        (("ISSUER", "https://idp.example.test"), ("ALGORITHMS", "HS256,RS256")),
        (("ISSUER", "https://idp.example.test"), ("LEEWAY_SECONDS", "301")),
        (("ISSUER", "https://idp.example.test"), ("ROLE_MAP", "x=superuser")),
        (("ISSUER", "https://idp.example.test"), ("TEAM_MAP", "security=")),
        (("ISSUER", "https://idp.example.test"), ("DEFAULT_ROLE", "superuser")),
    ),
)
def test_invalid_oidc_settings_fail_with_static_configuration_details(
    values: tuple[tuple[str, str], ...],
) -> None:
    """Invalid OIDC inputs fail without exposing configured values."""
    configured_value: str = values[-1][1]
    with pytest.raises(ConfigError) as error:
        load_oidc_settings(_oidc_env(*values))
    assert configured_value not in error.value.detail


def test_load_settings_exposes_oidc_and_static_token_login_defaults() -> None:
    """Application settings carry the OIDC contract and configurable token login."""
    defaults: TelescreenSettings = load_settings(FakeEnv())
    disabled: TelescreenSettings = load_settings(_env(("UI_TOKEN_LOGIN", "false")))
    enabled: TelescreenSettings = load_settings(_env(("OIDC_ISSUER", "https://idp.example.test")))
    assert defaults.oidc is None and defaults.ui_token_login is True
    assert disabled.ui_token_login is False
    assert enabled.oidc is not None and enabled.ui_token_login is True
