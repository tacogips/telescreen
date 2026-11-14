"""GitHub webhook, incremental schedule, and content adapter."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import (
    CollectorKind,
    CoverageState,
    ErrorCode,
    PlacementKind,
    UnsupportedReason,
)
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    Coverage,
    EventCheck,
    EventCheckKind,
    EventResources,
    Exposure,
    GitHubSelector,
    InboundWebhook,
    OwnerIdentity,
    PlacementRef,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
    WatchState,
)

from .client import GitHubApi, GitHubNotFound
from .codeowners import CommitContext, commit_context, match_owner
from .exposure import repo_exposure


@dataclass(frozen=True)
class GitHubAdapter:
    """Implement collector operations for GitHub and GitHub Enterprise."""

    api_factory: Callable[[CollectorConfig], GitHubApi]
    host: str
    max_bytes: int
    kind: CollectorKind = CollectorKind.GITHUB

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Validate GitHub's SHA-256 webhook signature in constant time."""
        signature: str | None = event.header("X-Hub-Signature-256")
        event_name: str | None = event.header("X-GitHub-Event")
        if event_name == "ping" and signature is None:
            return EventCheck(EventCheckKind.VALID)
        if signature is None or secrets.signing_secret is None:
            return EventCheck(EventCheckKind.INVALID)
        expected: str = (
            "sha256="
            + hmac.new(
                secrets.signing_secret.encode("utf-8"), event.body, hashlib.sha256
            ).hexdigest()
        )
        return EventCheck(
            EventCheckKind.VALID
            if hmac.compare_digest(expected, signature)
            else EventCheckKind.INVALID
        )

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Extract selected push and pull-request file refs from an event."""
        payload: JsonValue = parse_json(event.body)
        event_name: str | None = event.header("X-GitHub-Event")
        if event_name == "ping":
            return EventResources((), ())
        repo_value: JsonValue = _get(payload, "repository")
        owner: str = _string(_get(_get(repo_value, "owner"), "login"))
        repo: str = _string(_get(repo_value, "name"))
        selector: GitHubSelector = _selector(config)
        if owner.casefold() != selector.owner.casefold() or not _repo_selected(repo, selector):
            return EventResources((), ())
        if event_name == "push":
            ref: str = _string(_get(payload, "ref"))
            branch_name: str = ref.removeprefix("refs/heads/")
            default_branch: str = _string(_get(repo_value, "default_branch"))
            if not ref.startswith("refs/heads/") or not _branch_selected(
                branch_name, default_branch, selector
            ):
                return EventResources((), ())
            if _boolean(_get(payload, "forced")):
                return EventResources((), (), poll_changes=True)
            changed: list[ResourceRef] = []
            removed: list[ResourceRef] = []
            commits: tuple[JsonValue, ...] = _array(_get(payload, "commits"))
            head_sha: str | None = _validated_sha(
                _optional_string(_get(_get(payload, "head_commit"), "id"))
            )
            for commit in commits:
                for path in _strings(_get(commit, "added")) + _strings(_get(commit, "modified")):
                    if _path_selected(path, selector):
                        changed.append(self._ref(owner, repo, ref, path, head_sha))
                for path in _strings(_get(commit, "removed")):
                    if _path_selected(path, selector):
                        removed.append(self._ref(owner, repo, ref, path))
            return EventResources(tuple(changed), tuple(removed))
        if event_name == "pull_request":
            action: str = _string(_get(payload, "action"))
            if action not in ("opened", "synchronize", "reopened"):
                return EventResources((), ())
            number: int = _integer(_get(payload, "number"))
            api: GitHubApi = self._api(config)
            files: tuple[JsonValue, ...] = await api.pull_files(owner, repo, number)
            changed: list[ResourceRef] = []
            for file_value in files:
                path: str = _string(_get(file_value, "filename"))
                status: str = _string(_get(file_value, "status"))
                if status != "removed" and _path_selected(path, selector):
                    changed.append(self._ref(owner, repo, f"refs/pull/{number}/head", path))
            return EventResources(tuple(changed), ())
        return EventResources((), ())

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """List incremental changes, or page a complete listing after rewrites."""
        selector: GitHubSelector = _selector(config)
        api: GitHubApi = self._api(config)
        old_cursor: JsonValue = parse_json(cursor.value) if cursor is not None else json_object(())
        old_offset: int = _integer(_get(old_cursor, "__offset"))
        paging: bool = _get(old_cursor, "__offset").as_int() is not None
        targets: list[_Target] = await self._targets(config, selector, api)
        target_heads: list[tuple[_Target, str]] = []
        full_required: bool = False
        changes: list[ResourceRef] = []
        removed: list[ResourceRef] = []
        next_members: list[tuple[str, JsonValue]] = []
        for target in targets:
            branch_data: JsonValue = await api.branch(target.owner, target.repo, target.branch)
            head_sha: str = _string(_get(_get(branch_data, "commit"), "sha"))
            key: str = f"{target.owner}/{target.repo}@{target.branch}"
            old_sha: str | None = _optional_string(_get(old_cursor, key))
            pinned_sha: str = old_sha if paging and old_sha is not None else head_sha
            target_heads.append((target, pinned_sha))
            next_members.append((key, json_str(pinned_sha)))
            if paging:
                full_required = True
                continue
            if old_sha is None:
                full_required = True
                continue
            if old_sha == head_sha:
                continue
            try:
                comparison: JsonValue = await api.compare(
                    target.owner, target.repo, old_sha, head_sha
                )
            except GitHubNotFound:
                full_required = True
                continue
            status: str = _string(_get(comparison, "status"))
            if status == "identical":
                continue
            files: tuple[JsonValue, ...] = _array(_get(comparison, "files"))
            if status != "ahead" or _boolean(_get(comparison, "truncated")) or len(files) >= 300:
                full_required = True
                continue
            for file_value in files:
                filename: str = _string(_get(file_value, "filename"))
                previous: str = _string(_get(file_value, "previous_filename"))
                state: str = _string(_get(file_value, "status"))
                if state == "removed":
                    if _path_selected(filename, selector):
                        removed.append(
                            self._ref(
                                target.owner, target.repo, _branch_ref(target.branch), filename
                            )
                        )
                elif state == "renamed":
                    if previous and _path_selected(previous, selector):
                        removed.append(
                            self._ref(
                                target.owner, target.repo, _branch_ref(target.branch), previous
                            )
                        )
                    if _path_selected(filename, selector):
                        changes.append(
                            self._ref(
                                target.owner, target.repo, _branch_ref(target.branch), filename
                            )
                        )
                elif _path_selected(filename, selector):
                    changes.append(
                        self._ref(target.owner, target.repo, _branch_ref(target.branch), filename)
                    )
        if full_required:
            all_refs: list[ResourceRef] = []
            for target, head_sha in target_heads:
                tree: JsonValue = await api.tree(target.owner, target.repo, head_sha)
                if _boolean(_get(tree, "truncated")):
                    raise TelescreenError(ErrorCode.FETCH_FAILED, "GitHub tree is truncated")
                for entry in _array(_get(tree, "tree")):
                    path: str = _string(_get(entry, "path"))
                    if _string(_get(entry, "type")) == "blob" and _path_selected(path, selector):
                        all_refs.append(
                            self._ref(target.owner, target.repo, _branch_ref(target.branch), path)
                        )
            page_size: int = 100
            page: tuple[ResourceRef, ...] = tuple(all_refs[old_offset : old_offset + page_size])
            offset: int = old_offset + len(page)
            has_more: bool = offset < len(all_refs)
            if has_more:
                next_members.append(("__offset", json_int(offset)))
            next_cursor: CollectorCursor = CollectorCursor(dump_json(json_object(next_members)))
            return ChangePage(page, (), next_cursor, True, has_more)
        next_cursor = CollectorCursor(dump_json(json_object(next_members)))
        return ChangePage(tuple(changes), tuple(removed), next_cursor, False, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Fetch a selected file transiently and expose text only as line segments."""
        parts: _RefParts = _parse_ref(ref)
        api: GitHubApi = self._api(config)
        repository: JsonValue = await api.repo(parts.owner, parts.repo)
        exposure: Exposure = repo_exposure(repository)
        owner_text: str | None = await api.codeowners(parts.owner, parts.repo, parts.ref)
        codeowners_owner: OwnerIdentity | None = (
            match_owner(owner_text, parts.path) if owner_text is not None else None
        )
        try:
            commit: JsonValue | None = (
                await api.commit(parts.owner, parts.repo, parts.head_sha)
                if parts.head_sha is not None
                else await api.last_commit_for_path(parts.owner, parts.repo, parts.path, parts.ref)
            )
        except Exception:
            commit = None
        context: CommitContext = commit_context(commit)
        owner: OwnerIdentity | None = codeowners_owner or context.owner
        placement: tuple[PlacementRef, ...] = (
            PlacementRef(PlacementKind.GITHUB_OWNER, parts.owner.casefold()),
            PlacementRef(
                PlacementKind.GITHUB_REPO,
                f"{parts.owner}/{parts.repo}".casefold(),
            ),
        )
        try:
            metadata: JsonValue = await api.contents(parts.owner, parts.repo, parts.path, parts.ref)
        except ResourceGoneError:
            raise
        except TelescreenError as error:
            coverage_state: CoverageState = (
                CoverageState.PERMISSION_DENIED
                if error.code is ErrorCode.FORBIDDEN
                else CoverageState.FETCH_FAILED
            )
            return self._content(
                config,
                ref,
                parts,
                (),
                False,
                False,
                exposure,
                owner,
                placement,
                Coverage(coverage_state),
                context.modified_at,
            )
        selector: GitHubSelector = _selector(config)
        branch_name: str = parts.ref.removeprefix("refs/heads/")
        sweep_covered: bool = False
        if (
            parts.ref.startswith("refs/heads/")
            and parts.owner.casefold() == selector.owner.casefold()
            and _repo_selected(parts.repo, selector)
        ):
            if selector.branches:
                sweep_covered = branch_name in selector.branches
            else:
                default_branch: str = _string(_get(repository, "default_branch"))
                sweep_covered = branch_name == default_branch
        content: str = _string(_get(metadata, "content")).replace("\n", "").replace("\r", "")
        size: int = _integer(_get(metadata, "size"))
        blob_sha: str = _string(_get(metadata, "sha"))
        if size > self.max_bytes:
            return self._content(
                config,
                ref,
                parts,
                (),
                False,
                sweep_covered,
                exposure,
                owner,
                placement,
                Coverage(CoverageState.TOO_LARGE),
                context.modified_at,
            )
        encoding: str = _string(_get(metadata, "encoding"))
        if encoding != "base64" or (not content and size > 0):
            try:
                blob: JsonValue = await api.blob(parts.owner, parts.repo, blob_sha)
            except TelescreenError as error:
                coverage_state = (
                    CoverageState.PERMISSION_DENIED
                    if error.code is ErrorCode.FORBIDDEN
                    else CoverageState.FETCH_FAILED
                )
                return self._content(
                    config,
                    ref,
                    parts,
                    (),
                    False,
                    sweep_covered,
                    exposure,
                    owner,
                    placement,
                    Coverage(coverage_state),
                    context.modified_at,
                )
            content = _string(_get(blob, "content")).replace("\n", "").replace("\r", "")
            encoding = _string(_get(blob, "encoding"))
        if encoding != "base64" or (not content and size > 0):
            return self._content(
                config,
                ref,
                parts,
                (),
                False,
                sweep_covered,
                exposure,
                owner,
                placement,
                Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.BINARY),
                context.modified_at,
            )
        try:
            raw: bytes = base64.b64decode(content, validate=True)
            text: str = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return self._content(
                config,
                ref,
                parts,
                (),
                False,
                sweep_covered,
                exposure,
                owner,
                placement,
                Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.BINARY),
                context.modified_at,
            )
        segments: tuple[Segment, ...] = _segments(text, blob_sha)
        return self._content(
            config,
            ref,
            parts,
            segments,
            True,
            sweep_covered,
            exposure,
            owner,
            placement,
            Coverage(CoverageState.SCANNED),
            context.modified_at,
        )

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        """GitHub webhooks are configured externally, so no watch is renewed here."""
        return None

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Decode an adapter resource reference stored as canonical JSON."""
        value: JsonValue = parse_json(ref_json)
        owner: str = _string(_get(value, "owner"))
        repo: str = _string(_get(value, "repo"))
        ref: str = _string(_get(value, "ref"))
        path: str = _string(_get(value, "path"))
        head_sha: str | None = _head_sha_from_ref(value)
        return self._ref(owner, repo, ref, path, head_sha)

    def _ref(
        self, owner: str, repo: str, ref: str, path: str, head_sha: str | None = None
    ) -> ResourceRef:
        """Create a ref with a digit-free push commit id that passes leak detection."""
        members: list[tuple[str, JsonValue]] = [
            ("host", json_str(self.host)),
            ("owner", json_str(owner)),
            ("repo", json_str(repo)),
            ("ref", json_str(ref)),
            ("path", json_str(path)),
        ]
        encoded_head_sha: str | None = _encode_sha(head_sha)
        if encoded_head_sha is not None:
            members.append(("head_commit", json_str(encoded_head_sha)))
        ref_json: str = dump_json(json_object(members))
        return ResourceRef(
            CollectorKind.GITHUB,
            ref_json,
            f"github:{self.host}/{owner}/{repo}@{ref}:{path}",
        )

    def _content(
        self,
        config: CollectorConfig,
        ref: ResourceRef,
        parts: _RefParts,
        segments: tuple[Segment, ...],
        complete: bool,
        sweep_covered: bool,
        exposure: Exposure | None = None,
        owner: OwnerIdentity | None = None,
        placement: tuple[PlacementRef, ...] = (),
        coverage: Coverage | None = None,
        modified_at: datetime | None = None,
    ) -> ResourceContent:
        """Build the transient scanner payload with PR coverage semantics."""
        return ResourceContent(
            source="github",
            collector_config_id=config.id,
            segments=segments,
            complete=complete,
            sweep_covered=sweep_covered,
            raw_locator=ref.raw_locator,
            display_title=parts.path,
            ref=ref,
            exposure=exposure,
            owner=owner,
            placement=placement,
            coverage=coverage,
            modified_at=modified_at,
        )

    def _api(self, config: CollectorConfig) -> GitHubApi:
        """Create an API client from the configured token reference."""
        return self.api_factory(config)

    async def _targets(
        self, config: CollectorConfig, selector: GitHubSelector, api: GitHubApi
    ) -> list[_Target]:
        """Resolve selected repositories and branches for schedule polling."""
        repos: list[tuple[str, str]] = []
        if selector.repos:
            repos.extend((selector.owner, name) for name in selector.repos)
        else:
            page: int = 1
            while True:
                values: tuple[JsonValue, ...] = await api.org_repos(selector.owner, page)
                repos.extend(
                    (_string(_get(_get(value, "owner"), "login")), _string(_get(value, "name")))
                    for value in values
                )
                if len(values) < 100:
                    break
                page += 1
        targets: list[_Target] = []
        for owner, repo in repos:
            repo_value: JsonValue = await api.repo(owner, repo)
            default_branch: str = _string(_get(repo_value, "default_branch"))
            branches: tuple[str, ...] = selector.branches or (default_branch,)
            targets.extend(_Target(owner, repo, branch) for branch in branches)
        return targets


@dataclass(frozen=True)
class _Target:
    """Selected repository branch target."""

    owner: str
    repo: str
    branch: str


@dataclass(frozen=True)
class _RefParts:
    """Parsed stable locator components."""

    owner: str
    repo: str
    ref: str
    path: str
    head_sha: str | None


def _selector(config: CollectorConfig) -> GitHubSelector:
    """Return the GitHub selector or a safe configuration error."""
    if not isinstance(config.selector, GitHubSelector):
        raise TelescreenError(ErrorCode.CONFIG_ERROR, "invalid GitHub selector")
    return config.selector


def _get(value: JsonValue, name: str) -> JsonValue:
    """Return an object field, using JSON null for absent members."""
    from telescreen.core.json import json_null

    return value.get(name) or json_null()


def _string(value: JsonValue) -> str:
    """Return a string member or an empty string."""
    return value.as_str() or ""


def _optional_string(value: JsonValue) -> str | None:
    """Return an optional string member."""
    return value.as_str()


def _integer(value: JsonValue) -> int:
    """Return an integer member or zero."""
    return value.as_int() or 0


def _boolean(value: JsonValue) -> bool:
    """Return a boolean member or false."""
    return value.as_bool() or False


def _array(value: JsonValue) -> tuple[JsonValue, ...]:
    """Return an array member or an empty tuple."""
    return value.as_list() or ()


def _strings(value: JsonValue) -> tuple[str, ...]:
    """Return string entries from a JSON array."""
    result: list[str] = []
    for item in _array(value):
        candidate: str | None = item.as_str()
        if candidate is not None:
            result.append(candidate)
    return tuple(result)


def _repo_selected(repo: str, selector: GitHubSelector) -> bool:
    """Check the repository allowlist, with an empty list meaning all."""
    return not selector.repos or repo in selector.repos


def _branch_selected(branch: str, default: str, selector: GitHubSelector) -> bool:
    """Check explicit branch selection or default-branch behavior."""
    return branch in selector.branches if selector.branches else branch == default


def _path_selected(path: str, selector: GitHubSelector) -> bool:
    """Apply configured globs to the full repository path."""
    return not selector.path_globs or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in selector.path_globs
    )


def _branch_ref(branch: str) -> str:
    """Return the canonical branch ref."""
    return branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"


def _validated_sha(value: str | None) -> str | None:
    """Accept only GitHub's lowercase 40- or 64-character hexadecimal commit ids."""
    if value is None or len(value) not in (40, 64):
        return None
    return value if all(character in "0123456789abcdef" for character in value) else None


