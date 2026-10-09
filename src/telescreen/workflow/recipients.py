"""Recipient-reference helpers shared by workflow notifications.

The notification sender stores recipient references (``team:x``, ``member:y``,
``email:z``, ``slack:c``) and resolves them to addresses at delivery time. Workflow code
therefore passes references, never resolved addresses, and uses resolution only to decide
which channels can reach a reference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.ports.directory import ResolvedRecipients

logger: logging.Logger = logging.getLogger(__name__)


class RecipientLookup(Protocol):
    """The part of ``RecipientResolver`` these helpers need."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients: ...


def deliverable_channels(recipients: ResolvedRecipients) -> tuple[NotificationChannel, ...]:
    """Return the channels on which resolved recipients have at least one address."""
    channels: list[NotificationChannel] = []
    if recipients.emails:
        channels.append(NotificationChannel.EMAIL)
    if recipients.slack_channels or recipients.slack_user_ids:
        channels.append(NotificationChannel.SLACK)
    return tuple(channels)


@dataclass(frozen=True)
class ReachableRef:
    """A recipient reference with the channels that can reach it."""

    ref: str
    channels: tuple[NotificationChannel, ...]


async def reachable_refs(
    resolver: RecipientLookup, session: AsyncSession, refs: tuple[str, ...]
) -> tuple[ReachableRef, ...]:
    """Resolve each distinct reference; unknown recipients are skipped, not fatal.

    An unknown recipient must never roll back the caller's transaction (for example the
    creation of a human task); it is logged without the reference value.
    """
    reachable: list[ReachableRef] = []
    for ref in dict.fromkeys(refs):
        try:
            resolved: ResolvedRecipients = await resolver.resolve(session, ref)
        except TelescreenError as error:
            if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                raise
            logger.warning("workflow notification recipient is unknown; skipped")
            continue
        channels: tuple[NotificationChannel, ...] = deliverable_channels(resolved)
        if channels:
            reachable.append(ReachableRef(ref, channels))
    return tuple(reachable)


def refs_for_channel(
    reachable: tuple[ReachableRef, ...], channel: NotificationChannel
) -> tuple[str, ...]:
    """Return the references reachable on ``channel``."""
    return tuple(item.ref for item in reachable if channel in item.channels)
