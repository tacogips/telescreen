"""Notification runtime import boundary, checked in an isolated process."""

import subprocess
import sys


def test_notification_runtime_does_not_import_forbidden_application_modules() -> None:
    """Building Notification must not load Telescreen feature or Inventory modules."""
    source: str = """
import asyncio
import httpx
import sys
from telescreen.core.config import TelescreenSettings
from telescreen.notification_service.wiring import build_notification_runtime

class Env:
    def get(self, key: str, /) -> str | None:
        return "local-secret"

async def run() -> None:
    client = httpx.AsyncClient()
    runtime = build_notification_runtime(
        TelescreenSettings(
            notification_database_url="sqlite+aiosqlite:///:memory:",
            notification_service_token_ref="env:TELESCREEN_SERVICE_TOKEN",
            notification_inventory_token_ref="env:INVENTORY_SERVICE_TOKEN",
        ),
        Env(),
        client,
    )
    forbidden = (
        "telescreen.wiring", "telescreen.issues", "telescreen.workflow",
        "telescreen.alerts", "telescreen.collectors", "telescreen.police",
        "telescreen.search", "telescreen.directory", "telescreen.inventory",
        "telescreen.inventory_service",
    )
    loaded = tuple(
        name for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    )
    assert not loaded, loaded
    assert runtime.engine is not None
    await runtime.engine.dispose()
    await client.aclose()

asyncio.run(run())
"""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
