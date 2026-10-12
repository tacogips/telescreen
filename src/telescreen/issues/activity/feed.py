"""Merged activity feed query for issue detail."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ActorDisplayKind, ErrorCode
from telescreen.db.models import IssueCommentRow, IssueHistoryRow, IssueRow
from telescreen.ports.directory import ActorDirectory, ActorDisplay


@dataclass(frozen=True)
class ActivityComment:
    """Safe comment projection for activity feed."""

    id: str
    body: str
    edited_at: datetime | None
    deleted: bool
    can_edit: bool


@dataclass(frozen=True)
class ActivityItem:
    """One history or comment activity record."""

    id: str
    type: str
    kind: str
    actor: ActorDisplay
    at: datetime
    from_value: str | None
    to_value: str | None
    note: str | None
    comment: ActivityComment | None


@dataclass(frozen=True)
class ActivityPage:
    """A newest-first activity page and its next cursor tuple."""

    items: tuple[ActivityItem, ...]
    next_cursor: tuple[str, str] | None


@dataclass(frozen=True)
class _RawActivity:
    """Internal merged history/comment entry before actor display resolution."""

    id: str
    type: str
    kind: str
    actor_ref: str
    at: datetime
    from_value: str | None
    to_value: str | None
    note: str | None
    comment: ActivityComment | None


async def activity_feed(
    session: AsyncSession,
    issue_id: str,
    cursor: tuple[str, str] | None,
    limit: int,
    actors: ActorDirectory | None,
    viewer_ref: str,
    can_manage_any: bool,
) -> ActivityPage:
    """Merge history and comments newest-first, replacing linked comment history."""
    issue: IssueRow | None = await session.get(IssueRow, issue_id)
    if issue is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
    histories: tuple[IssueHistoryRow, ...] = tuple(
        await session.scalars(
            select(IssueHistoryRow)
            .where(IssueHistoryRow.issue_id == issue_id)
            .order_by(IssueHistoryRow.at.desc(), IssueHistoryRow.id.desc())
        )
    )
    comments: tuple[IssueCommentRow, ...] = tuple(
        await session.scalars(
            select(IssueCommentRow)
            .where(IssueCommentRow.issue_id == issue_id)
            .order_by(IssueCommentRow.created_at.desc(), IssueCommentRow.id.desc())
        )
    )
    linked_comment_ids: set[str] = {
        row.to_value for row in histories if row.kind == "commented" and row.to_value is not None
    }
    raw: list[_RawActivity] = []
    for row in histories:
        if row.kind == "commented" and row.to_value is not None:
            continue
        legacy_comment: ActivityComment | None = None
        item_type: str = "history"
        if row.kind == "commented" and row.to_value is None:
            item_type = "comment"
            legacy_comment = ActivityComment(row.id, row.note or "", None, False, False)
        raw.append(
            _RawActivity(
                row.id,
                item_type,
                row.kind,
                row.actor_ref,
                ensure_utc(row.at),
                row.from_value,
                row.to_value,
                row.note,
                legacy_comment,
            )
        )
    for row in comments:
        if row.id in linked_comment_ids:
            history: IssueHistoryRow | None = next(
                (
                    item
                    for item in histories
                    if item.kind == "commented" and item.to_value == row.id
                ),
                None,
            )
            actor_ref: str = history.actor_ref if history is not None else row.author_ref
            kind: str = history.kind if history is not None else "commented"
        else:
            actor_ref = row.author_ref
            kind = "commented"
        projection: ActivityComment = ActivityComment(
            row.id,
            row.body,
            ensure_utc(row.edited_at) if row.edited_at is not None else None,
            row.deleted_at is not None,
            row.author_ref == viewer_ref or can_manage_any,
        )
        raw.append(
            _RawActivity(
                row.id,
                "comment",
                kind,
                actor_ref,
                ensure_utc(row.created_at),
                None,
                None,
                None,
                projection,
            )
        )
    raw.sort(key=lambda item: (item.at, item.id), reverse=True)
    if cursor is not None:
        marker_at: datetime = ensure_utc(datetime.fromisoformat(cursor[0]))
        raw = [item for item in raw if (item.at, item.id) < (marker_at, cursor[1])]
    page: list[_RawActivity] = raw[: limit + 1]
    has_more: bool = len(page) > limit
    selected: list[_RawActivity] = page[:limit]
    refs_list: list[str] = []
    for item in selected:
        # Every actor is resolved (system and token actors too); history values only when
        # they are member or team references.
        if item.actor_ref not in refs_list:
            refs_list.append(item.actor_ref)
        for ref in (item.from_value, item.to_value):
            if ref is not None and ref.startswith(("member:", "team:")) and ref not in refs_list:
                refs_list.append(ref)
    refs: tuple[str, ...] = tuple(refs_list)
    displays: tuple[ActorDisplay, ...] = (
        await actors.display(session, refs) if actors is not None and refs else ()
    )
    items: list[ActivityItem] = []
    for item in selected:
        actor: ActorDisplay = next(
            (display for display in displays if display.ref == item.actor_ref),
            ActorDisplay(item.actor_ref, _actor_kind(item.actor_ref), "Unknown", None, False),
        )
        items.append(
            ActivityItem(
                item.id,
                item.type,
                item.kind,
                actor,
                item.at,
                _display_value(item.from_value, displays),
                _display_value(item.to_value, displays),
                item.note,
                item.comment,
            )
        )
    next_cursor: tuple[str, str] | None = None
    if has_more and selected:
        last: _RawActivity = selected[-1]
        next_cursor = (last.at.isoformat(), last.id)
    return ActivityPage(tuple(items), next_cursor)


def _actor_kind(ref: str) -> ActorDisplayKind:
    """Choose a safe actor category for unresolved references."""
    if ref.startswith("member:"):
        return ActorDisplayKind.MEMBER
    if ref.startswith("team:"):
        return ActorDisplayKind.TEAM
    return ActorDisplayKind.SYSTEM


def _display_value(value: str | None, displays: tuple[ActorDisplay, ...]) -> str | None:
    """Resolve member and team refs before showing history values."""
    if value is None or not value.startswith(("member:", "team:")):
        return value
    actor: ActorDisplay | None = next((item for item in displays if item.ref == value), None)
    return actor.display_name if actor is not None else "Unknown"


def _kind_for(history_id: str, histories: tuple[IssueHistoryRow, ...]) -> str:
    """Return the persisted history kind for a feed item."""
    row: IssueHistoryRow | None = next((item for item in histories if item.id == history_id), None)
    return row.kind if row is not None else "activity"
