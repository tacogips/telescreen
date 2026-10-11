"""Discovery validation, cache lifetime and stale-JWKS fallback."""

from datetime import timedelta

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.identity.discovery import DiscoveryDocument
from tests.identity.oidc_fixtures import ISSUER, SUBJECT, VerifierHarness, make_harness


async def _unavailable(harness: VerifierHarness, token: str) -> None:
    """Assert verification fails with idp_unavailable and no host or token detail."""
    with pytest.raises(TelescreenError) as caught:
        await harness.verifier.verify(token)
    assert caught.value.code is ErrorCode.IDP_UNAVAILABLE
    assert "idp.example.test" not in str(caught.value)
    assert token not in str(caught.value)


async def test_discovery_issuer_mismatch_is_unavailable_with_static_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness: VerifierHarness = make_harness()
    harness.server.discovery_issuer = "https://idp.example.test/realms/other"

    await _unavailable(harness, harness.mint())

    assert "oidc discovery issuer mismatch" in caplog.text
    assert "realms/other" not in caplog.text


@pytest.mark.parametrize(
    "jwks_uri",
    [
        "https://evil.example.test/realms/telescreen/protocol/openid-connect/certs",
        "http://idp.example.test/realms/telescreen/protocol/openid-connect/certs",
        "https://idp.example.test:8443/realms/telescreen/protocol/openid-connect/certs",
    ],
    ids=["other-host", "downgraded-scheme", "other-port"],
)
async def test_cross_origin_jwks_uri_is_unavailable(jwks_uri: str) -> None:
    harness: VerifierHarness = make_harness()
    harness.server.jwks_uri = jwks_uri

    await _unavailable(harness, harness.mint())

    assert not any(path.endswith("/certs") for path in harness.server.paths)


async def test_first_use_fetch_failure_is_unavailable_not_invalid() -> None:
    harness: VerifierHarness = make_harness()
    harness.server.fail = True

    await _unavailable(harness, harness.mint())
    with pytest.raises(TelescreenError) as caught:
        await harness.verifier.metadata()
    assert caught.value.code is ErrorCode.IDP_UNAVAILABLE


async def test_cache_is_reused_while_fresh_and_refetched_after_expiry() -> None:
    harness: VerifierHarness = make_harness()
    await harness.verifier.verify(harness.mint())
    harness.clock.advance(timedelta(seconds=299))
    await harness.verifier.verify(harness.mint())
    assert harness.server.jwks_requests() == 1

    harness.clock.advance(timedelta(seconds=2))
    await harness.verifier.verify(harness.mint())
    assert harness.server.jwks_requests() == 2
    document: DiscoveryDocument = await harness.provider.metadata()
    assert document.issuer == ISSUER


async def test_stale_jwks_serves_during_outage_for_up_to_one_hour() -> None:
    harness: VerifierHarness = make_harness()
    await harness.verifier.verify(harness.mint())
    harness.server.fail = True

    harness.clock.advance(timedelta(seconds=1800))
    assert (await harness.verifier.verify(harness.mint())).subject == SUBJECT
    harness.clock.advance(timedelta(seconds=10))
    requests_before: int = len(harness.server.paths)
    assert (await harness.verifier.verify(harness.mint())).subject == SUBJECT
    assert len(harness.server.paths) == requests_before

    harness.clock.advance(timedelta(seconds=300 + 3600 - 1800))
    await _unavailable(harness, harness.mint())


async def test_recovery_after_outage_replaces_stale_keys() -> None:
    harness: VerifierHarness = make_harness()
    await harness.verifier.verify(harness.mint())
    harness.server.fail = True
    harness.clock.advance(timedelta(seconds=600))
    await harness.verifier.verify(harness.mint())

    harness.server.fail = False
    harness.clock.advance(timedelta(seconds=31))
    jwks_before: int = harness.server.jwks_requests()
    await harness.verifier.verify(harness.mint())
    assert harness.server.jwks_requests() == jwks_before + 1
    harness.server.fail = True
    harness.clock.advance(timedelta(seconds=300 + 3599))
    assert (await harness.verifier.verify(harness.mint())).subject == SUBJECT
