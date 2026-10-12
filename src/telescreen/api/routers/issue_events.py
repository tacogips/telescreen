"""Unregistered issue event selection, tags, and statistics routes."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.routers.issues_common import IssuesRouterDeps, _finding_json, _response
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    json_array,
    json_float,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import IssueRow
from telescreen.issues.detail.group import group_issue_ids
from telescreen.issues.detail.navigation import EventSelection, select_event
from telescreen.issues.detail.stats import StatBucket, issue_stats
from telescreen.issues.detail.tags import TagDistribution, tag_distribution
from telescreen.issues.query.period import Period, parse_period


def build_issue_events_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build direct-testable issue event routes; wiring belongs to p12."""
    router: APIRouter = APIRouter(tags=["issue-events"])

    @router.get("/api/issues/{issue_id}/events/{selector}")
    async def select_issue_event(
        issue_id: str,
        selector: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
    ) -> Response:
        """Select a finding and its chronological neighbors (B1)."""
        del principal
        async with deps.ctx.sessions() as session:
            issue_ids: tuple[str, ...] = await group_issue_ids(session, issue_id)
            selection: EventSelection = await select_event(session, issue_ids, selector)
            issue: IssueRow | None = await session.get(IssueRow, selection.event.issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return _response(
            json_object(
                (
                    ("event", _finding_json(selection.event)),
                    ("issue_short_id", json_str(f"TELE-{issue.number}")),
                    ("previous_id", _optional_string(selection.previous_id)),
                    ("next_id", _optional_string(selection.next_id)),
                    ("position", json_int(selection.position)),
                    ("total", json_int(selection.total)),
                )
            )
        )

    @router.get("/api/issues/{issue_id}/tags")
    async def issue_tags(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        period: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Response:
        """Return top tag values across all findings in the issue group (B7)."""
        del principal
        selected: Period = parse_period(period, start, end, ensure_utc(deps.ctx.clock.now()))
        async with deps.ctx.sessions() as session:
            issue_ids: tuple[str, ...] = await group_issue_ids(session, issue_id)
            grouped: bool = len(issue_ids) > 1
            result: TagDistribution = await tag_distribution(
                session, issue_ids, (selected.start, selected.end), grouped
            )
        return _response(
            json_object(
                (
                    ("total", json_int(result.total)),
                    (
                        "tags",
                        json_array(
                            tuple(
                                json_object(
                                    (
                                        ("key", json_str(tag.key)),
                                        (
                                            "values",
                                            json_array(
                                                tuple(
                                                    json_object(
                                                        (
                                                            ("value", json_str(value.value)),
                                                            ("count", json_int(value.count)),
                                                            ("percent", json_float(value.percent)),
                                                        )
                                                    )
                                                    for value in tag.values
                                                )
                                            ),
                                        ),
                                        ("other_count", json_int(tag.other_count)),
                                    )
                                )
                                for tag in result.tags
                            )
                        ),
                    ),
                )
            )
        )

    @router.get("/api/issues/{issue_id}/stats")
    async def issue_statistics(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        period: str | None = "14d",
        start: datetime | None = None,
        end: datetime | None = None,
        bucket: str = "day",
    ) -> Response:
        """Return zero-filled finding buckets over the merged issue group (B7)."""
        del principal
        now: datetime = ensure_utc(deps.ctx.clock.now())
        selected: Period = parse_period(period, start, end, now)
        lower: datetime = selected.start or now - timedelta(days=14)
        upper: datetime = selected.end or now
        async with deps.ctx.sessions() as session:
            issue_ids: tuple[str, ...] = await group_issue_ids(session, issue_id)
            buckets: tuple[StatBucket, ...] = await issue_stats(
                session, issue_ids, (lower, upper), bucket, now
            )
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(_stat_json(item) for item in buckets))),
                    ("total", json_int(sum(item.count for item in buckets))),
                )
            )
        )

    return router


def _optional_string(value: str | None) -> JsonValue:
    """Serialize an optional identifier."""
    return json_str(value) if value is not None else json_null()


def _stat_json(bucket: StatBucket) -> JsonValue:
    """Serialize one UTC stat bucket."""
    return json_object((("at", json_str(bucket.at.isoformat())), ("count", json_int(bucket.count))))
