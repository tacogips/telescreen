"""Shared pytest configuration."""

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip integration tests when no external service is configured."""
    enabled: bool = bool(
        os.environ.get("TELESCREEN_TEST_DATABASE_URL")
        or os.environ.get("TELESCREEN_TEST_MEILI_URL")
    )
    if enabled:
        return
    skip_marker: pytest.MarkDecorator = pytest.mark.skip(
        reason="requires TELESCREEN_TEST_DATABASE_URL or TELESCREEN_TEST_MEILI_URL"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
