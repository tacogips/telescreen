"""Map GitHub repository metadata to transient exposure context."""

from __future__ import annotations

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import RepoVisibility, Visibility
from telescreen.ports.collectors import Exposure


def repo_exposure(repo: JsonValue) -> Exposure:
    """Return repository visibility and fork status without provider principals."""
    visibility_value: JsonValue | None = repo.get("visibility")
    visibility_name: str | None = (
        visibility_value.as_str() if visibility_value is not None else None
    )
    private_value: JsonValue | None = repo.get("private")
    is_private: bool = private_value.as_bool() is True if private_value is not None else False
    fork_value: JsonValue | None = repo.get("fork")
    is_fork: bool = fork_value.as_bool() is True if fork_value is not None else False
    if visibility_name == RepoVisibility.PUBLIC.value:
        return Exposure(Visibility.PUBLIC, repo_visibility=RepoVisibility.PUBLIC, is_fork=is_fork)
    if visibility_name == RepoVisibility.INTERNAL.value:
        return Exposure(Visibility.DOMAIN, repo_visibility=RepoVisibility.INTERNAL, is_fork=is_fork)
    if visibility_name == RepoVisibility.PRIVATE.value:
        return Exposure(Visibility.PRIVATE, repo_visibility=RepoVisibility.PRIVATE, is_fork=is_fork)
    if private_value is not None:
        fallback_visibility: Visibility = Visibility.PRIVATE if is_private else Visibility.PUBLIC
        fallback_repo_visibility: RepoVisibility = (
            RepoVisibility.PRIVATE if is_private else RepoVisibility.PUBLIC
        )
        return Exposure(
            fallback_visibility,
            repo_visibility=fallback_repo_visibility,
            is_fork=is_fork,
        )
    return Exposure(Visibility.UNKNOWN, repo_visibility=None, is_fork=is_fork)
