"""Tests for workflow recipient-reference helpers."""

from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.ports.directory import ResolvedRecipients
from telescreen.workflow.recipients import (
    ReachableRef,
    deliverable_channels,
    reachable_refs,
    refs_for_channel,
)


@dataclass
class FakeResolver:
    """Resolve a fixed table of references; unknown references raise."""

    table: tuple[tuple[str, ResolvedRecipients], ...]
    calls: list[str] = field(default_factory=list)

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session
        self.calls.append(ref)
        for known, recipients in self.table:
            if known == ref:
                return recipients
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class FailingResolver:
    """Raise a non-recipient error for every reference."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        raise TelescreenError(ErrorCode.INTERNAL_ERROR, "boom")


EMAIL_ONLY: ResolvedRecipients = ResolvedRecipients(("team@example.invalid",), (), (), ())
SLACK_ONLY: ResolvedRecipients = ResolvedRecipients((), ("C0TEAM",), (), ())
BOTH: ResolvedRecipients = ResolvedRecipients(("lead@example.invalid",), (), ("U0LEAD",), ())
NONE: ResolvedRecipients = ResolvedRecipients((), (), (), ())


def test_deliverable_channels_follow_available_addresses() -> None:
    assert deliverable_channels(EMAIL_ONLY) == (NotificationChannel.EMAIL,)
    assert deliverable_channels(SLACK_ONLY) == (NotificationChannel.SLACK,)
    assert deliverable_channels(BOTH) == (NotificationChannel.EMAIL, NotificationChannel.SLACK)
    assert deliverable_channels(NONE) == ()


@pytest.mark.asyncio
async def test_reachable_refs_keep_references_skip_unknown_and_dedupe() -> None:
    resolver: FakeResolver = FakeResolver(
        (("team:triage", EMAIL_ONLY), ("team:ops", SLACK_ONLY), ("member:lead", BOTH))
    )
    session: AsyncSession = AsyncSession()
    reachable: tuple[ReachableRef, ...] = await reachable_refs(
        resolver,
        session,
        ("team:triage", "team:unknown", "member:lead", "team:triage", "team:ops"),
    )

    assert [item.ref for item in reachable] == ["team:triage", "member:lead", "team:ops"]
    assert resolver.calls == ["team:triage", "team:unknown", "member:lead", "team:ops"]
    assert refs_for_channel(reachable, NotificationChannel.EMAIL) == (
        "team:triage",
        "member:lead",
    )
    assert refs_for_channel(reachable, NotificationChannel.SLACK) == ("member:lead", "team:ops")


@pytest.mark.asyncio
async def test_reachable_refs_propagate_non_recipient_errors() -> None:
    session: AsyncSession = AsyncSession()
    with pytest.raises(TelescreenError) as raised:
        await reachable_refs(FailingResolver(), session, ("team:triage",))
    assert raised.value.code is ErrorCode.INTERNAL_ERROR
