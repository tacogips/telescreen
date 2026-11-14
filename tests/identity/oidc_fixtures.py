"""Shared offline IdP, settings, clock and token builders for identity tests."""

import base64
import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from telescreen.core.clock import FakeClock
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_object,
    json_str,
)
from telescreen.core.oidc_settings import OidcRoleMapping, OidcSettings
from telescreen.core.vocabulary import OidcProvisioning, Role
from telescreen.identity.discovery import OidcProviderClient
from telescreen.identity.verifier import OidcBearerVerifier
from tests.support.oidc import TestIdp

ISSUER: str = "https://idp.example.test/realms/telescreen"
AUDIENCE: str = "telescreen-ui"
SUBJECT: str = "f3c1d2a4-subject-value-never-logged"
EMAIL: str = "alice.identity@example.test"
START: datetime = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

MINTED_TOKENS: list[str] = []


def base_settings() -> OidcSettings:
    """Return OIDC settings matching the offline test IdP."""
    return OidcSettings(
        issuer=ISSUER,
        client_id=AUDIENCE,
        audience=AUDIENCE,
        scopes=("openid", "profile", "email"),
        algorithms=("RS256",),
        leeway_seconds=60,
        jwks_cache_seconds=300,
        role_claim="realm_access.roles",
        role_map=(
            OidcRoleMapping("telescreen-admin", Role.ADMIN),
            OidcRoleMapping("telescreen-responder", Role.RESPONDER),
            OidcRoleMapping("telescreen-viewer", Role.VIEWER),
        ),
        default_role=None,
        group_claim="groups",
        team_map=(),
        provisioning=OidcProvisioning.LINK,
    )


@dataclass
class IdpServer:
    """Configurable MockTransport IdP that counts every request."""

    idp: TestIdp
    serve_rotation_key: bool = True
    fail: bool = False
    discovery_issuer: str = ISSUER
    jwks_uri: str = f"{ISSUER}/protocol/openid-connect/certs"
    paths: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Serve discovery and JWKS documents or simulate an outage."""
        self.paths.append(request.url.path)
        if self.fail:
            raise httpx.ConnectError("idp unavailable", request=request)
        if str(request.url) == f"{ISSUER}/.well-known/openid-configuration":
            document: JsonValue = json_object(
                [
                    ("issuer", json_str(self.discovery_issuer)),
                    ("authorization_endpoint", json_str(f"{ISSUER}/protocol/openid-connect/auth")),
                    ("token_endpoint", json_str(f"{ISSUER}/protocol/openid-connect/token")),
                    ("end_session_endpoint", json_str(f"{ISSUER}/protocol/openid-connect/logout")),
                    ("jwks_uri", json_str(self.jwks_uri)),
                ]
            )
            return httpx.Response(200, content=dump_json(document))
        if str(request.url) == self.jwks_uri:
            served: TestIdp = self.idp
            if not self.serve_rotation_key:
                served = dataclasses.replace(self.idp, rotation_private_key=None, rotation_kid=None)
            return httpx.Response(200, content=served.jwks_json())
        return httpx.Response(404)

    def jwks_requests(self) -> int:
        """Return how many JWKS fetches were attempted."""
        return sum(1 for path in self.paths if path.endswith("/certs"))


@dataclass
class VerifierHarness:
    """A verifier wired to a MockTransport IdP and a fake clock."""

    server: IdpServer
    clock: FakeClock
    settings: OidcSettings
    provider: OidcProviderClient
    verifier: OidcBearerVerifier

    def mint(
        self,
        extra: tuple[tuple[str, JsonValue], ...] = (),
        drop: tuple[str, ...] = (),
        kid: str | None = None,
        extra_headers_json: str | None = None,
    ) -> str:
        """Mint and record a token with claims relative to the current clock time."""
        token: str = self.server.idp.mint(
            claims_document(self.clock.now(), extra, drop),
            kid=kid,
            extra_headers_json=extra_headers_json,
        )
        MINTED_TOKENS.append(token)
        return token


TEST_IDP: TestIdp = TestIdp.generate(ISSUER, AUDIENCE)


def make_harness(settings: OidcSettings | None = None) -> VerifierHarness:
    """Build a verifier harness with a fresh cache and request counter."""
    server: IdpServer = IdpServer(TEST_IDP)
    clock: FakeClock = FakeClock(START)
    configured: OidcSettings = settings if settings is not None else base_settings()
    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    provider: OidcProviderClient = OidcProviderClient(http, configured, clock)
    verifier: OidcBearerVerifier = OidcBearerVerifier(provider, configured, clock)
    return VerifierHarness(server, clock, configured, provider, verifier)


def claims_document(
    now: datetime,
    extra: tuple[tuple[str, JsonValue], ...] = (),
    drop: tuple[str, ...] = (),
) -> str:
    """Return a Keycloak-like access-token claim set with overrides applied."""
    issued: int = int(now.timestamp())
    base: list[tuple[str, JsonValue]] = [
        ("iss", json_str(ISSUER)),
        ("aud", json_array([json_str(AUDIENCE), json_str("account")])),
        ("azp", json_str(AUDIENCE)),
        ("sub", json_str(SUBJECT)),
        ("typ", json_str("Bearer")),
        ("iat", json_int(issued)),
        ("exp", json_int(issued + 300)),
        ("realm_access", json_object([("roles", json_array([json_str("telescreen-admin")]))])),
        ("groups", json_array([json_str("security")])),
        ("email", json_str(EMAIL)),
        ("email_verified", json_bool(True)),
        ("preferred_username", json_str("alice")),
        ("name", json_str("Alice Example")),
    ]
    replaced: set[str] = {name for name, _ in extra} | set(drop)
    members: list[tuple[str, JsonValue]] = [
        (name, value) for name, value in base if name not in replaced
    ]
    members.extend(extra)
    return dump_json(json_object(members))


def b64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def hand_built(header_json: str, claims_json: str, signature: bytes) -> str:
    """Assemble a compact JWS from raw parts and record it for leak checks."""
    token: str = (
        f"{b64url(header_json.encode())}.{b64url(claims_json.encode())}.{b64url(signature)}"
    )
    if signature == b"":
        token = f"{b64url(header_json.encode())}.{b64url(claims_json.encode())}."
    MINTED_TOKENS.append(token)
    return token
