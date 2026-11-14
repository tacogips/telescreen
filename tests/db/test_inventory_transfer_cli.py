"""The transfer CLI uses configured stores and emits value-free content manifests."""

import asyncio

import pytest

from telescreen.cli import main
from tests.db.test_inventory_transfer import TransferWorld, world

__all__: list[str] = ["world"]


@pytest.mark.asyncio
async def test_real_copy_verify_and_merge_cli(
    world: TransferWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No runtime fingerprint/channel secret is needed for a stopped-writer data operation."""
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", world.source_url)
    monkeypatch.setenv("TELESCREEN_INVENTORY_DATABASE_URL", world.target_url)
    assert await asyncio.to_thread(main, ["inventory-store", "copy"]) == 3
    assert await asyncio.to_thread(main, ["inventory-store", "verify", "--writers-stopped"]) == 1
    assert await asyncio.to_thread(main, ["inventory-store", "copy", "--writers-stopped"]) == 0
    output: str = capsys.readouterr().out
    assert '"copied":true' in output and '"schema_revision":"inventory_0002"' in output
    assert "original-revision" not in output and "Café" not in output
    assert "sqlite" not in output
    assert await asyncio.to_thread(main, ["inventory-store", "verify", "--writers-stopped"]) == 0
    assert (
        await asyncio.to_thread(
            main, ["inventory-store", "copy", "--direction", "merge", "--writers-stopped"]
        )
        == 0
    )
    assert '"copied":false' in capsys.readouterr().out


def test_invalid_database_configuration_does_not_expose_values_or_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SQLAlchemy URL/driver failures follow the CLI's safe runtime-error behavior."""
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///unused.db")
    monkeypatch.setenv("TELESCREEN_INVENTORY_DATABASE_URL", "private-invalid-url")
    assert main(["inventory-store", "copy", "--writers-stopped"]) == 1
    assert capsys.readouterr().err == "runtime command failed\n"
