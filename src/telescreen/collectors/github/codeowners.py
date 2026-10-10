"""Match GitHub CODEOWNERS rules without retaining source files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import IdentityKind
from telescreen.ports.collectors import OwnerIdentity


@dataclass(frozen=True)
class CommitContext:
    """Hold transient author identity and timestamp from a GitHub commit."""

    owner: OwnerIdentity | None
    modified_at: datetime | None


def commit_context(value: JsonValue | None) -> CommitContext:
    """Extract a transient commit author and UTC timestamp from API JSON."""
    if value is None:
        return CommitContext(None, None)
    author_login: str | None = _member(_member(value, "author"), "login").as_str()
    owner: OwnerIdentity | None = (
        OwnerIdentity(IdentityKind.GITHUB_LOGIN, author_login) if author_login else None
    )
    commit: JsonValue = _member(value, "commit")
    author_date: str | None = _member(_member(commit, "author"), "date").as_str()
    committer_date: str | None = _member(_member(commit, "committer"), "date").as_str()
    modified_at: datetime | None = _timestamp(author_date or committer_date)
    return CommitContext(owner, modified_at)


def _member(value: JsonValue, name: str) -> JsonValue:
    """Return a JSON member or its null value."""
    from telescreen.core.json import json_null

    return value.get(name) or json_null()


def _timestamp(value: str | None) -> datetime | None:
    """Parse a provider timestamp and normalize it to UTC."""
    if value is None:
        return None
    try:
        timestamp: datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def match_owner(codeowners_text: str, path: str) -> OwnerIdentity | None:
    """Return the first usable owner from the last rule matching ``path``."""
    selected: OwnerIdentity | None = None
    normalized_path: str = path.lstrip("/")
    for line in codeowners_text.splitlines():
        stripped: str = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pieces: tuple[str, ...] = tuple(stripped.split())
        if len(pieces) < 2 or not _matches(pieces[0], normalized_path):
            continue
        selected = _owner(pieces[1:])
    return selected


def _owner(values: tuple[str, ...]) -> OwnerIdentity | None:
    """Choose the first supported owner, skipping CODEOWNERS team slugs."""
    for value in values:
        if value.startswith("@"):
            login: str = value[1:]
            if "/" not in login and login:
                return OwnerIdentity(IdentityKind.GITHUB_LOGIN, login)
        elif "@" in value:
            return OwnerIdentity(IdentityKind.EMAIL, value)
    return None


def _matches(pattern: str, path: str) -> bool:
    """Match a supported CODEOWNERS pattern against a repository path."""
    rooted: bool = pattern.startswith("/") or "/" in pattern
    directory: bool = pattern.endswith("/")
    normalized: str = pattern.strip("/")
    if not normalized:
        return False
    expression: str = re.escape(normalized)
    expression = expression.replace(r"\*\*", ".*")
    expression = expression.replace(r"\*", "[^/]*")
    prefix: str = "^" if rooted else r"^(?:.*/)?"
    suffix: str = r"(?:/.*)?$" if directory else "$"
    return re.match(prefix + expression + suffix, path) is not None
