"""Unregistered issue external-link and GitHub creation routes."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.issues_common import _response
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_bool, json_null, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import ExternalIssueLinkRow, IssueRow
from telescreen.issues.external.github import GithubIssueCreator
from telescreen.issues.external.links import ExternalLinkService


@pydantic.dataclasses.dataclass(frozen=True)
class ExternalLinkBody:
    """User-provided external link fields."""

    url: str
    title: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class GithubIssueBody:
    """Allow-listed repository and optional issue title."""

    repo: str
    title: str | None = None


@dataclass(frozen=True)
class IssueExternalRouterDeps:
    """Dependencies for external issue routes."""

    ctx: ApiContext
    links: ExternalLinkService
    github: GithubIssueCreator | None
    github_repos: tuple[str, ...]


def build_issue_external_router(deps: IssueExternalRouterDeps) -> APIRouter:
    """Build direct-testable external issue routes; wiring belongs to p12."""
    router: APIRouter = APIRouter(tags=["issue-external"])

    @router.get("/api/issues/{issue_id}/external-issues")
    async def list_external(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
    ) -> Response:
        """List external issue links for a Telescreen issue."""
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[ExternalIssueLinkRow, ...] = await deps.links.list(session, issue_id)
        return _response(
            json_object((("items", json_array(tuple(_link_json(row) for row in rows))),))
        )

    @router.get("/api/external-issues/github/repos")
    async def github_repositories(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
    ) -> Response:
        """Return GitHub repository configuration without revealing credentials."""
        del principal
        configured: bool = (
            deps.github is not None and bool(deps.github.token_ref) and bool(deps.github_repos)
        )
        repos: tuple[JsonValue, ...] = (
            tuple(json_str(repo) for repo in deps.github_repos) if configured else ()
        )
        return _response(
            json_object((("configured", json_bool(configured)), ("repos", json_array(repos))))
        )

    @router.post("/api/issues/{issue_id}/external-issues", status_code=201)
    async def create_external(
        issue_id: str,
        body: ExternalLinkBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EXTERNAL_ISSUES_WRITE))
        ],
    ) -> Response:
        """Create an HTTPS external issue link."""
        async with deps.ctx.sessions.begin() as session:
            row: ExternalIssueLinkRow = await deps.links.link(
                session, issue_id, body.url, body.title, principal.actor_ref()
            )
        return _response(_link_json(row), status_code=201)

    @router.delete("/api/issues/{issue_id}/external-issues/{link_id}")
    async def delete_external(
        issue_id: str,
        link_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EXTERNAL_ISSUES_WRITE))
        ],
    ) -> Response:
        """Delete one external link from the requested issue."""
        async with deps.ctx.sessions.begin() as session:
            await deps.links.unlink(session, issue_id, link_id, principal.actor_ref())
        return _response(json_object((("deleted", json_bool(True)),)))

    @router.post("/api/issues/{issue_id}/external-issues/github", status_code=201)
    async def create_github(
        issue_id: str,
        body: GithubIssueBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EXTERNAL_ISSUES_WRITE))
        ],
    ) -> Response:
        """Create a GitHub issue from masked, allow-listed issue fields."""
        if deps.github is None:
            return _error(503, "integration_unconfigured")
        row: ExternalIssueLinkRow | None = None
        async with deps.ctx.sessions.begin() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            try:
                row: ExternalIssueLinkRow = await deps.github.create(
                    session, issue, body.repo, body.title, principal.actor_ref()
                )
            except TelescreenError as error:
                if error.code is ErrorCode.CONFIG_ERROR:
                    return _error(503, "integration_unconfigured")
                if error.detail == "dlp_blocked":
                    return _error(422, "dlp_blocked")
                raise
        if row is None:
            raise TelescreenError(ErrorCode.INTERNAL_ERROR, "external issue creation failed")
        return _response(_link_json(row), status_code=201)

    return router


def _link_json(row: ExternalIssueLinkRow) -> JsonValue:
    """Serialize an external issue link."""
    return json_object(
        (
            ("id", json_str(row.id)),
            ("kind", json_str(row.kind)),
            ("url", json_str(row.url)),
            ("title", json_str(row.title)),
            ("external_ref", json_str(row.external_ref) if row.external_ref else json_null()),
        )
    )


def _error(status_code: int, code: str) -> JSONResponse:
    """Return a stable service error code without input details."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code}})
