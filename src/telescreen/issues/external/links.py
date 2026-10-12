"""Validated external issue links and audit records."""

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.db.models import ExternalIssueLinkRow, IssueRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog


@dataclass(frozen=True)
class ExternalLinkService:
    """Create, list and remove safe external issue links."""

    redactor: Redactor
    audit: AuditLog
    clock: Clock

    async def list(self, session: AsyncSession, issue_id: str) -> tuple[ExternalIssueLinkRow, ...]:
        """List links for an issue in creation order."""
        await _require_issue(session, issue_id)
        return tuple(
            await session.scalars(
                select(ExternalIssueLinkRow)
                .where(ExternalIssueLinkRow.issue_id == issue_id)
                .order_by(ExternalIssueLinkRow.created_at, ExternalIssueLinkRow.id)
            )
        )

    async def link(
        self,
        session: AsyncSession,
        issue_id: str,
        url: str,
        title: str | None,
        actor: str,
    ) -> ExternalIssueLinkRow:
        """Validate and persist one HTTPS link without echoing rejected input."""
        await _require_issue(session, issue_id)
        selected_title: str = title or _host(url)
        if (
            len(url) > 2048
            or not _is_valid_https_url(url)
            or len(selected_title) > 200
            or self.redactor.has_hits(url)
            or self.redactor.has_hits(selected_title)
        ):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "external link rejected")
        row: ExternalIssueLinkRow = ExternalIssueLinkRow(
            id=new_id(),
            issue_id=issue_id,
            kind="url",
            url=url,
            title=selected_title,
            external_ref=None,
            created_by=actor,
            created_at=ensure_utc(self.clock.now()),
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            raise TelescreenError(ErrorCode.CONFLICT, "external link already exists") from None
        await _audit(
            self.audit, session, actor, "external_issue.linked", row.id, "External issue linked"
        )
        return row

    async def unlink(self, session: AsyncSession, issue_id: str, link_id: str, actor: str) -> None:
        """Remove a link scoped to the requested issue."""
        row: ExternalIssueLinkRow | None = await session.scalar(
            select(ExternalIssueLinkRow).where(
                ExternalIssueLinkRow.issue_id == issue_id, ExternalIssueLinkRow.id == link_id
            )
        )
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "external link not found")
        await session.delete(row)
        await _audit(
            self.audit,
            session,
            actor,
            "external_issue.unlinked",
            link_id,
            "External issue unlinked",
        )


async def _require_issue(session: AsyncSession, issue_id: str) -> IssueRow:
    """Require an existing issue without exposing request data."""
    issue: IssueRow | None = await session.get(IssueRow, issue_id)
    if issue is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
    return issue


def _host(url: str) -> str:
    """Return the parsed hostname or an empty safe default."""
    try:
        parsed: SplitResult = urlsplit(url)
    except ValueError:
        return "External issue"
    return parsed.hostname or "External issue"


def _is_valid_https_url(url: str) -> bool:
    """Check the external link scheme and userinfo constraints."""
    try:
        parsed: SplitResult = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


async def _audit(
    audit: AuditLog,
    session: AsyncSession,
    actor: str,
    action: str,
    target_id: str,
    summary: str,
) -> None:
    """Append a content-free external issue audit event."""
    kind: ActorKind = ActorKind.MEMBER if actor.startswith("member:") else ActorKind.TOKEN
    await audit.append(
        session,
        AuditEntry(
            kind,
            actor,
            action,
            "external_issue",
            target_id,
            summary,
            json_object((("id", json_str(target_id)),)),
        ),
    )
