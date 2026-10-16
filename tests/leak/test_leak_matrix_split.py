"""Exercise the three-process TCP flow with leak assertions on all owned stores."""

import sqlite3
from pathlib import Path

import pytest

from telescreen.db.ownership import Store
from tests.inventory_service.test_process import (
    run_three_processes_with_leak_probe,
)
from tests.leak.flows_split import assert_store_tables_clean
from tests.support.samples import all_samples


@pytest.mark.asyncio
async def test_split_process_leak_matrix(tmp_path: Path) -> None:
    """Run the service process flow, which scans each database, log and API response."""
    await run_three_processes_with_leak_probe(tmp_path)


def test_split_leak_matrix_fails_when_owned_table_is_missing(tmp_path: Path) -> None:
    """Require every table declared by ownership metadata to exist in its store."""
    path: Path = tmp_path / "empty.db"
    connection: sqlite3.Connection = sqlite3.connect(path)
    connection.close()
    with pytest.raises(AssertionError, match="store is missing declared table"):
        assert_store_tables_clean(all_samples(), ((Store.MAIN, path),))
