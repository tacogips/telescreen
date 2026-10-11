"""Bearer access-token verification against the configured OIDC issuer."""

import logging
from dataclasses import dataclass, field

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json
from telescreen.core.oidc_settings import OidcSettings
from telescreen.identity.discovery import DiscoveryDocument, KeyEntry, OidcProviderClient
from telescreen.identity.jose import (
    JwsHeader,
    invalid_token,
    parse_header,
    split_token,
    verify_signature,
)
from telescreen.ports.identity import BearerVerifier, ProviderMetadata, VerifiedToken

logger: logging.Logger = logging.getLogger(__name__)

MAX_SUBJECT_LENGTH: int = 255
_ID_TOKEN_CLAIMS: tuple[str, ...] = ("nonce", "at_hash")


def _reject(reason: str) -> TelescreenError:
    """Log a static rejection reason and return the invalid-token error."""
    logger.info("oidc token rejected: %s", reason)
    return invalid_token()


def expected_key_type(alg: str) -> str | None:
    """Return the JWK key type required by an asymmetric signature algorithm."""
    if alg.startswith(("RS", "PS")):
        return "RSA"
    if alg.startswith("ES"):
        return "EC"
    return None


def _audience_matches(value: JsonValue | None, audience: str) -> bool:
    """Return whether a string or non-empty string-array audience contains ours."""
    if value is None:
        return False
    single: str | None = value.as_str()
    if single is not None:
        return single == audience
    items: tuple[JsonValue, ...] | None = value.as_list()
    if not items:
        return False
    values: tuple[str | None, ...] = tuple(item.as_str() for item in items)
    return all(item is not None for item in values) and audience in values


@dataclass
class OidcBearerVerifier(BearerVerifier):
    """Validate signature, header and claims of IdP-issued access tokens."""

    provider: OidcProviderClient
    settings: OidcSettings = field(repr=False)
    clock: Clock = field(repr=False)

    async def verify(self, token: str) -> VerifiedToken:
        """Verify a bearer JWT or raise invalid_token / idp_unavailable."""
        segments: tuple[str, str, str] = split_token(token)
        header: JwsHeader = parse_header(segments[0])
        if header.alg not in self.settings.algorithms:
            raise _reject("algorithm")
        key_type: str | None = expected_key_type(header.alg)
        if key_type is None:
            raise _reject("algorithm")
        entry: KeyEntry | None = await self.provider.key_for(header.kid)
        if entry is None:
            raise _reject("unknown key")
        jwk_json: str
        kty: str | None
        use: str | None
        key_alg: str | None
        jwk_json, kty, use, key_alg = entry
        if kty != key_type:
            raise _reject("key type")
        if use is not None and use != "sig":
            raise _reject("key use")
        if key_alg is not None and key_alg != header.alg:
            raise _reject("key algorithm")
        payload: bytes = verify_signature(token, jwk_json, header.alg)
        try:
            claims: JsonValue = parse_json(payload)
        except TelescreenError:
            raise _reject("payload") from None
        subject: str = self._check_claims(claims)
        return VerifiedToken(
            issuer=self.settings.issuer, subject=subject, claims_json=dump_json(claims)
        )

    def _check_claims(self, claims: JsonValue) -> str:
        """Enforce issuer, audience, party, lifetime, subject and type claims."""
        if claims.kind is not JsonKind.OBJECT:
            raise _reject("payload")
        issuer: JsonValue | None = claims.get("iss")
        if issuer is None or issuer.as_str() != self.settings.issuer:
            raise _reject("issuer")
        if not _audience_matches(claims.get("aud"), self.settings.audience):
            raise _reject("audience")
        party: JsonValue | None = claims.get("azp")
        if party is not None and party.as_str() != self.settings.client_id:
            raise _reject("authorized party")
        now: float = ensure_utc(self.clock.now()).timestamp()
        leeway: int = self.settings.leeway_seconds
        expires: JsonValue | None = claims.get("exp")
        expires_at: int | None = expires.as_int() if expires is not None else None
        if expires_at is None or not now < expires_at + leeway:
            raise _reject("expiry")
        not_before: JsonValue | None = claims.get("nbf")
        if not_before is not None:
            not_before_at: int | None = not_before.as_int()
            if not_before_at is None or now < not_before_at - leeway:
                raise _reject("not before")
        subject_value: JsonValue | None = claims.get("sub")
        subject: str | None = subject_value.as_str() if subject_value is not None else None
        if not subject or len(subject) > MAX_SUBJECT_LENGTH:
            raise _reject("subject")
        token_type: JsonValue | None = claims.get("typ")
        if token_type is not None and token_type.as_str() != "Bearer":
            raise _reject("token type")
        if any(claims.get(name) is not None for name in _ID_TOKEN_CLAIMS):
            raise _reject("id token claims")
        return subject

    async def metadata(self) -> ProviderMetadata:
        """Return the discovered authorization, token and logout endpoints."""
        document: DiscoveryDocument = await self.provider.metadata()
        return ProviderMetadata(
            authorization_endpoint=document.authorization_endpoint,
            token_endpoint=document.token_endpoint,
            end_session_endpoint=document.end_session_endpoint,
        )
