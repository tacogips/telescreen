"""Google service-account JWT authentication and in-memory access-token cache."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import jwt
from pydantic import TypeAdapter, ValidationError

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.vocabulary import ErrorCode


@dataclass(frozen=True)
class ServiceAccountKey:
    """Parsed service-account signing material with a redacted representation."""

    client_email: str
    token_uri: str
    private_key: str = field(repr=False)


@dataclass(frozen=True)
class AccessToken:
    """Cached OAuth token and its safe expiration metadata."""

    scopes: tuple[str, ...]
    subject: str | None
    value: str = field(repr=False)
    expires_at: datetime


@dataclass
class GoogleAuth:
    """Exchange service-account assertions for short-lived Google access tokens."""

    http: httpx.AsyncClient
    key: ServiceAccountKey
    clock: Clock
    cache: list[AccessToken] = field(default_factory=list, repr=False)

    async def token(self, scopes: tuple[str, ...], subject: str | None = None) -> str:
        """Return a cached token or request one for the requested scopes and subject."""
        now: datetime = ensure_utc(self.clock.now())
        cached: AccessToken | None = next(
            (
                item
                for item in self.cache
                if item.scopes == scopes
                and item.subject == subject
                and item.expires_at > now + timedelta(seconds=60)
            ),
            None,
        )
        if cached is not None:
            return cached.value
        issued_at: int = int(now.timestamp())
        try:
            if subject is None:
                assertion: str = jwt.encode(
                    {
                        "iss": self.key.client_email,
                        "scope": " ".join(scopes),
                        "aud": self.key.token_uri,
                        "iat": issued_at,
                        "exp": issued_at + 3600,
                    },
                    self.key.private_key,
                    algorithm="RS256",
                )
            else:
                assertion = jwt.encode(
                    {
                        "iss": self.key.client_email,
                        "scope": " ".join(scopes),
                        "aud": self.key.token_uri,
                        "iat": issued_at,
                        "exp": issued_at + 3600,
                        "sub": subject,
                    },
                    self.key.private_key,
                    algorithm="RS256",
                )
            response: httpx.Response = await self.http.post(
                self.key.token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        except (httpx.RequestError, jwt.PyJWTError):
            raise ConfigError("Google service-account authentication failed") from None
        if response.is_error:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google authentication failed")
        try:
            token_response: TokenResponse = TypeAdapter(TokenResponse).validate_json(
                response.content
            )
        except ValidationError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google authentication failed") from None
        token: AccessToken = AccessToken(
            scopes=scopes,
            subject=subject,
            value=token_response.access_token,
            expires_at=now + timedelta(seconds=token_response.expires_in),
        )
        self.cache = [
            item
            for item in self.cache
            if item.expires_at > now and not (item.scopes == scopes and item.subject == subject)
        ]
        self.cache.append(token)
        return token.value


@dataclass(frozen=True)
class TokenResponse:
    """OAuth token response fields used by the collector."""

    access_token: str
    expires_in: int
    token_type: str = "Bearer"


def parse_service_account_key(text: str) -> ServiceAccountKey:
    """Parse a service-account JSON document without returning secret input on error."""
    try:
        return TypeAdapter(ServiceAccountKey).validate_json(text)
    except ValidationError:
        raise ConfigError("Google service-account key is invalid") from None
