"""Import gates for the lazy notification package facade."""

import subprocess
import sys


def test_dlp_import_does_not_import_delivery_adapters() -> None:
    """Importing the DLP submodule avoids transport and delivery dependencies."""
    command: tuple[str, ...] = (
        sys.executable,
        "-c",
        "import sys, telescreen.notifications.dlp; "
        "assert not any(name in sys.modules for name in ("
        "'telescreen.notifications.sender', 'telescreen.notifications.email', "
        "'telescreen.notifications.slack', 'telescreen.notifications.delivery'))",
    )
    result: subprocess.CompletedProcess[str] = subprocess.run(
        command, check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_notifications_port_does_not_import_command_codec() -> None:
    """The request DTO module stays import-independent from command codecs."""
    command: tuple[str, ...] = (
        sys.executable,
        "-c",
        "import sys, telescreen.ports.notifications; "
        "assert 'telescreen.ports.notification_commands' not in sys.modules",
    )
    result: subprocess.CompletedProcess[str] = subprocess.run(
        command, check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
