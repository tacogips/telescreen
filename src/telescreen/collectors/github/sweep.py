"""Repository and branch partitioner for GitHub map-reduce sweeps."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    CollectorKind,
    CoverageState,
    ErrorCode,
    PlacementKind,
    UnsupportedReason,
)
from telescreen.ports.collectors import (
    CollectorConfig,
    Coverage,
    Exposure,
    GitHubSelector,
    OwnerIdentity,
    PlacementRef,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
)
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
)

from .client import GitHubApi
from .codeowners import CommitContext, commit_context, match_owner
from .exposure import repo_exposure


@dataclass(frozen=True)
class GitHubSweep:
    """Partition GitHub repositories and stream branch file outcomes."""

    api_factory: Callable[[CollectorConfig], GitHubApi]
    host: str
    max_bytes: int

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        """Create one deterministic shard for each selected repository branch."""
        if not isinstance(request.selector, GitHubSelector):
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "invalid GitHub selector")
        selector: GitHubSelector = request.selector
        api: GitHubApi = self.api_factory(config)
        repos: list[JsonValue] = []
        next_cursor: str | None = None
        if selector.repos:
            for name in selector.repos:
                repos.append(await api.repo(selector.owner, name))
        else:
            page_number: int = int(request.cursor) if request.cursor is not None else 1
            repos.extend(await api.org_repos(selector.owner, page_number))
            if len(repos) == 100:
                next_cursor = str(page_number + 1)
        shards: list[ShardSpec] = []
        for repository in repos:
            owner: str = _string(_get(_get(repository, "owner"), "login")) or selector.owner
            name: str = _string(_get(repository, "name"))
            repo_id: int = _integer(_get(repository, "id"))
            default_branch: str = _string(_get(repository, "default_branch"))
            branches: tuple[str, ...] = selector.branches or (default_branch,)
            for branch in branches:
                key: str = f"{self.host}/{owner}/{name}@{branch}"
                shard_key: str = hashlib.sha256(key.encode("utf-8")).hexdigest()
                descriptor: JsonValue = json_object(
                    (("repo_id", json_int(repo_id)), ("branch", json_str(branch)))
                )
                shards.append(ShardSpec(shard_key, descriptor, 1))
        return PartitionPage(tuple(shards), next_cursor, ())

    def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Stream fetched, removed, or failed outcomes for one branch shard."""
        return self._shard_resources(config, shard)

    async def _shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Resolve a branch tree and yield transient content for matching blobs."""
        selector: GitHubSelector
        if not isinstance(config.selector, GitHubSelector):
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "invalid GitHub selector")
        selector = config.selector
        repo_id: int = _integer(_get(shard.descriptor, "repo_id"))
        branch_name: str = _string(_get(shard.descriptor, "branch"))
        api: GitHubApi = self.api_factory(config)
        repository: JsonValue = await api.repo_by_id(repo_id)
        owner: str = _string(_get(_get(repository, "owner"), "login"))
        repo: str = _string(_get(repository, "name"))
        exposure: Exposure = repo_exposure(repository)
        branch_ref: str = (
            branch_name if branch_name.startswith("refs/heads/") else f"refs/heads/{branch_name}"
        )
        codeowners_text: str | None = await api.codeowners(owner, repo, branch_ref)
        placement: tuple[PlacementRef, ...] = (
            PlacementRef(PlacementKind.GITHUB_OWNER, owner.casefold()),
            PlacementRef(PlacementKind.GITHUB_REPO, f"{owner}/{repo}".casefold()),
        )
        branch: JsonValue = await api.branch(owner, repo, branch_name)
        head_sha: str = _string(_get(_get(branch, "commit"), "sha"))
        tree: JsonValue = await api.tree(owner, repo, head_sha)
        if _boolean(_get(tree, "truncated")):
            raise TelescreenError(ErrorCode.FETCH_FAILED, "GitHub tree is truncated")
        for entry in _array(_get(tree, "tree")):
            if _string(_get(entry, "type")) != "blob":
                continue
            path: str = _string(_get(entry, "path"))
            if not _path_selected(path, selector):
                continue
            size: int = _integer(_get(entry, "size"))
            ref: ResourceRef = self._ref(owner, repo, branch_name, path)
            codeowners_owner: OwnerIdentity | None = (
                match_owner(codeowners_text, path) if codeowners_text is not None else None
            )
            try:
                commit: JsonValue | None = await api.last_commit_for_path(
                    owner, repo, path, branch_ref
                )
            except Exception:
                commit = None
            context: CommitContext = commit_context(commit)
            owner_identity: OwnerIdentity | None = codeowners_owner or context.owner
            if size > self.max_bytes:
                content: ResourceContent = self._incomplete_content(
                    config,
                    ref,
                    path,
                    exposure,
                    owner_identity,
                    placement,
                    context.modified_at,
                )
                yield ShardEntry(ShardEntryKind.FETCHED, ref, content=content)
                continue
            sha: str = _string(_get(entry, "sha"))
            try:
                blob: JsonValue = await api.blob(owner, repo, sha)
                content: ResourceContent = self._content(
                    config,
                    ref,
                    path,
                    sha,
                    blob,
                    size,
                    exposure,
                    owner_identity,
                    placement,
                    context.modified_at,
                )
            except ResourceGoneError:
                yield ShardEntry(ShardEntryKind.REMOVED, ref)
                continue
            except TelescreenError as error:
                yield ShardEntry(ShardEntryKind.FAILED, ref, error_code=error.code)
                continue
            yield ShardEntry(ShardEntryKind.FETCHED, ref, content=content)

    def _ref(self, owner: str, repo: str, branch: str, path: str) -> ResourceRef:
        """Create a stable branch locator without embedding a commit SHA."""
        ref: str = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
        ref_json: str = dump_json(
            json_object(
                (
                    ("host", json_str(self.host)),
                    ("owner", json_str(owner)),
                    ("repo", json_str(repo)),
                    ("ref", json_str(ref)),
                    ("path", json_str(path)),
                )
            )
        )
        return ResourceRef(
            CollectorKind.GITHUB,
            ref_json,
            f"github:{self.host}/{owner}/{repo}@{ref}:{path}",
        )

    def _content(
        self,
        config: CollectorConfig,
        ref: ResourceRef,
        path: str,
        blob_sha: str,
        blob: JsonValue,
        size: int,
        exposure: Exposure,
        owner: OwnerIdentity | None,
        placement: tuple[PlacementRef, ...],
        modified_at: datetime | None,
    ) -> ResourceContent:
        """Build a transient scanner payload from a Git blob response."""
        encoded: str = _string(_get(blob, "content")).replace("\n", "")
        try:
            raw: bytes = base64.b64decode(encoded, validate=False)
            text: str = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            segments: tuple[Segment, ...] = ()
            complete: bool = False
            coverage: Coverage = Coverage(
                CoverageState.UNSUPPORTED,
                UnsupportedReason.BINARY,
            )
        else:
            if size > self.max_bytes:
                segments = ()
                complete = False
                coverage = Coverage(CoverageState.TOO_LARGE)
            else:
                short_sha: str = blob_sha[:7]
                segments = tuple(
                    Segment(f"L{index}@{short_sha}", line)
                    for index, line in enumerate(text.splitlines(), start=1)
                )
                complete = True
                coverage = Coverage(CoverageState.SCANNED)
        return ResourceContent(
            source="github",
            collector_config_id=config.id,
            segments=segments,
            complete=complete,
            sweep_covered=True,
            raw_locator=ref.raw_locator,
            display_title=path,
            ref=ref,
            exposure=exposure,
            owner=owner,
            placement=placement,
            coverage=coverage,
            modified_at=modified_at,
        )

    def _incomplete_content(
        self,
        config: CollectorConfig,
        ref: ResourceRef,
        path: str,
        exposure: Exposure,
        owner: OwnerIdentity | None,
        placement: tuple[PlacementRef, ...],
        modified_at: datetime | None,
    ) -> ResourceContent:
        """Represent an oversized enumerated file without scanning or resolving it."""
        return ResourceContent(
            source="github",
            collector_config_id=config.id,
            segments=(),
            complete=False,
            sweep_covered=True,
            raw_locator=ref.raw_locator,
            display_title=path,
            ref=ref,
            exposure=exposure,
            owner=owner,
            placement=placement,
            coverage=Coverage(CoverageState.TOO_LARGE),
            modified_at=modified_at,
        )


def _get(value: JsonValue, name: str) -> JsonValue:
    """Return an object field or JSON null."""
    from telescreen.core.json import json_null

    return value.get(name) or json_null()


def _string(value: JsonValue) -> str:
    """Return a string field or an empty string."""
    return value.as_str() or ""


def _integer(value: JsonValue) -> int:
    """Return an integer field or zero."""
    return value.as_int() or 0


def _boolean(value: JsonValue) -> bool:
    """Return a boolean field or false."""
    return value.as_bool() or False


def _array(value: JsonValue) -> tuple[JsonValue, ...]:
    """Return array entries or an empty tuple."""
    return value.as_list() or ()


def _path_selected(path: str, selector: GitHubSelector) -> bool:
    """Apply configured path patterns to the entire GitHub path."""
    return not selector.path_globs or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in selector.path_globs
    )
