"""Bearer JWT verification: positive path, every negative case, leeway and rotation."""

import dataclasses
import hashlib
import hmac
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_int, json_str, parse_json
from telescreen.core.vocabulary import ErrorCode
from telescreen.identity.jose import parse_header, split_token
from telescreen.ports.identity import ProviderMetadata, VerifiedToken
from tests.identity.oidc_fixtures import (
    AUDIENCE,
    ISSUER,
    SUBJECT,
    TEST_IDP,
    VerifierHarness,
    b64url,
    base_settings,
    claims_document,
    hand_built,
    make_harness,
)


async def _rejected(harness: VerifierHarness, token: str) -> TelescreenError:
    """Assert a token is rejected with invalid_token and a value-free error."""
    with pytest.raises(TelescreenError) as caught:
        await harness.verifier.verify(token)
    error: TelescreenError = caught.value
    assert error.code is ErrorCode.INVALID_TOKEN
    assert token not in str(error)
    assert SUBJECT not in str(error)
    assert ISSUER not in str(error)
    return error


async def test_valid_rs256_token_verifies_and_repr_hides_subject() -> None:
    harness: VerifierHarness = make_harness()
    token: str = harness.mint()

    verified: VerifiedToken = await harness.verifier.verify(token)

    assert verified.issuer == ISSUER
    assert verified.subject == SUBJECT
    claims: JsonValue = parse_json(verified.claims_json)
    subject_claim: JsonValue | None = claims.get("sub")
    assert subject_claim is not None and subject_claim.as_str() == SUBJECT
    assert SUBJECT not in repr(verified)
    assert verified.claims_json not in repr(verified)
    assert SUBJECT not in repr(harness.verifier)


async def test_metadata_exposes_discovered_endpoints() -> None:
    harness: VerifierHarness = make_harness()

    metadata: ProviderMetadata = await harness.verifier.metadata()

    assert metadata.authorization_endpoint == f"{ISSUER}/protocol/openid-connect/auth"
    assert metadata.token_endpoint == f"{ISSUER}/protocol/openid-connect/token"
    assert metadata.end_session_endpoint == f"{ISSUER}/protocol/openid-connect/logout"


async def test_alg_none_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    claims: str = claims_document(harness.clock.now())
    header: str = '{"alg":"none","kid":"test-key-1"}'

    await _rejected(harness, hand_built(header, claims, b""))
    await _rejected(harness, hand_built(header, claims, b"not-a-signature"))


async def test_hs256_signed_with_public_key_bytes_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    public_pem: bytes = TEST_IDP.private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header_json: str = '{"alg":"HS256","kid":"test-key-1","typ":"JWT"}'
    claims: str = claims_document(harness.clock.now())
    signing_input: bytes = f"{b64url(header_json.encode())}.{b64url(claims.encode())}".encode()
    signature: bytes = hmac.new(public_pem, signing_input, hashlib.sha256).digest()

    await _rejected(harness, hand_built(header_json, claims, signature))


@pytest.mark.parametrize(
    "headers_json",
    [
        '{"alg":"RS256","kid":"test-key-1","jku":"https://evil.example.test/jwks"}',
        '{"alg":"RS256","kid":"test-key-1","jwk":{"kty":"RSA","n":"AQAB","e":"AQAB"}}',
        '{"alg":"RS256","kid":"test-key-1","x5u":"https://evil.example.test/cert"}',
        '{"alg":"RS256","kid":"test-key-1","crit":["exp"]}',
        '{"alg":"RS256"}',
        '{"alg":"RS256","kid":""}',
    ],
    ids=["jku", "jwk", "x5u", "crit", "missing-kid", "empty-kid"],
)
async def test_forbidden_or_incomplete_headers_are_rejected(headers_json: str) -> None:
    harness: VerifierHarness = make_harness()

    await _rejected(harness, harness.mint(extra_headers_json=headers_json))
    assert harness.server.paths == []


async def test_unknown_kid_after_refetch_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    await harness.verifier.verify(harness.mint())
    harness.clock.advance(timedelta(seconds=31))
    before: int = harness.server.jwks_requests()

    await _rejected(harness, harness.mint(extra_headers_json='{"alg":"RS256","kid":"unknown-key"}'))

    assert harness.server.jwks_requests() == before + 1


async def test_rsa_key_with_es256_header_is_rejected() -> None:
    harness: VerifierHarness = make_harness(
        dataclasses.replace(base_settings(), algorithms=("RS256", "ES256"))
    )
    claims: str = claims_document(harness.clock.now())

    await _rejected(harness, hand_built('{"alg":"ES256","kid":"test-key-1"}', claims, b"x" * 64))


async def test_algorithm_outside_configured_set_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    claims: str = claims_document(harness.clock.now())

    await _rejected(harness, hand_built('{"alg":"PS256","kid":"test-key-1"}', claims, b"x" * 256))


