"""Persistence and policy for server-side saved issue views."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_array, json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode, SavedViewVisibility
from telescreen.db.models import SavedViewDefaultRow, SavedViewRow, SavedViewStarRow
from telescreen.issues.query.model import QueryError
from telescreen.issues.query.parser import parse_issue_query
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog

_SORTS: tuple[str, ...] = (
    "last_seen",
    "first_seen",
    "times_seen",
    "values",
    "risk",
    "priority",
    "trending",
)
_PERIODS: tuple[str, ...] = ("all", "24h", "7d", "14d", "30d", "custom")


@dataclass(frozen=True)
class SavedViewInput:
    """Validated candidate values for a saved issue view."""

    name: str
    query: str
    sort: str
    period: str | None
    visibility: SavedViewVisibility


@dataclass(frozen=True)
class SavedViewSummary:
    """View row decorated with the caller's star and default state."""

    row: SavedViewRow
    starred: bool
    is_default: bool


@dataclass(frozen=True)
class SavedViewService:
    """Manage saved views inside caller-owned database transactions."""

    redactor: Redactor
    audit: AuditLog
    clock: Clock

    async def list(self, session: AsyncSession, member_id: str) -> tuple[SavedViewSummary, ...]:
        """Return personal and shared views with caller-specific state."""
        rows: list[SavedViewRow] = list(
            (
                await session.scalars(
                    select(SavedViewRow)
                    .where(
                        or_(
                            SavedViewRow.visibility == SavedViewVisibility.SHARED.value,
                            SavedViewRow.owner_member_id == member_id,
                        )
                    )
                    .order_by(SavedViewRow.position, SavedViewRow.name, SavedViewRow.id)
                )
            ).all()
        )
        stars: set[str] = set(
            (
                await session.scalars(
                    select(SavedViewStarRow.view_id).where(SavedViewStarRow.member_id == member_id)
                )
            ).all()
        )
        default_id: str | None = await session.scalar(
            select(SavedViewDefaultRow.view_id).where(SavedViewDefaultRow.member_id == member_id)
        )
        return tuple(SavedViewSummary(row, row.id in stars, row.id == default_id) for row in rows)

    async def create(
        self,
        session: AsyncSession,
        member_id: str,
        candidate: SavedViewInput,
        actor_ref: str,
        manage_shared: bool,
    ) -> SavedViewRow:
        """Validate and persist a new saved view."""
        await self._validate(candidate, session, actor_ref)
        self._require_shared(candidate.visibility, manage_shared)
        count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(SavedViewRow)
                .where(SavedViewRow.visibility == candidate.visibility.value)
                if candidate.visibility is SavedViewVisibility.SHARED
                else select(func.count())
                .select_from(SavedViewRow)
                .where(
                    SavedViewRow.visibility == SavedViewVisibility.PERSONAL.value,
                    SavedViewRow.owner_member_id == member_id,
                )
            )
            or 0
        )
        limit: int = 100 if candidate.visibility is SavedViewVisibility.SHARED else 50
        if count >= limit:
            raise TelescreenError(ErrorCode.LIMIT_EXCEEDED, "saved view limit exceeded")
        now: datetime = self.clock.now()
        row: SavedViewRow = SavedViewRow(
            id=new_id(),
            name=candidate.name.strip(),
            query=candidate.query,
            sort=candidate.sort,
            period=candidate.period,
            visibility=candidate.visibility.value,
            owner_member_id=member_id,
            position=count,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await self._audit(session, actor_ref, "view.created", row.id, row.visibility)
        return row

    async def update(
        self,
        session: AsyncSession,
        view_id: str,
        member_id: str,
        candidate: SavedViewInput,
        actor_ref: str,
        manage_shared: bool,
    ) -> SavedViewRow:
        """Replace a saved view after enforcing ownership or shared permission."""
        await self._validate(candidate, session, actor_ref)
        row: SavedViewRow = await self._required(session, view_id)
        self._authorize(row, member_id, manage_shared)
        if row.visibility != candidate.visibility.value:
            self._require_shared(candidate.visibility, manage_shared)
            await self._enforce_limit(session, candidate.visibility, member_id, row.id)
        row.name = candidate.name.strip()
        row.query = candidate.query
        row.sort = candidate.sort
        row.period = candidate.period
        row.visibility = candidate.visibility.value
        row.updated_at = self.clock.now()
        await self._audit(session, actor_ref, "view.updated", row.id, row.visibility)
        return row

    async def delete(
        self,
        session: AsyncSession,
        view_id: str,
        member_id: str,
        actor_ref: str,
        manage_shared: bool,
    ) -> None:
        """Delete a view and its star/default references."""
        row: SavedViewRow = await self._required(session, view_id)
        self._authorize(row, member_id, manage_shared)
        await session.execute(delete(SavedViewStarRow).where(SavedViewStarRow.view_id == view_id))
        await session.execute(
            delete(SavedViewDefaultRow).where(SavedViewDefaultRow.view_id == view_id)
        )
        await session.delete(row)
        await self._audit(session, actor_ref, "view.deleted", row.id, row.visibility)

    async def star(self, session: AsyncSession, view_id: str, member_id: str) -> None:
        """Star a view visible to the caller."""
        await self._visible(session, view_id, member_id)
        existing: SavedViewStarRow | None = await session.get(
            SavedViewStarRow, (view_id, member_id)
        )
        if existing is None:
            session.add(SavedViewStarRow(view_id=view_id, member_id=member_id))

    async def unstar(self, session: AsyncSession, view_id: str, member_id: str) -> None:
        """Remove a caller's star from a view."""
        await session.execute(
            delete(SavedViewStarRow).where(
                SavedViewStarRow.view_id == view_id, SavedViewStarRow.member_id == member_id
            )
        )

    async def set_default(self, session: AsyncSession, member_id: str, view_id: str | None) -> None:
        """Set or clear the caller's default view."""
        existing: SavedViewDefaultRow | None = await session.get(SavedViewDefaultRow, member_id)
        if view_id is None:
            if existing is not None:
                await session.delete(existing)
            return
        await self._visible(session, view_id, member_id)
        if existing is None:
            session.add(SavedViewDefaultRow(member_id=member_id, view_id=view_id))
        else:
            existing.view_id = view_id

    async def _validate(
        self, candidate: SavedViewInput, session: AsyncSession, actor_ref: str
    ) -> None:
        """Validate user text and saved-view query options without exposing content."""
        detected: list[str] = []
        if self.redactor.has_hits(candidate.name):
            detected.append("name")
        if self.redactor.has_hits(candidate.query):
            detected.append("query")
        if detected:
            await self._rejected(session, actor_ref, tuple(detected))
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "raw value detected")
        if not 1 <= len(candidate.name.strip()) <= 64:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid saved view name")
        try:
            parse_issue_query(candidate.query)
        except QueryError:
            raise TelescreenError(ErrorCode.INVALID_QUERY, "invalid query") from None
        if candidate.sort not in _SORTS or candidate.period not in (None, *_PERIODS):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid saved view options")

    async def _enforce_limit(
        self,
        session: AsyncSession,
        visibility: SavedViewVisibility,
        member_id: str,
        excluded_view_id: str,
    ) -> None:
        """Enforce the destination visibility cap while converting an existing view."""
        statement: Select[tuple[int]] = (
            select(func.count())
            .select_from(SavedViewRow)
            .where(
                SavedViewRow.visibility == visibility.value,
                SavedViewRow.id != excluded_view_id,
            )
        )
        if visibility is SavedViewVisibility.PERSONAL:
            statement = statement.where(SavedViewRow.owner_member_id == member_id)
        count: int = int(await session.scalar(statement) or 0)
        limit: int = 50 if visibility is SavedViewVisibility.PERSONAL else 100
        if count >= limit:
            raise TelescreenError(ErrorCode.LIMIT_EXCEEDED, "saved view limit exceeded")

    async def _rejected(
        self, session: AsyncSession, actor_ref: str, fields: tuple[str, ...]
    ) -> None:
        """Append a field-only audit record for a redaction rejection."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                actor_ref,
                "view.rejected",
                "saved_view",
                "rejected",
                "saved view rejected by redactor",
                json_object((("fields", json_array(tuple(json_str(field) for field in fields))),)),
            ),
        )

    async def _visible(self, session: AsyncSession, view_id: str, member_id: str) -> SavedViewRow:
        """Load a view visible to the member or fail without disclosing ownership."""
        row: SavedViewRow = await self._required(session, view_id)
        if row.visibility != SavedViewVisibility.SHARED.value and row.owner_member_id != member_id:
            raise TelescreenError(ErrorCode.NOT_FOUND, "saved view not found")
        return row

    async def _required(self, session: AsyncSession, view_id: str) -> SavedViewRow:
        """Load a saved view or return a static not-found error."""
        row: SavedViewRow | None = await session.get(SavedViewRow, view_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "saved view not found")
        return row

    def _authorize(self, row: SavedViewRow, member_id: str, manage_shared: bool) -> None:
        """Enforce owner-only personal edits and shared-view capability."""
        if row.visibility == SavedViewVisibility.SHARED.value:
            if not manage_shared:
                raise TelescreenError(ErrorCode.FORBIDDEN, "shared view permission required")
        elif row.owner_member_id != member_id:
            raise TelescreenError(ErrorCode.NOT_FOUND, "saved view not found")

    def _require_shared(self, visibility: SavedViewVisibility, manage_shared: bool) -> None:
        """Require shared-view management permission for shared creation or conversion."""
        if visibility is SavedViewVisibility.SHARED and not manage_shared:
            raise TelescreenError(ErrorCode.FORBIDDEN, "shared view permission required")

    async def _audit(
        self, session: AsyncSession, actor_ref: str, action: str, view_id: str, visibility: str
    ) -> None:
        """Append an audit event restricted to saved-view id and visibility."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                actor_ref,
                action,
                "saved_view",
                view_id,
                action,
                json_object((("id", json_str(view_id)), ("visibility", json_str(visibility)))),
            ),
        )
