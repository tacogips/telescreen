"""Issue stream status counts API route."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.issues_common import _response
from telescreen.core.clock import ensure_utc
from telescreen.core.json import json_array, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import UNRESOLVED_STATUSES, IssueStatus
from telescreen.db.models import IssueRow
from telescreen.issues.query.filters import QueryContext, build_filters
from telescreen.issues.query.model import ParsedQuery, QueryError, QueryKey
from telescreen.issues.query.parser import parse_issue_query
from telescreen.issues.query.period import Period, parse_period
from telescreen.ports.directory import OwnerDirectory, RecipientResolver


@dataclass(frozen=True)
class IssueCountsRouterDeps:
    """Dependencies required by the issue counts route."""

    ctx: ApiContext
    resolver: RecipientResolver
    owners: OwnerDirectory | None = None


def build_issue_counts_router(deps: IssueCountsRouterDeps) -> APIRouter:
    """Build the unregistered counts router for later API assembly."""
    router: APIRouter = APIRouter()

    @router.get("/api/issues/counts")
    async def issue_counts(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        query: str = "",
        period: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        updated_since: datetime | None = None,
    ) -> Response:
        """Return status tab counts for the caller's matching issue stream."""
        now: datetime = ensure_utc(deps.ctx.clock.now())
        try:
            parsed: ParsedQuery = parse_issue_query(query)
            selected_period: Period = parse_period(period, start, end, now)
        except QueryError as error:
            return _query_error(error)
        context: QueryContext = QueryContext(principal.member_id, deps.resolver, deps.owners, now)
        base_parsed: ParsedQuery = ParsedQuery(
            tuple(term for term in parsed.terms if term.key not in (QueryKey.IS, QueryKey.STATUS)),
            parsed.text,
        )
        updated_filters: tuple[ColumnElement[bool], ...]
        async with deps.ctx.sessions() as session:
            base_filters: tuple[ColumnElement[bool], ...] = await build_filters(
                session, base_parsed, context
            )
            updated_filters = await build_filters(session, parsed, context)
            tabs: list[tuple[str, int]] = []
            tab_specs: tuple[tuple[str, tuple[str, ...], bool], ...] = (
                ("unresolved", tuple(item.value for item in UNRESOLVED_STATUSES), False),
                ("for_review", tuple(item.value for item in UNRESOLVED_STATUSES), True),
                ("regressed", (IssueStatus.REGRESSED.value,), False),
                ("escalating", (IssueStatus.ESCALATING.value,), False),
                ("archived", (IssueStatus.ARCHIVED.value,), False),
                ("resolved", (IssueStatus.RESOLVED.value,), False),
            )
            for tab, statuses, needs_review in tab_specs:
                tab_filters: list[ColumnElement[bool]] = [
                    *base_filters,
                    IssueRow.status.in_(statuses),
                    IssueRow.merged_into_id.is_(None),
                ]
                if needs_review:
                    tab_filters.append(IssueRow.needs_review.is_(True))
                _add_period(tab_filters, selected_period)
                count: int = int(
                    await session.scalar(
                        select(func.count()).select_from(IssueRow).where(*tab_filters)
                    )
                    or 0
                )
                tabs.append((tab, count))
            since_count: int = 0
            if updated_since is not None:
                since_filters: list[ColumnElement[bool]] = [
                    *updated_filters,
                    IssueRow.merged_into_id.is_(None),
                    IssueRow.updated_at > ensure_utc(updated_since),
                ]
                _add_period(since_filters, selected_period)
                since_count = int(
                    await session.scalar(
                        select(func.count()).select_from(IssueRow).where(*since_filters)
                    )
                    or 0
                )
        return _response(
            json_object(
                (
                    (
                        "tabs",
                        json_array(
                            tuple(
                                json_object((("tab", json_str(tab)), ("count", json_int(count))))
                                for tab, count in tabs
                            )
                        ),
                    ),
                    ("updated_since_count", json_int(since_count)),
                )
            )
        )

    return router


def _add_period(filters: list[ColumnElement[bool]], period: Period) -> None:
    """Apply overlap semantics to a finding-time period."""
    if period.start is not None:
        filters.append(IssueRow.last_seen >= period.start)
    if period.end is not None:
        filters.append(IssueRow.first_seen <= period.end)


def _query_error(error: QueryError) -> Response:
    """Return a query error response without echoing request text."""

    return Response(
        content=(
            '{"error":{"code":"invalid_query","detail":"invalid query",'
            f'"token_index":{error.token_index},"reason":"{error.reason.value}"}}'
        ),
        status_code=422,
        media_type="application/json",
    )
