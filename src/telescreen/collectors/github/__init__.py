"""GitHub collector API, adapter, and sweep capability."""

from telescreen.collectors.github.adapter import GitHubAdapter
from telescreen.collectors.github.client import GitHubApi
from telescreen.collectors.github.sweep import GitHubSweep

__all__: list[str] = ["GitHubApi", "GitHubAdapter", "GitHubSweep"]
