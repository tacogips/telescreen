"""Exercise the three-process TCP flow with leak assertions on all owned stores."""

from pathlib import Path

import pytest

from tests.inventory_service.test_process import (
    run_three_processes_with_leak_probe,
)


@pytest.mark.asyncio
async def test_split_process_leak_matrix(tmp_path: Path) -> None:
    """Run the service process flow, which scans each database, log and API response."""
    await run_three_processes_with_leak_probe(tmp_path)
