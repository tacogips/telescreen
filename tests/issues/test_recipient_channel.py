"""Tests for issue-notification channel selection by recipient reference."""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.issues.recipient_channel import preferred_channel
from telescreen.ports.directory import ResolvedRecipients


@dataclass
class TableResolver:
    """Resolve fixed references; unknown references raise ``recipient_unknown``."""

    table: tuple[tuple[str, ResolvedRecipients], ...]

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session
        for known, recipients in self.table:
            if known == ref:
                return recipients
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class BrokenResolver:
    """Raise a non-recipient error."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        raise TelescreenError(ErrorCode.INTERNAL_ERROR, "boom")


RESOLVER: TableResolver = TableResolver(
    (
        ("member:slacker", ResolvedRecipients(("a@example.invalid",), (), ("U0SLACK",), ())),
        ("member:mailer", ResolvedRecipients(("b@example.invalid",), (), (), ())),
        ("team:silent", ResolvedRecipients((), (), (), ())),
    )
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("member:slacker", NotificationChannel.SLACK),
        ("member:mailer", NotificationChannel.EMAIL),
        ("team:silent", None),
        ("member:unknown", None),
    ],
)
async def test_preferred_channel(ref: str, expected: NotificationChannel | None) -> None:
    session: AsyncSession = AsyncSession()
    assert await preferred_channel(RESOLVER, session, ref) is expected


@pytest.mark.asyncio
async def test_preferred_channel_propagates_other_errors() -> None:
    session: AsyncSession = AsyncSession()
    with pytest.raises(TelescreenError) as raised:
        await preferred_channel(BrokenResolver(), session, "member:slacker")
    assert raised.value.code is ErrorCode.INTERNAL_ERROR
