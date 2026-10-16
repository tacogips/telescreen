"""Raw-value sink assertions for the independent service process flow."""

from pathlib import Path

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
