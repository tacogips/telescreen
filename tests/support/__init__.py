"""Shared test helpers."""

import os

import pytest


def require_env(name: str) -> str:
    """Return a required test environment variable or skip the test."""
    value: str | None = os.environ.get(name)
    if value is None:
        pytest.skip(f"requires {name}")
    return value
