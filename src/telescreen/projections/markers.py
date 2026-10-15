"""ORM-only change markers for directory and issue projections."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.orm.unitofwork import UOWTransaction

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.db.models import (
    ApiTokenRow,
    CollectorConfigRow,
    IssueRow,
    MemberIdentityRow,
    MemberRow,
    OwnershipRuleRow,
    ProjectionChangeRow,
    TeamMembershipRow,
    TeamRow,
)

type WatchedModel = (
    ApiTokenRow
    | CollectorConfigRow
    | IssueRow
    | MemberIdentityRow
    | MemberRow
    | OwnershipRuleRow
    | TeamMembershipRow
    | TeamRow
)
type BeforeFlush = Callable[[Session, UOWTransaction, Sequence[WatchedModel] | None], None]


@dataclass(frozen=True)
class _ListenerRegistration:
    sync_class: type[Session]
    listener: BeforeFlush


_LISTENERS: list[_ListenerRegistration] = []
_ISSUE_FIELDS: tuple[str, ...] = ("resource_key", "number", "status", "owner_ref")
_MEMBER_FIELDS: tuple[str, ...] = (
    "handle",
    "display_name",
    "active",
    "role",
    "email",
    "slack_user_id",
)
_ALL_MODELS: tuple[type[WatchedModel], ...] = (
    TeamRow,
    TeamMembershipRow,
    MemberIdentityRow,
    ApiTokenRow,
    OwnershipRuleRow,
    CollectorConfigRow,
)


def install_projection_markers(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    """Install a before-flush listener that only marks sessions from this factory."""
    existing_class: type[Session] | None = sessionmaker.kw.get("sync_session_class")
    if existing_class is not None and any(
        listener.sync_class is existing_class for listener in _LISTENERS
    ):
        return
    base_class: type[Session] = existing_class or Session
    sync_class: type[Session] = type("ProjectionMarkerSession", (base_class,), {})
    sessionmaker.configure(sync_session_class=sync_class)

    def before_flush(
        session: Session, flush_context: UOWTransaction, instances: Sequence[WatchedModel] | None
    ) -> None:
        del flush_context, instances
        pending: tuple[WatchedModel, ...] = tuple(
            item
            for item in tuple(session.new) + tuple(session.deleted) + tuple(session.dirty)
            if isinstance(item, _ALL_MODELS + (IssueRow, MemberRow))
            and (item in session.new or item in session.deleted or _changed_projection(item))
        )
        for item in pending:
            marker: tuple[str, str] | None = _marker_identity(item)
            if marker is None:
                continue
            kind: str
            ref: str
            kind, ref = marker
            session.add(
                ProjectionChangeRow(id=new_id(), kind=kind, entity_ref=ref, recorded_at=clock.now())
            )

    registration: _ListenerRegistration = _ListenerRegistration(sync_class, before_flush)
    event.listen(sync_class, "before_flush", before_flush)
    _LISTENERS.append(registration)


def uninstall_projection_markers(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Remove this factory's listener for isolated tests."""
    sync_class: type[Session] = sessionmaker.kw["sync_session_class"]
    registration: _ListenerRegistration | None = next(
        (listener for listener in _LISTENERS if listener.sync_class is sync_class), None
    )
    if registration is not None:
        event.remove(sync_class, "before_flush", registration.listener)
        _LISTENERS.remove(registration)


def _changed_projection(item: WatchedModel) -> bool:
    if isinstance(item, IssueRow):
        names: tuple[str, ...] = _ISSUE_FIELDS
    elif isinstance(item, MemberRow):
        names = _MEMBER_FIELDS
    elif isinstance(item, _ALL_MODELS):
        names = tuple(item.__table__.columns.keys())
    else:
        return False
    return any(inspect(item).attrs[name].history.has_changes() for name in names)


def _marker_identity(item: WatchedModel) -> tuple[str, str] | None:
    if isinstance(item, IssueRow):
        return "issue_link", f"issue:{item.id}"
    if isinstance(item, MemberRow):
        return "directory", f"member:{item.id}"
    if isinstance(item, TeamRow):
        return "directory", f"team:{item.id}"
    if isinstance(item, TeamMembershipRow):
        return "directory", f"team:{item.team_id}"
    if isinstance(item, MemberIdentityRow):
        return "directory", f"identity:{item.id}"
    if isinstance(item, ApiTokenRow):
        return "directory", f"api_token:{item.id}"
    if isinstance(item, OwnershipRuleRow):
        return "directory", "ownership_rules:rules"
    if isinstance(item, CollectorConfigRow):
        return "directory", f"collector:{item.id}"
    return None
