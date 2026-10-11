"""OIDC verification and member provisioning ports."""

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.oidc_settings import OidcTeamMapping
from telescreen.core.vocabulary import OidcProvisioning, Role, SignInProvisioning


@dataclass(frozen=True)
class VerifiedToken:
    """Verified external identity and claims, excluded from representations."""

    issuer: str
    subject: str = field(repr=False)
    claims_json: str = field(repr=False)


@dataclass(frozen=True)
class ProviderMetadata:
    """Public endpoints discovered from an OIDC provider."""

    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None


class BearerVerifier(Protocol):
    """Verify an IdP access token and expose safe public provider metadata."""

    async def verify(self, token: str) -> VerifiedToken:
        """Verify a bearer token or raise invalid_token / idp_unavailable."""
        ...

    async def metadata(self) -> ProviderMetadata:
        """Return discovered authorization and logout endpoints."""
        ...


@dataclass(frozen=True)
class MemberAuth:
    """Safe member identity and authorization state for sign-in."""

    member_id: str
    handle: str
    display_name: str
    role: Role | None
    active: bool


@dataclass(frozen=True)
class SignInRequest:
    """Mapped OIDC claims and provisioning policy for one sign-in."""

    issuer: str
    subject: str = field(repr=False)
    email: str | None = field(default=None, repr=False)
    email_verified: bool = False
    preferred_username: str | None = None
    display_name: str | None = None
    role: Role = Role.VIEWER
    groups: tuple[str, ...] = ()
    provisioning: OidcProvisioning = OidcProvisioning.LINK
    team_map: tuple[OidcTeamMapping, ...] = ()


@dataclass(frozen=True)
class SignInResult:
    """Member and provisioning outcome returned after sign-in."""

    member: MemberAuth
    provisioning: SignInProvisioning
    role_changed: bool


class MemberProvisioner(Protocol):
    """Resolve stable OIDC identities and provision members transactionally."""

    async def member_for_identity(
        self, session: AsyncSession, issuer: str, subject: str
    ) -> MemberAuth | None:
        """Return the member already linked to an issuer and subject."""
        ...

    async def sign_in(self, session: AsyncSession, request: SignInRequest) -> SignInResult:
        """Resolve, link or create a member according to the request policy."""
        ...