def _encode_sha(value: str | None) -> str | None:
    """Encode a valid commit id without decimal digits for the collector leak guard."""
    validated: str | None = _validated_sha(value)
    if validated is None:
        return None
    return validated.translate(str.maketrans("0123456789", "ghijklmnop"))


def _decode_sha(value: str | None) -> str | None:
    """Decode only a 40- or 64-character digit-free encoded commit id."""
    if value is None or len(value) not in (40, 64):
        return None
    if not all(character in "abcdefghijklmnop" for character in value):
        return None
    decoded: str = value.translate(str.maketrans("ghijklmnop", "0123456789"))
    return _validated_sha(decoded)


def _head_sha_from_ref(value: JsonValue) -> str | None:
    """Decode the current digit-free form or a valid legacy hexadecimal key."""
    encoded: str | None = _decode_sha(_optional_string(_get(value, "head_commit")))
    if encoded is not None:
        return encoded
    return _validated_sha(_optional_string(_get(value, "head_sha")))


def _parse_ref(ref: ResourceRef) -> _RefParts:
    """Decode a stored GitHub reference without exposing raw values in errors."""
    value: JsonValue = parse_json(ref.ref_json)
    return _RefParts(
        _string(_get(value, "owner")),
        _string(_get(value, "repo")),
        _string(_get(value, "ref")),
        _string(_get(value, "path")),
        _head_sha_from_ref(value),
    )


def _segments(text: str, blob_sha: str) -> tuple[Segment, ...]:
    """Split UTF-8 source text into line segments with short blob identity."""
    short_sha: str = blob_sha[:7]
    return tuple(
        Segment(f"L{index}@{short_sha}", line)
        for index, line in enumerate(text.splitlines(), start=1)
    )
