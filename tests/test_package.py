"""Installed package and CLI contract tests."""

from pathlib import Path

import pytest

from telescreen import greet
from telescreen.cli import main


def test_greet_returns_expected_message() -> None:
    """Keep the package's established greeting API covered."""
    assert greet("uv") == "Hello, uv!"


def test_bare_cli_prints_help_and_returns_usage_code() -> None:
    """Treat an empty command as documented usage failure."""
    assert main([]) == 2


def test_workflow_validation_runs_without_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validate the example workflow offline and report invalid input safely."""
    monkeypatch.setenv("TELESCREEN_MANAGED_LLM_PROVIDER", "codex")
    monkeypatch.delenv("TELESCREEN_MANAGED_LLM_MODEL", raising=False)
    assert main(["workflow", "validate", "examples/workflows/github-credential-leak.yaml"]) == 0
    invalid: Path = tmp_path / "invalid.yaml"
    invalid.write_text("kind: Nope\n", encoding="utf-8")
    assert main(["workflow", "validate", str(invalid)]) == 2


def test_codex_provider_without_model_is_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map an incomplete Codex managed-provider configuration to exit code three."""
    monkeypatch.setenv("TELESCREEN_MANAGED_LLM_PROVIDER", "codex")
    monkeypatch.delenv("TELESCREEN_MANAGED_LLM_MODEL", raising=False)
    assert main(["serve"]) == 3


def test_serve_without_built_index_exits_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject a configured UI directory without an index before starting uvicorn."""
    monkeypatch.delenv("TELESCREEN_MANAGED_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("TELESCREEN_FINGERPRINT_KEY", "env:TELESCREEN_TEST_KEY")
    monkeypatch.setenv("TELESCREEN_TEST_KEY", "t" * 32)
    monkeypatch.setenv("TELESCREEN_WEB_DIST", str(tmp_path))
    assert main(["serve"]) == 3
