"""Import-boundary checks for the independently deployed services."""

import subprocess
import sys


def _run(source: str) -> None:
    """Run a boundary assertion in a clean interpreter."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_inventory_cli_help_does_not_import_telescreen_features() -> None:
    """The standalone Inventory command stays below Telescreen feature packages."""
    _run(
        """
import subprocess
import sys
result = subprocess.run(
    [sys.executable, '-m', 'telescreen.inventory_service', '--help'],
    capture_output=True, text=True, check=False
)
assert result.returncode == 0, result.stderr
forbidden = (
    'telescreen.wiring', 'telescreen.inventory_service.wiring',
    'telescreen.inventory_service.bureau', 'telescreen.directory',
    'telescreen.issues', 'telescreen.notifications'
)
loaded = tuple(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
)
assert not loaded, loaded
"""
    )


def test_inventory_runtime_build_does_not_import_telescreen_features() -> None:
    """Building Inventory in isolation does not load Telescreen feature packages."""
    _run(
        """
import asyncio
import httpx
import sys
from telescreen.core.config import TelescreenSettings
from telescreen.inventory_service.app import build_inventory_runtime
class Env:
    def get(self, key, /):
        return 'p' * 32
async def run():
    client = httpx.AsyncClient()
    settings = TelescreenSettings(
        inventory_database_url='sqlite+aiosqlite:///:memory:',
        notification_service_url='http://127.0.0.1:1',
        notification_inventory_token_ref='env:INVENTORY',
        inventory_service_token_ref='env:INVENTORY',
        fingerprint_key_ref='env:FP'
    )
    runtime = build_inventory_runtime(settings, Env(), client)
    forbidden = (
        'telescreen.wiring', 'telescreen.inventory_service.wiring',
        'telescreen.inventory_service.bureau', 'telescreen.directory',
        'telescreen.issues', 'telescreen.notifications'
    )
    loaded = tuple(
        name for name in sys.modules
        if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
    )
    assert not loaded, loaded
    await runtime.engine.dispose()
    await client.aclose()
asyncio.run(run())
"""
    )


def test_notification_cli_help_does_not_import_telescreen_features() -> None:
    """The standalone Notification command does not import Inventory or Telescreen."""
    _run(
        """
import subprocess
import sys
result = subprocess.run(
    [sys.executable, '-m', 'telescreen.notification_service', '--help'],
    capture_output=True, text=True, check=False
)
assert result.returncode == 0, result.stderr
forbidden = (
    'telescreen.wiring', 'telescreen.issues', 'telescreen.inventory',
    'telescreen.collectors', 'telescreen.workflow', 'telescreen.alerts'
)
loaded = tuple(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
)
assert not loaded, loaded
"""
    )


def test_police_sdk_imports_without_telescreen_package() -> None:
    """The Police SDK remains independently importable."""
    _run(
        """
import sys
import telescreen_police_sdk
loaded = tuple(
    name for name in sys.modules
    if name == 'telescreen' or name.startswith('telescreen.')
)
assert not loaded
"""
    )
