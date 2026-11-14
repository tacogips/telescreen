"""Shared typed Google MockTransport fixtures for collector tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
    generate_private_key,
)
from pydantic import TypeAdapter

from telescreen.collectors.google.adapter import GoogleAdapter, build_google_adapters
from telescreen.collectors.google.auth import GoogleAuth, ServiceAccountKey
from telescreen.collectors.google.drive import DriveApi
from telescreen.collectors.google.sheets import SheetsApi
from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import CollectorKind, TriggerMode
from telescreen.ports.collectors import CollectorConfig, DriveSelector

TOKEN_URI: str = "https://oauth.test/token"


class GoogleHandler(Protocol):
    """Mock API callback used by httpx.MockTransport."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Return a deterministic response for one request."""
        ...


@dataclass(frozen=True)
class JwtClaims:
    """JWT claim fields inspected by auth and impersonation tests."""

    iss: str
    scope: str
    aud: str
    iat: int
    exp: int
    sub: str | None = None


@dataclass
class GoogleTestBundle:
    """Mock HTTP clients and generated service-account material."""

    client: httpx.AsyncClient
    auth: GoogleAuth
    drive: DriveApi
    sheets: SheetsApi
    clock: FakeClock
    public_key: RSAPublicKey
    token_requests: list[httpx.Request]
    api_requests: list[httpx.Request]

    async def close(self) -> None:
        """Close the shared mock client."""
        await self.client.aclose()


@dataclass(frozen=True)
class DriveClientFactory:
    """Callable Drive client provider for one GoogleAdapter."""

    value: DriveApi

    def __call__(self) -> DriveApi:
        """Return the configured Drive client."""
        return self.value


@dataclass(frozen=True)
class SheetsClientFactory:
    """Callable Sheets client provider for one GoogleAdapter."""

    value: SheetsApi

    def __call__(self) -> SheetsApi:
        """Return the configured Sheets client."""
        return self.value


@dataclass
class SweepClientFactory:
    """Drive client plus the selected kind adapter for GoogleSweep tests."""

    drive_client: DriveApi
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter]

    def drive(self) -> DriveApi:
        """Return the configured Drive client."""
        return self.drive_client

    def adapter(self, kind: CollectorKind) -> GoogleAdapter:
        """Find the adapter matching a Google collector kind."""
        return next(adapter for adapter in self.adapters if adapter.kind is kind)


def make_bundle(handler: GoogleHandler) -> GoogleTestBundle:
    """Create generated RSA service-account auth with a routed mock transport."""
    private_key: RSAPrivateKey = generate_private_key(public_exponent=65537, key_size=2048)
    pem: bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key: RSAPublicKey = private_key.public_key()
    key: ServiceAccountKey = ServiceAccountKey(
        "collector@example.invalid", TOKEN_URI, pem.decode("utf-8")
    )
    clock: FakeClock = FakeClock(datetime(2026, 10, 8, 12, 0, tzinfo=UTC))
    token_requests: list[httpx.Request] = []
    api_requests: list[httpx.Request] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        """Return OAuth tokens locally and pass Google calls to the test handler."""
        if str(request.url) == TOKEN_URI:
            token_requests.append(request)
            form: httpx.QueryParams = httpx.QueryParams(request.content.decode("utf-8"))
            assertion: str = form.get("assertion", "")
            claims: JwtClaims = TypeAdapter(JwtClaims).validate_python(
                jwt.decode(
                    assertion,
                    public_key,
                    algorithms=("RS256",),
                    options={"verify_aud": False, "verify_iat": False, "verify_exp": False},
                )
            )
            access_token: str = f"access:{claims.sub}" if claims.sub else "access:service"
            return httpx.Response(
                200,
                json={"access_token": access_token, "expires_in": 3600, "token_type": "Bearer"},
            )
        api_requests.append(request)
        return handler(request)

    client: httpx.AsyncClient = httpx.AsyncClient(
        transport=httpx.MockTransport(dispatch), timeout=3.0
    )
    auth: GoogleAuth = GoogleAuth(client, key, clock)
    drive: DriveApi = DriveApi(client, auth)
    sheets: SheetsApi = SheetsApi(client, auth)
    return GoogleTestBundle(
        client, auth, drive, sheets, clock, public_key, token_requests, api_requests
    )


def adapters_for(
    bundle: GoogleTestBundle,
    channel_token_key: bytes = b"test-channel-key",
    public_base_url: str | None = "https://bureau.example.invalid",
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter]:
    """Construct all three adapter kinds over one MockTransport client."""
    return build_google_adapters(
        DriveClientFactory(bundle.drive),
        SheetsClientFactory(bundle.sheets),
        bundle.clock,
        channel_token_key,
        public_base_url,
        max_bytes,
    )


def drive_config(
    kind: CollectorKind = CollectorKind.GDRIVE,
    selector: DriveSelector | None = None,
) -> CollectorConfig:
    """Return an enabled test collector config with a typed Drive selector."""
    selected: DriveSelector = selector if selector is not None else DriveSelector((), (), ())
    return CollectorConfig(
        id="config-1",
        name="google-test",
        kind=kind,
        trigger_mode=TriggerMode.BOTH,
        cron="0 * * * *",
        sweep_cron="0 0 * * *",
        sweep_max_concurrency=4,
        selector=selected,
        credential_ref="env:GOOGLE_TEST_KEY",
        enabled=True,
    )


def decode_claims(bundle: GoogleTestBundle, request: httpx.Request) -> JwtClaims:
    """Decode a generated assertion for a specific token request."""
    form: httpx.QueryParams = httpx.QueryParams(request.content.decode("utf-8"))
    assertion: str = form.get("assertion", "")
    return TypeAdapter(JwtClaims).validate_python(
        jwt.decode(
            assertion,
            bundle.public_key,
            algorithms=("RS256",),
            options={"verify_aud": False, "verify_iat": False, "verify_exp": False},
        )
    )
