"""Secret references and resolution helpers."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from telescreen.core.errors import ConfigError


class SecretScheme(StrEnum):
    """Supported secret reference schemes."""

    ENV = "env"
    FILE = "file"


@dataclass(frozen=True)
class SecretRef:
    """A reference to a secret stored outside the application database."""

    scheme: SecretScheme
    target: str = field(repr=False)


@dataclass(frozen=True)
class SecretValue:
    """Resolved secret value excluded from representations."""

    value: str = field(repr=False)


class EnvSource(Protocol):
    """Read-only environment-variable source."""

    def get(self, key: str, /) -> str | None:
        """Return an environment value when present."""


def parse_secret_ref(ref: str) -> SecretRef:
    """Parse an env:NAME or file:/path secret reference."""
    scheme_text: str
    target: str
    if ":" not in ref:
        raise ConfigError("invalid secret reference")
    scheme_text, target = ref.split(":", 1)
    try:
        scheme: SecretScheme = SecretScheme(scheme_text)
    except ValueError:
        raise ConfigError("unsupported secret reference scheme") from None
    if not target:
        raise ConfigError("secret reference target is empty")
    if scheme is SecretScheme.ENV and not target.isidentifier():
        raise ConfigError("invalid environment secret reference")
    return SecretRef(scheme, target)


def resolve_secret(ref: str, env: EnvSource) -> SecretValue:
    """Resolve a secret reference without including its value in errors."""
    parsed: SecretRef = parse_secret_ref(ref)
    if parsed.scheme is SecretScheme.ENV:
        value: str | None = env.get(parsed.target)
        if value is None:
            raise ConfigError("referenced environment secret is unavailable")
        return SecretValue(value)
    try:
        file_value: str = Path(parsed.target).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigError("referenced secret file is unavailable") from None
    return SecretValue(file_value.rstrip("\r\n"))
