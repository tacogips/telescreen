"""Caller-transaction persistence operations for alert rules and fires."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import AlertFireRow, AlertRuleRow


class AlertRepository:
    """Store and query alert-owned rows without opening transactions."""

    async def list_rules(self, session: AsyncSession) -> tuple[AlertRuleRow, ...]:
        """Return rules ordered by name and id."""
        rows: Sequence[AlertRuleRow] = (
            await session.scalars(select(AlertRuleRow).order_by(AlertRuleRow.name, AlertRuleRow.id))
        ).all()
        return tuple(rows)

    async def get_rule(self, session: AsyncSession, rule_id: str) -> AlertRuleRow | None:
        """Return one rule by identifier."""
        return await session.get(AlertRuleRow, rule_id)

    async def insert_rule(self, session: AsyncSession, row: AlertRuleRow) -> None:
        """Insert and flush one rule."""
        session.add(row)
        await session.flush()

    async def add_fire(self, session: AsyncSession, row: AlertFireRow) -> None:
        """Insert and flush one fire outcome."""
        session.add(row)
        await session.flush()

    async def fires_since(self, session: AsyncSession, rule_id: str, since: datetime) -> int:
        """Count fires for a rule since a timestamp."""
        count: int | None = await session.scalar(
            select(func.count())
            .select_from(AlertFireRow)
            .where(AlertFireRow.rule_id == rule_id, AlertFireRow.fired_at >= since)
        )
        return count or 0

    async def latest_issue_fire(
        self, session: AsyncSession, rule_id: str, issue_id: str
    ) -> AlertFireRow | None:
        """Return latest fire for a rule and issue."""
        return await session.scalar(
            select(AlertFireRow)
            .where(AlertFireRow.rule_id == rule_id, AlertFireRow.issue_id == issue_id)
            .order_by(AlertFireRow.fired_at.desc(), AlertFireRow.id.desc())
            .limit(1)
        )

    async def latest_delivered_issue_fire(
        self, session: AsyncSession, rule_id: str, issue_id: str
    ) -> AlertFireRow | None:
        """Return the latest delivered fire for a rule and issue."""
        return await session.scalar(
            select(AlertFireRow)
            .where(
                AlertFireRow.rule_id == rule_id,
                AlertFireRow.issue_id == issue_id,
                AlertFireRow.outcome == "delivered",
            )
            .order_by(AlertFireRow.fired_at.desc(), AlertFireRow.id.desc())
            .limit(1)
        )

    async def latest_muted_issue_fire(
        self, session: AsyncSession, rule_id: str, issue_id: str
    ) -> AlertFireRow | None:
        """Return the latest muted fire for a rule and issue."""
        return await session.scalar(
            select(AlertFireRow)
            .where(
                AlertFireRow.rule_id == rule_id,
                AlertFireRow.issue_id == issue_id,
                AlertFireRow.outcome == "muted",
            )
            .order_by(AlertFireRow.fired_at.desc(), AlertFireRow.id.desc())
            .limit(1)
        )

    async def has_suppressed_interval_since(
        self,
        session: AsyncSession,
        rule_id: str,
        issue_id: str,
        delivered_at: datetime,
    ) -> bool:
        """Check whether the delivery window already has a suppression row."""
        fire_id: str | None = await session.scalar(
            select(AlertFireRow.id)
            .where(
                AlertFireRow.rule_id == rule_id,
                AlertFireRow.issue_id == issue_id,
                AlertFireRow.outcome == "suppressed_interval",
                AlertFireRow.fired_at >= delivered_at,
            )
            .limit(1)
        )
        return fire_id is not None

    async def history(
        self,
        session: AsyncSession,
        rule_id: str,
        before: datetime | None,
        before_id: str | None,
        limit: int = 51,
    ) -> tuple[AlertFireRow, ...]:
        """Read newest fires, optionally before a timestamp and id cursor."""
        statement: Select[tuple[AlertFireRow]] = select(AlertFireRow).where(
            AlertFireRow.rule_id == rule_id
        )
        if before is not None:
            if before_id is None:
                statement = statement.where(AlertFireRow.fired_at < before)
            else:
                statement = statement.where(
                    or_(
                        AlertFireRow.fired_at < before,
                        and_(AlertFireRow.fired_at == before, AlertFireRow.id < before_id),
                    )
                )
        rows: Sequence[AlertFireRow] = (
            await session.scalars(
                statement.order_by(AlertFireRow.fired_at.desc(), AlertFireRow.id.desc()).limit(
                    limit
                )
            )
        ).all()
        return tuple(rows)

    async def delete_fires_before(self, session: AsyncSession, before: datetime) -> int:
        """Delete fire history older than the retention cutoff."""
        count: int | None = await session.scalar(
            select(func.count()).select_from(AlertFireRow).where(AlertFireRow.fired_at < before)
        )
        await session.execute(delete(AlertFireRow).where(AlertFireRow.fired_at < before))
        return count or 0
