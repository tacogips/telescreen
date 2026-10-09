"""Service-account JWT claims and cache boundary tests."""

import json
from datetime import timedelta

import httpx

from telescreen.collectors.google.auth import (
    ServiceAccountKey,
    parse_service_account_key,
)
from telescreen.core.errors import ConfigError
from telescreen.core.vocabulary import ErrorCode
from tests.collectors.google.support import (
    GoogleTestBundle,
    JwtClaims,
    decode_claims,
    make_bundle,
)


async def test_token_claims_are_signed_cached_and_scoped_by_subject() -> None:
    """The JWT carries expected claims and cache entries separate delegated users."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    bundle: GoogleTestBundle = make_bundle(handler)
    try:
        token: str = await bundle.auth.token(("scope-a", "scope-b"), "member@example.invalid")
        claims: JwtClaims = decode_claims(bundle, bundle.token_requests[0])
        assert token == "access:member@example.invalid"
        assert claims.iss == "collector@example.invalid"
        assert claims.aud == "https://oauth.test/token"
        assert claims.scope == "scope-a scope-b"
        assert claims.sub == "member@example.invalid"
        assert claims.exp - claims.iat == 3600
        assert await bundle.auth.token(("scope-a", "scope-b"), "member@example.invalid") == token
        assert len(bundle.token_requests) == 1
        assert await bundle.auth.token(("scope-a", "scope-b"), "other@example.invalid") == (
            "access:other@example.invalid"
        )
        assert len(bundle.token_requests) == 2
    finally:
        await bundle.close()


async def test_token_refreshes_sixty_seconds_before_expiry() -> None:
    """A token expiring at the sixty-second margin is refreshed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    bundle: GoogleTestBundle = make_bundle(handler)
    try:
        first: str = await bundle.auth.token(("scope-a",), None)
        bundle.clock.advance(timedelta(seconds=3540))
        second: str = await bundle.auth.token(("scope-a",), None)
        assert first == second
        assert len(bundle.token_requests) == 2
    finally:
        await bundle.close()


def test_service_account_parser_and_repr_hide_private_key() -> None:
    """The secret parser builds a typed key while its representation omits key bytes."""
    encoded: str = json.dumps(
        {
            "client_email": "collector@example.invalid",
            "token_uri": "https://oauth.test/token",
            "private_key": "runtime-private-key-material",
        }
    )
    key: ServiceAccountKey = parse_service_account_key(encoded)
    assert "runtime-private-key-material" not in repr(key)
    assert key.client_email == "collector@example.invalid"


def test_service_account_parser_error_does_not_echo_input() -> None:
    """Invalid key documents yield a safe configuration error."""
    secret: str = "not-a-valid-key-document"
    try:
        parse_service_account_key(secret)
    except ConfigError as error:
        assert error.code is ErrorCode.CONFIG_ERROR
        assert secret not in str(error)
    else:
        raise AssertionError("invalid key document was accepted")
