"""Typed HTTP boundary for GitHub and GitHub Enterprise APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote, urljoin

import httpx

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_object, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.collectors import RateLimited, ResourceGoneError


class GitHubNotFound(Exception):
    """A GitHub endpoint returned 404 where rewrite fallback is supported."""


@dataclass(frozen=True)
class GitHubApi:
    """Call the GitHub REST API without exposing its access token in repr."""

    http: httpx.AsyncClient = field(repr=False)
    base_url: str
    token: SecretValue = field(repr=False)

    async def repo(self, owner: str, name: str) -> JsonValue:
        """Return repository metadata."""
        return await self._get(f"repos/{quote(owner)}/{quote(name)}")

    async def repo_by_id(self, repo_id: int) -> JsonValue:
        """Return repository metadata by immutable numeric id."""
        return await self._get(f"repositories/{repo_id}")

    async def org_repos(self, owner: str, page: int) -> tuple[JsonValue, ...]:
        """Return one organization repository page, with user fallback on 404."""
        endpoint: str = f"orgs/{quote(owner)}/repos?per_page=100&page={page}"
        response: httpx.Response = await self._request("GET", endpoint)
        if response.status_code == 404:
            response = await self._request(
                "GET", f"users/{quote(owner)}/repos?per_page=100&page={page}"
            )
        value: JsonValue = self._decode(response)
        values: tuple[JsonValue, ...] | None = value.as_list()
        if values is None:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "invalid GitHub response")
        return values

    async def branch(self, owner: str, repo: str, branch: str) -> JsonValue:
        """Return branch metadata including its current head commit."""
        return await self._get(
            f"repos/{quote(owner)}/{quote(repo)}/branches/{quote(branch, safe='')}"
        )

    async def compare(self, owner: str, repo: str, base: str, head: str) -> JsonValue:
        """Compare two commits or branches."""
        endpoint: str = (
            f"repos/{quote(owner)}/{quote(repo)}/compare/"
            f"{quote(base, safe='')}...{quote(head, safe='')}?per_page=100"
        )
        response: httpx.Response = await self._request("GET", endpoint)
        if response.status_code == 404:
            raise GitHubNotFound
        value: JsonValue = self._decode(response)
        files: tuple[JsonValue, ...] = await self._pages_from_response(response, value)
        files_value: JsonValue = json_array(files)
        members: list[tuple[str, JsonValue]] = []
        for member in value.members:
            members.append((member.name, files_value if member.name == "files" else member.value))
        if not any(name == "files" for name, _ in members):
            members.append(("files", files_value))
        return json_object(members)

    async def tree(self, owner: str, repo: str, sha: str, recursive: bool = True) -> JsonValue:
        """Return a Git tree, optionally recursively expanded."""
        suffix: str = "?recursive=1" if recursive else ""
        return await self._get(f"repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(sha)}{suffix}")

    async def contents(self, owner: str, repo: str, path: str, ref: str) -> JsonValue:
        """Return content metadata at a ref, including encoded file content."""
        endpoint: str = (
            f"repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}"
            f"?ref={quote(ref, safe='')}"
        )
        response: httpx.Response = await self._request("GET", endpoint)
        if response.status_code == 404:
            raise ResourceGoneError
        return self._decode(response)

    async def blob(self, owner: str, repo: str, sha: str) -> JsonValue:
        """Return blob metadata and base64 content."""
        response: httpx.Response = await self._request(
            "GET", f"repos/{quote(owner)}/{quote(repo)}/git/blobs/{quote(sha)}"
        )
        if response.status_code == 404:
            raise ResourceGoneError
        return self._decode(response)

    async def pull_files(self, owner: str, repo: str, number: int) -> tuple[JsonValue, ...]:
        """Return every file changed by a pull request."""
        endpoint: str = f"repos/{quote(owner)}/{quote(repo)}/pulls/{number}/files?per_page=100"
        return await self._pages(endpoint)

    async def tree_pages(self, owner: str, repo: str, sha: str) -> tuple[JsonValue, ...]:
        """Return a recursive tree listing as individual entry values."""
        value: JsonValue = await self.tree(owner, repo, sha)
        entries: tuple[JsonValue, ...] | None = _member(value, "tree").as_list()
        if entries is None:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "invalid GitHub response")
        return entries

    async def _get(self, endpoint: str) -> JsonValue:
        """Fetch and decode one JSON resource."""
        response: httpx.Response = await self._request("GET", endpoint)
        return self._decode(response)

    async def _pages(self, endpoint: str) -> tuple[JsonValue, ...]:
        """Follow GitHub's Link rel=next pages while retaining typed JSON values."""
        items: list[JsonValue] = []
        next_url: str | None = urljoin(self.base_url.rstrip("/") + "/", endpoint)
        while next_url is not None:
            response: httpx.Response = await self._request("GET", next_url)
            page: JsonValue = self._decode(response)
            values: tuple[JsonValue, ...] | None = page.as_list()
            if values is None:
                raise TelescreenError(ErrorCode.FETCH_FAILED, "invalid GitHub response")
            items.extend(values)
            next_url = _next_link(response.headers.get("link"))
            if next_url is not None:
                next_url = urljoin(str(response.url), next_url)
        return tuple(items)

    async def _pages_from_response(
        self, response: httpx.Response, first_page: JsonValue
    ) -> tuple[JsonValue, ...]:
        """Collect an embedded compare file list across Link pages."""
        result: list[JsonValue] = list(_member(first_page, "files").as_list() or ())
        next_url: str | None = _next_link(response.headers.get("link"))
        while next_url is not None:
            page_response: httpx.Response = await self._request(
                "GET", urljoin(str(response.url), next_url)
            )
            page: JsonValue = self._decode(page_response)
            result.extend(_member(page, "files").as_list() or ())
            next_url = _next_link(page_response.headers.get("link"))
            response = page_response
        return tuple(result)

    async def _request(self, method: str, endpoint: str) -> httpx.Response:
        """Send an authenticated request and translate rate-limit responses."""
        url: str = (
            endpoint
            if endpoint.startswith("https://") or endpoint.startswith("http://")
            else urljoin(self.base_url.rstrip("/") + "/", endpoint)
        )
        try:
            response: httpx.Response = await self.http.request(
                method,
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token.value}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "GitHub request failed") from None
        if response.status_code == 403 or response.status_code == 429:
            remaining: str | None = response.headers.get("x-ratelimit-remaining")
            retry_after: str | None = response.headers.get("retry-after")
            if response.status_code == 429 or remaining == "0" or retry_after is not None:
                seconds: float | None = _float_or_none(retry_after)
                raise RateLimited(seconds)
        if response.status_code >= 400:
            return response
        return response

    def _decode(self, response: httpx.Response) -> JsonValue:
        """Decode a successful response or raise a static, value-safe error."""
        if response.status_code == 404:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "GitHub resource not found")
        if response.status_code >= 400:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "GitHub request failed")
        return parse_json(response.content)


def _member(value: JsonValue, name: str) -> JsonValue:
    """Return an object member or a JSON null sentinel."""
    from telescreen.core.json import json_null

    return value.get(name) or json_null()


def _next_link(header: str | None) -> str | None:
    """Select the next URL from an RFC 5988 Link header."""
    if header is None:
        return None
    for item in header.split(","):
        pieces: tuple[str, ...] = tuple(part.strip() for part in item.split(";"))
        if len(pieces) > 1 and any(part == 'rel="next"' for part in pieces[1:]):
            target: str = pieces[0]
            if target.startswith("<") and target.endswith(">"):
                return target[1:-1]
    return None


def _float_or_none(value: str | None) -> float | None:
    """Parse a Retry-After value without including it in error output."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
