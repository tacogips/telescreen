"""OIDC sign-in orchestration: verify, map role, provision and audit."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_object, json_str, parse_json
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.vocabulary import ActorKind, ErrorCode, IdentityKind, Role
from telescreen.identity.claims import ClaimProfile, claim_strings, map_role, profile
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import OwnerIdentity
from telescreen.ports.identity import (
    BearerVerifier,
    MemberProvisioner,
    SignInRequest,
    SignInResult,
    VerifiedToken,
)

KEY_PREFIX_LENGTH: int = 12
_AUDITED_DENIALS: frozenset[ErrorCode] = frozenset(
    (ErrorCode.NOT_PROVISIONED, ErrorCode.IDENTITY_CONFLICT, ErrorCode.FORBIDDEN)
)


def identity_key_prefix(fingerprinter: Fingerprinter, issuer: str, subject: str) -> str:
    """Return the first 12 hex characters of the keyed OIDC subject identity."""
    owner_ref: str = fingerprinter.owner_ref(
        OwnerIdentity(IdentityKind.OIDC_SUBJECT, f"{issuer}\0{subject}")
    )
    return owner_ref.partition(":")[2][:KEY_PREFIX_LENGTH]


@dataclass(frozen=True)
class SignInService:
    """Turn a verified IdP access token into an audited member sign-in."""

    sessions: async_sessionmaker[AsyncSession] = field(repr=False)
    verifier: BearerVerifier = field(repr=False)
    provisioner: MemberProvisioner = field(repr=False)
    settings: OidcSettings = field(repr=False)
    audit: AuditLog = field(repr=False)
    fingerprinter: Fingerprinter = field(repr=False)

    async def sign_in(self, token: str) -> SignInResult:
        """Verify the token, deny without a mapped role, then provision and audit."""
        verified: VerifiedToken = await self.verifier.verify(token)
        claims: JsonValue = parse_json(verified.claims_json)
        key_prefix: str = identity_key_prefix(self.fingerprinter, verified.issuer, verified.subject)
        role: Role | None = map_role(claims, self.settings)
        if role is None:
            await self._audit_denial(ErrorCode.NO_ROLE, key_prefix)
            raise TelescreenError(ErrorCode.NO_ROLE, "no mapped role")
        claim_profile: ClaimProfile = profile(claims)
        request: SignInRequest = SignInRequest(
            issuer=verified.issuer,
            subject=verified.subject,
            email=claim_profile.email,
            email_verified=claim_profile.email_verified,
            preferred_username=claim_profile.preferred_username,
            display_name=claim_profile.name,
            role=role,
            groups=claim_strings(claims, self.settings.group_claim),
            provisioning=self.settings.provisioning,
            team_map=self.settings.team_map,
        )
        try:
            async with self.sessions.begin() as session:
                result: SignInResult = await self.provisioner.sign_in(session, request)
                member_role: Role = result.member.role or role
                await self.audit.append(
                    session,
                    AuditEntry(
                        ActorKind.MEMBER,
                        f"member:{result.member.member_id}",
                        "auth.sign_in",
                        "member",
                        result.member.member_id,
                        "Member signed in with OIDC",
                        json_object(
                            [
                                ("method", json_str("oidc")),
                                ("role", json_str(member_role.value)),
                                ("provisioning", json_str(result.provisioning.value)),
                            ]
                        ),
                    ),
                )
        except TelescreenError as error:
            if error.code in _AUDITED_DENIALS:
                await self._audit_denial(error.code, key_prefix)
            raise
        return result

    async def _audit_denial(self, reason: ErrorCode, key_prefix: str) -> None:
        """Audit a denied sign-in in its own transaction with safe details only."""
        async with self.sessions.begin() as session:
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:auth",
                    "auth.sign_in_denied",
                    "auth",
                    "oidc",
                    "OIDC sign-in denied",
                    json_object(
                        [
                            ("reason", json_str(reason.value)),
                            ("key_prefix", json_str(key_prefix)),
                        ]
                    ),
                ),
            )
