"""Raw-value regression checks for development-only UI mock fixtures."""

from pathlib import Path

from telescreen.masking.redact import Redactor
from tests.support.samples import find_leaks


def test_every_mock_fixture_is_masked() -> None:
    """Require real mock fixture sources and reject detected or known raw samples."""
    fixture_root: Path = Path("src/web/src/api/mock")
    fixtures: tuple[Path, ...] = tuple(sorted(fixture_root.rglob("*.ts")))
    assert fixture_root.is_dir()
    assert fixtures
    redactor: Redactor = Redactor.default()
    for fixture in fixtures:
        content: str = fixture.read_text(encoding="utf-8")
        assert not redactor.has_hits(content), fixture.as_posix()
        assert find_leaks(content) == (), fixture.as_posix()
