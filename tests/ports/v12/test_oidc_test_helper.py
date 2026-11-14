"""Tests for the shared locally signed OIDC test provider."""

from datetime import UTC, datetime

import httpx
import jwt

from tests.support.oidc import TestIdp as OidcTestProvider


def test_test_idp_mints_valid_tokens_and_serves_discovery_and_jwks() -> None:
    """Generated signing keys validate locally and are exposed over MockTransport."""
    idp: OidcTestProvider = OidcTestProvider.generate(
        "https://idp.example.test/realm", "telescreen-ui"
    )
    now: int = int(datetime.now(UTC).timestamp())
    claims_json: str = (
        f'{{"iss":"{idp.issuer}","aud":"{idp.audience}","sub":"subject-1",'
        f'"iat":{now},"exp":{now + 300}}}'
    )
    token: str = idp.mint(claims_json)
    assert (
        jwt.decode(
            token,
            key=idp.private_key.public_key(),
            algorithms=["RS256"],
            audience=idp.audience,
            issuer=idp.issuer,
        )["sub"]
        == "subject-1"
    )
    assert idp.rotation_kid is not None
    rotated_token: str = idp.mint(claims_json, kid=idp.rotation_kid)
    assert rotated_token != token

    transport: httpx.MockTransport = httpx.MockTransport(idp.mock_handler)
    with httpx.Client(transport=transport) as client:
        discovery: httpx.Response = client.get(f"{idp.issuer}/.well-known/openid-configuration")
        jwks: httpx.Response = client.get(f"{idp.issuer}/.well-known/jwks.json")
    assert discovery.status_code == 200
    assert idp.issuer in discovery.text
    assert jwks.status_code == 200
    assert idp.kid in jwks.text
    assert idp.rotation_kid in jwks.text
