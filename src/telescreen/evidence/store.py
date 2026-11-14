"""Evidence storage interfaces and local file implementation."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class EvidenceStore(Protocol):
    """Store accepted evidence bytes under opaque identifiers."""

    def put(self, key: str, data: bytes) -> None:
        """Persist accepted bytes using an opaque key."""

    def get(self, key: str) -> bytes:
        """Read stored bytes for an accepted evidence key."""


@dataclass(frozen=True)
class LocalFileEvidenceStore:
    """Store accepted evidence in files with owner-only permissions."""

    root: Path

    def put(self, key: str, data: bytes) -> None:
        """Create the evidence root and write the file with mode 0600."""
        self.root.mkdir(parents=True, exist_ok=True)
        path: Path = self._path(key)
        descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        path.chmod(0o600)

    def get(self, key: str) -> bytes:
        """Read stored bytes by opaque key."""
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        """Resolve a single safe key beneath the configured root."""
        if not key or Path(key).name != key or key in {".", ".."}:
            raise ValueError("invalid evidence key")
        return self.root / key
