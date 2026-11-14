"""Typed and validated OpenID Connect configuration."""

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import OidcProvisioning, Role

_ALLOWED_ALGORITHMS: frozenset[str] = frozenset(
    ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
)
_DEFAULT_ROLE_MAP: tuple[tuple[str, Role], ...] = (
    ("telescreen-admin", Role.ADMIN),
    ("telescreen-responder", Role.RESPONDER),
    ("telescreen-viewer", Role.VIEWER),
)


@dataclass(frozen=True)
class OidcRoleMapping:
    """Map one IdP claim value to a Telescreen role."""

    claim_value: str
    role: Role


@dataclass(frozen=True)
class OidcTeamMapping:
    """Map one IdP group value to an existing team slug."""

    group: str
    team_slug: str


@dataclass(frozen=True)
class OidcSettings:
    """Validated settings used to verify and map OIDC access tokens."""

    issuer: str
    client_id: str
    audience: str
    scopes: tuple[str, ...]
    algorithms: tuple[str, ...]
    leeway_seconds: int
    jwks_cache_seconds: int
    role_claim: str
    role_map: tuple[OidcRoleMapping, ...]
    default_role: Role | None
    group_claim: str
    team_map: tuple[OidcTeamMapping, ...]
    provisioning: OidcProvisioning


def _setting(env: EnvSource, name: str) -> str | None:
    """Read an OIDC environment variable without echoing its value."""
    return env.get(f"TELESCREEN_OIDC_{name}")


def _integer(env: EnvSource, name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an OIDC integer setting and enforce its allowed range."""
    raw: str | None = _setting(env, name)
    if raw is None:
        return default
    try:
        value: int = int(raw)
    except ValueError:
        raise ConfigError(f"TELESCREEN_OIDC_{name} must be an integer") from None
    if value < minimum or value > maximum:
        raise ConfigError(f"TELESCREEN_OIDC_{name} is outside the allowed range")
    return value


def _csv(env: EnvSource, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Read an OIDC comma-separated list, rejecting empty entries and duplicates."""
    raw: str | None = _setting(env, name)
    if raw is None:
        return default
    values: tuple[str, ...] = tuple(part.strip() for part in raw.split(","))
    if not values or any(not value for value in values) or len(set(values)) != len(values):
        raise ConfigError(f"TELESCREEN_OIDC_{name} is invalid")
    return values


def _role_map(env: EnvSource) -> tuple[OidcRoleMapping, ...]:
    """Read claim-to-role mappings with static errors for malformed values."""
    raw: str | None = _setting(env, "ROLE_MAP")
    if raw is None:
        return tuple(OidcRoleMapping(value, role) for value, role in _DEFAULT_ROLE_MAP)
    mappings: list[OidcRoleMapping] = []
    seen: set[str] = set()
    for entry in raw.split(","):
        claim_value: str
        separator: str
        role_text: str
        claim_value, separator, role_text = entry.partition("=")
        claim_value = claim_value.strip()
        role_text = role_text.strip()
        if not separator or not claim_value or not role_text or "=" in role_text:
            raise ConfigError("TELESCREEN_OIDC_ROLE_MAP is invalid")
        if claim_value in seen:
            raise ConfigError("TELESCREEN_OIDC_ROLE_MAP is invalid")
        try:
            role: Role = Role(role_text)
        except ValueError:
            raise ConfigError("TELESCREEN_OIDC_ROLE_MAP contains an unknown role") from None
        seen.add(claim_value)
        mappings.append(OidcRoleMapping(claim_value, role))
    return tuple(mappings)


def _team_map(env: EnvSource) -> tuple[OidcTeamMapping, ...]:
    """Read group-to-team mappings, allowing an empty setting to disable sync."""
    raw: str | None = _setting(env, "TEAM_MAP")
    if raw is None or not raw.strip():
        return ()
    mappings: list[OidcTeamMapping] = []
    seen: set[str] = set()
    for entry in raw.split(","):
        group: str
        separator: str
        team_slug: str
        group, separator, team_slug = entry.partition("=")
        group = group.strip()
        team_slug = team_slug.strip()
        if not separator or not group or not team_slug or "=" in team_slug or group in seen:
            raise ConfigError("TELESCREEN_OIDC_TEAM_MAP is invalid")
        seen.add(group)
        mappings.append(OidcTeamMapping(group, team_slug))
    return tuple(mappings)


def load_oidc_settings(env: EnvSource) -> OidcSettings | None:
    """Load OIDC settings, disabling OIDC when no issuer is configured."""
    issuer: str | None = _setting(env, "ISSUER")
    if issuer is None or not issuer.strip():
        return None
    parsed_issuer: SplitResult = urlsplit(issuer)
    if parsed_issuer.scheme not in ("http", "https") or not parsed_issuer.hostname:
        raise ConfigError("TELESCREEN_OIDC_ISSUER is invalid")

    client_id: str = _setting(env, "CLIENT_ID") or "telescreen-ui"
    audience: str = _setting(env, "AUDIENCE") or client_id
    scopes_raw: str = _setting(env, "SCOPES") or "openid profile email"
    scopes: tuple[str, ...] = tuple(scopes_raw.split())
    if not scopes:
        raise ConfigError("TELESCREEN_OIDC_SCOPES is invalid")

    algorithms: tuple[str, ...] = _csv(env, "ALGORITHMS", ("RS256",))
    if any(algorithm not in _ALLOWED_ALGORITHMS for algorithm in algorithms):
        raise ConfigError("TELESCREEN_OIDC_ALGORITHMS contains an unsupported algorithm")

    leeway_seconds: int = _integer(env, "LEEWAY_SECONDS", 60, 0, 300)
    jwks_cache_seconds: int = _integer(env, "JWKS_CACHE_SECONDS", 300, 1, 86400)
    default_role_text: str | None = _setting(env, "DEFAULT_ROLE")
    default_role: Role | None = None
    if default_role_text is not None and default_role_text.strip():
        try:
            default_role = Role(default_role_text.strip())
        except ValueError:
            raise ConfigError("TELESCREEN_OIDC_DEFAULT_ROLE is invalid") from None
    provisioning_text: str = _setting(env, "PROVISIONING") or "link"
    try:
        provisioning: OidcProvisioning = OidcProvisioning(provisioning_text)
    except ValueError:
        raise ConfigError("TELESCREEN_OIDC_PROVISIONING is invalid") from None

    return OidcSettings(
        issuer=issuer,
        client_id=client_id,
        audience=audience,
        scopes=scopes,
        algorithms=algorithms,
        leeway_seconds=leeway_seconds,
        jwks_cache_seconds=jwks_cache_seconds,
        role_claim=_setting(env, "ROLE_CLAIM") or "realm_access.roles",
        role_map=_role_map(env),
        default_role=default_role,
        group_claim=_setting(env, "GROUP_CLAIM") or "groups",
        team_map=_team_map(env),
        provisioning=provisioning,
    )
