"""Channel choice for issue notifications that are addressed by recipient reference.

The notification sender stores recipient references (``member:x``, ``team:y``) and resolves
them at delivery time, so issue notifications pass the reference itself, never resolved
addresses. Resolution here only picks the channel; an unknown recipient is skipped so it
never rolls back the caller's transaction (exception expiry, verification, digests).
Issue notifications send an event and a ``NotificationContext`` built from masked issue
columns; the notification service renders the text from a template.
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.db.models import IssueRow
from telescreen.issues.keys import short_id
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.notifications import NotificationContext

logger: logging.Logger = logging.getLogger(__name__)


class RecipientLookup(Protocol):
    """The part of ``RecipientResolver`` needed to choose a channel."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients: ...


async def preferred_channel(
    resolver: RecipientLookup, session: AsyncSession, ref: str
) -> NotificationChannel | None:
    """Return Slack when the reference has Slack users, else email, else ``None``."""
    try:
        recipients: ResolvedRecipients = await resolver.resolve(session, ref)
    except TelescreenError as error:
        if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
            raise
        logger.warning("issue notification recipient is unknown; skipped")
        return None
    if recipients.slack_user_ids:
        return NotificationChannel.SLACK
    if recipients.emails:
        return NotificationChannel.EMAIL
    return None


def issue_context(issue: IssueRow) -> NotificationContext:
    """Build a template context from an issue's identifier and enum columns only."""
    return NotificationContext(
        issue_short_id=short_id(issue.number),
        issue_rule_class=issue.rule_class,
        issue_severity=issue.severity,
        issue_status=issue.status,
        issue_exposure=issue.exposure_visibility,
        issue_risk_score=issue.risk_score,
    )
