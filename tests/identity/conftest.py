"""Leak guard applied to every identity test."""

import logging
from collections.abc import Iterator

import pytest

from tests.identity.oidc_fixtures import EMAIL, MINTED_TOKENS, SUBJECT


@pytest.fixture(autouse=True)
def no_identity_values_in_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Capture all logs and assert tokens, subjects and emails never appear."""
    MINTED_TOKENS.clear()
    caplog.set_level(logging.DEBUG)
    yield
    text: str = caplog.text
    assert SUBJECT not in text
    assert EMAIL not in text
    for token in MINTED_TOKENS:
        assert token not in text
        assert token.split(".")[1] not in text
    MINTED_TOKENS.clear()
