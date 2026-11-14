"""Bulk issue, merge, and unmerge API routes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.issues_common import _issue_json, _response
from telescreen.core.clock import ensure_utc
from telescreen.core.json import json_array, json_bool, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ArchiveKind,
    IssuePriority,
    IssueResolution,
    IssueStatus,
    RemediationAction,
)
from telescreen.db.models import IssueRow
from telescreen.issues.archive import ArchivePolicy
from telescreen.issues.bulk import (
    BulkAction,
    BulkArchiveParams,
    BulkParams,
    BulkRequest,
    BulkResult,
    BulkService,
    BulkVerificationParams,
)
from telescreen.issues.merge import MergeService
from telescreen.issues.query.filters import QueryContext
from telescreen.issues.query.period import Period, parse_period
from telescreen.ports.directory import OwnerDirectory, RecipientResolver
from telescreen.ports.issues import IssueActivityNotifier
from telescreen.ports.search import SearchIndexer


@pydantic.dataclasses.dataclass(frozen=True)
class BulkArchiveBody:
    """Optional archive condition in a bulk request."""

    kind: ArchiveKind | None = None
    duration_minutes: int | None = None
    count: int | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class BulkVerificationBody:
    """Verification request options in a bulk request."""

    action: RemediationAction
    include_linked: bool = False


@pydantic.dataclasses.dataclass(frozen=True)
class BulkParamsBody:
    """Action parameters accepted by the bulk API."""

    status: IssueStatus | None = None
    assignee_ref: str | None = None
    priority: IssuePriority | None = None
    archive: BulkArchiveBody | None = None
    reason: IssueResolution | None = None
    note: str | None = None
    verification: BulkVerificationBody | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class BulkBody:
    """Issue selection, period, action, and parameters."""

    action: BulkAction
    params: BulkParamsBody = BulkParamsBody()
    ids: tuple[str, ...] | None = None
    query: str | None = None
    period: str | None = None
    start: datetime | None = None
    end: datetime | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class MergeBody:
    """Primary issue and finite selected issue ids."""

    primary_id: str
    ids: tuple[str, ...]


@pydantic.dataclasses.dataclass(frozen=True)
class UnmergeBody:
    """Selected child ids to unmerge; an empty tuple means all children."""

    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IssueBulkRouterDeps:
    """Dependencies needed by the bulk issue routes."""

    ctx: ApiContext
    bulk: BulkService
    merge: MergeService
    indexer: SearchIndexer
    resolver: RecipientResolver
    owners: OwnerDirectory | None = None
    archive_policy: ArchivePolicy = ArchivePolicy()
    activity: IssueActivityNotifier | None = None


def build_issue_bulk_router(deps: IssueBulkRouterDeps) -> APIRouter:
    """Build bulk, merge, and unmerge routes without nested router inclusion."""
    router: APIRouter = APIRouter()

    @router.post("/api/issues/bulk")
    async def bulk(
        body: BulkBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Apply one validated action to an explicit or query-selected issue set."""
        selected_period: Period | None = None
        if body.query is not None:
            selected_period = parse_period(
                body.period, body.start, body.end, ensure_utc(deps.ctx.clock.now())
            )
        archive: BulkArchiveParams | None = None
        if body.params.archive is not None and body.params.archive.kind is not None:
            archive = BulkArchiveParams(
                body.params.archive.kind,
                body.params.archive.duration_minutes,
                body.params.archive.count,
            )
        verification: BulkVerificationParams | None = None
        if body.params.verification is not None:
            verification = BulkVerificationParams(
                body.params.verification.action, body.params.verification.include_linked
            )
        params: BulkParams = BulkParams(
            body.params.status,
            body.params.assignee_ref,
            body.params.priority,
            archive,
            body.params.reason,
            body.params.note,
            verification,
        )
        request: BulkRequest = BulkRequest(
            body.ids, body.query, selected_period, body.action, params
        )
        query_context: QueryContext = QueryContext(
            principal.member_id,
            deps.resolver,
            deps.owners,
            ensure_utc(deps.ctx.clock.now()),
        )
        result: BulkResult = await deps.bulk.run(
            request,
            principal.actor_ref(),
            principal.allows(Permission.ISSUES_RESOLVE_UNVERIFIED),
            query_context,
        )
        return _response(
            json_object(
                (
                    ("matched", json_int(result.matched)),
                    ("processed", json_int(result.processed)),
                    ("truncated", json_bool(result.truncated)),
                    (
                        "succeeded",
                        json_array(tuple(json_str(issue_id) for issue_id in result.succeeded)),
                    ),
                    (
                        "failed",
                        json_array(
                            tuple(
                                json_object(
                                    (
                                        ("issue_id", json_str(item.issue_id)),
                                        ("code", json_str(item.code.value)),
                                    )
                                )
                                for item in result.failed
                            )
                        ),
                    ),
                )
            )
        )

    @router.post("/api/issues/merge")
    async def merge(
        body: MergeBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Merge a finite selected set and return its primary issue."""
        issue: IssueRow = await deps.merge.merge(body.primary_id, body.ids, principal.actor_ref())
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/unmerge")
    async def unmerge(
        issue_id: str,
        body: UnmergeBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Unmerge selected children or all children and return the primary issue."""
        issue: IssueRow = await deps.merge.unmerge(issue_id, body.ids, principal.actor_ref())
        return _response(_issue_json(issue))

    return router
