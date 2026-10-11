"""Claim extraction and claim-to-role mapping for verified access tokens."""

from dataclasses import dataclass, field

from telescreen.core.json import JsonKind, JsonValue
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.permissions import highest_role
from telescreen.core.vocabulary import Role


@dataclass(frozen=True)
class ClaimProfile:
    """Profile claims used for provisioning, with the email excluded from repr."""

    email: str | None = field(repr=False)
    email_verified: bool
    preferred_username: str | None
    name: str | None


def claim_strings(claims: JsonValue, dotted_path: str) -> tuple[str, ...]:
    """Return a claim at a dotted path as strings, or () unless string or string array."""
    current: JsonValue | None = claims
    for part in dotted_path.split("."):
        if current is None or current.kind is not JsonKind.OBJECT:
            return ()
        current = current.get(part)
    if current is None:
        return ()
    single: str | None = current.as_str()
    if single is not None:
        return (single,)
    items: tuple[JsonValue, ...] | None = current.as_list()
    if items is None:
        return ()
    values: list[str] = []
    for item in items:
        text: str | None = item.as_str()
        if text is None:
            return ()
        values.append(text)
    return tuple(values)


def map_role(claims: JsonValue, settings: OidcSettings) -> Role | None:
    """Map configured role-claim values to the highest role, else the default role."""
    values: tuple[str, ...] = claim_strings(claims, settings.role_claim)
    mapped: list[Role] = [
        mapping.role for mapping in settings.role_map if mapping.claim_value in values
    ]
    role: Role | None = highest_role(mapped)
    return role if role is not None else settings.default_role


def _string_claim(claims: JsonValue, name: str) -> str | None:
    """Return a top-level non-empty string claim."""
    value: JsonValue | None = claims.get(name)
    text: str | None = value.as_str() if value is not None else None
    return text if text else None


def profile(claims: JsonValue) -> ClaimProfile:
    """Return email, verified flag, preferred username and display name claims."""
    verified: JsonValue | None = claims.get("email_verified")
    return ClaimProfile(
        email=_string_claim(claims, "email"),
        email_verified=verified is not None and verified.as_bool() is True,
        preferred_username=_string_claim(claims, "preferred_username"),
        name=_string_claim(claims, "name"),
    )
