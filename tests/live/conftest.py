"""Opt-in live-service test gates for local Mailpit, Slack, and Keycloak."""

import os

import pytest


def require_live_variables(*names: str) -> None:
    """Skip a live test when any required service variable is unavailable."""
    missing: tuple[str, ...] = tuple(name for name in names if not os.environ.get(name))
    if missing:
        pytest.skip("live service variables are not configured")