@pytest.mark.parametrize(
    ("extra", "drop"),
    [
        ((("iss", json_str("https://other.example.test/realms/telescreen")),), ()),
        ((("aud", json_str("another-client")),), ()),
        ((("aud", json_array([])),), ()),
        ((("aud", json_array([json_str(AUDIENCE), json_int(3)])),), ()),
        ((), ("aud",)),
        ((("azp", json_str("another-client")),), ()),
        ((("typ", json_str("ID")),), ()),
        ((("nonce", json_str("n-0S6_WzA2Mj")),), ()),
        ((("at_hash", json_str("77QmUPtjPfzWtF2AnpK9RQ")),), ()),
        ((("sub", json_str("")),), ()),
        ((("sub", json_str("s" * 256)),), ()),
        ((), ("sub",)),
        ((), ("exp",)),
        ((("exp", json_str("4102444800")),), ()),
    ],
    ids=[
        "wrong-iss",
        "aud-mismatch",
        "aud-empty-list",
        "aud-non-string",
        "aud-missing",
        "azp-mismatch",
        "typ-id",
        "nonce",
        "at-hash",
        "empty-sub",
        "long-sub",
        "missing-sub",
        "missing-exp",
        "string-exp",
    ],
)
async def test_invalid_claims_are_rejected(
    extra: tuple[tuple[str, JsonValue], ...], drop: tuple[str, ...]
) -> None:
    harness: VerifierHarness = make_harness()

    await _rejected(harness, harness.mint(extra=extra, drop=drop))


async def test_float_exp_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    issued: int = int(harness.clock.now().timestamp())
    claims: str = claims_document(harness.clock.now()).replace(
        f'"exp":{issued + 300}', f'"exp":{issued + 300}.5'
    )
    token: str = TEST_IDP.mint(claims)

    await _rejected(harness, token)


async def test_expiry_and_not_before_respect_leeway() -> None:
    harness: VerifierHarness = make_harness()
    now: int = int(harness.clock.now().timestamp())

    within: str = harness.mint(extra=(("exp", json_int(now - 30)),))
    assert (await harness.verifier.verify(within)).subject == SUBJECT
    await _rejected(harness, harness.mint(extra=(("exp", json_int(now - 61)),)))
    await _rejected(harness, harness.mint(extra=(("exp", json_int(now - 60)),)))
    early: str = harness.mint(extra=(("nbf", json_int(now + 30)),))
    assert (await harness.verifier.verify(early)).subject == SUBJECT
    await _rejected(harness, harness.mint(extra=(("nbf", json_int(now + 61)),)))


async def test_tampered_payload_is_rejected() -> None:
    harness: VerifierHarness = make_harness()
    header: str
    signature: str
    header, _, signature = harness.mint().split(".")
    forged_claims: str = claims_document(
        harness.clock.now(), extra=(("sub", json_str("other-subject")),)
    )

    await _rejected(harness, hand_built_segments(header, forged_claims, signature))


def hand_built_segments(header: str, claims_json: str, signature: str) -> str:
    """Splice a new payload between an existing header and signature."""
    return f"{header}.{b64url(claims_json.encode())}.{signature}"


async def test_segment_count_length_and_alphabet_are_enforced() -> None:
    harness: VerifierHarness = make_harness()
    token: str = harness.mint()

    await _rejected(harness, f"{token}.extra")
    await _rejected(harness, token.rsplit(".", 1)[0])
    padded: str = harness.mint(extra=(("pad", json_str("p" * 9000)),))
    assert len(padded) > 8192
    await _rejected(harness, padded)
    await _rejected(harness, token.replace(".", ".+", 1))
    await _rejected(harness, f"{token[:-1]}é")
    assert harness.server.paths == []


def test_jose_helpers_reject_malformed_input_with_static_errors() -> None:
    with pytest.raises(TelescreenError) as caught:
        split_token("a.b")
    assert caught.value.code is ErrorCode.INVALID_TOKEN
    with pytest.raises(TelescreenError):
        parse_header(b64url(b"[1,2]"))
    with pytest.raises(TelescreenError):
        parse_header(b64url(b'{"alg":"RS256","kid":"k","typ":5}'))
    assert parse_header(b64url(b'{"alg":"RS256","kid":"k","typ":"JWT"}')).typ == "JWT"


async def test_key_rotation_refetches_once_and_rate_limits_unknown_kids() -> None:
    harness: VerifierHarness = make_harness()
    harness.server.serve_rotation_key = False
    await harness.verifier.verify(harness.mint())
    assert harness.server.jwks_requests() == 1

    harness.server.serve_rotation_key = True
    rotated: str = harness.mint(kid="test-key-2")
    assert (await harness.verifier.verify(rotated)).subject == SUBJECT
    assert harness.server.jwks_requests() == 2

    harness.clock.advance(timedelta(seconds=10))
    await _rejected(harness, harness.mint(extra_headers_json='{"alg":"RS256","kid":"unknown-key"}'))
    assert harness.server.jwks_requests() == 2
    assert (await harness.verifier.verify(harness.mint(kid="test-key-2"))).subject == SUBJECT
    assert harness.server.jwks_requests() == 2
