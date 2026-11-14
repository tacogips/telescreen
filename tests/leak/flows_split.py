"""Raw-value sink assertions for independent service process flows."""

import json
import sqlite3
from pathlib import Path

from telescreen.db.ownership import Store, tables_for
from tests.split.support import fetch_rows
from tests.support.samples import Sample


def assert_split_sinks_clean(
    samples: tuple[Sample, ...],
    database_paths: tuple[Path, ...],
    log_paths: tuple[Path, ...],
    api_responses: tuple[str, ...],
) -> None:
    """Reject raw runtime samples from each service database, logs and API response."""
    database_bytes: tuple[bytes, ...] = tuple(path.read_bytes() for path in database_paths)
    log_text: tuple[str, ...] = tuple(path.read_text(encoding="utf-8") for path in log_paths)
    for sample in samples:
        encoded: bytes = sample.value.encode("utf-8")
        assert all(encoded not in content for content in database_bytes), sample.name
        assert all(sample.value not in content for content in log_text), sample.name
        assert all(sample.value not in content for content in api_responses), sample.name
    assert_texts_clean(samples, log_text + api_responses)


def assert_store_tables_clean(
    samples: tuple[Sample, ...], stores: tuple[tuple[Store, Path], ...]
) -> None:
    """Scan every owned row and fail if a declared store table is missing."""
    for store, path in stores:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
            names: tuple[str, ...] = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            )
        for table in tables_for(store):
            assert table in names, f"{store.value} store is missing declared table {table}"
            rows: tuple[tuple[str, ...], ...] = fetch_rows(path, table)
            for sample in samples:
                escaped: str = json.dumps(sample.value, ensure_ascii=True)[1:-1]
                assert all(sample.value not in cell for row in rows for cell in row), (
                    store.value,
                    table,
                    sample.name,
                    "raw",
                )
                assert all(escaped not in cell for row in rows for cell in row), (
                    store.value,
                    table,
                    sample.name,
                    "json-escaped",
                )


def assert_texts_clean(samples: tuple[Sample, ...], texts: tuple[str, ...]) -> None:
    """Reject raw and JSON-escaped runtime samples in process and wire text."""
    for sample in samples:
        escaped: str = json.dumps(sample.value, ensure_ascii=True)[1:-1]
        assert all(sample.value not in text for text in texts), (sample.name, "raw")
        assert all(escaped not in text for text in texts), (sample.name, "json-escaped")
