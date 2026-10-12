"""Member notification preference defaults and persistence tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ErrorCode,
    NotificationCategory,
    NotificationPreference,
)
from telescreen.db.models import NotificationSettingRow
from telescreen.notifications.preferences import (
    CategorySetting,
    PreferenceRepository,
    PreferenceService,
)
from tests.notifications.v13.fakes import member_row
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.parametrize(
    ("email", "slack_user_id", "expected"),
    (
        ("member@example.test", None, NotificationPreference.EMAIL),
        (None, "U12345678", NotificationPreference.SLACK),
        (None, None, NotificationPreference.OFF),
    ),
)
async def test_defaults_follow_available_member_channels(
    email: str | None,
    slack_user_id: str | None,
    expected: NotificationPreference,
) -> None:
    """Use email first, then personal Slack, then off when no override exists."""
    harness: SqliteHarness = await make_sqlite_harness()
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", email, slack_user_id))
    service: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions() as session:
        settings: tuple[CategorySetting, ...] = await service.settings(session, "member-1")
        assert len(settings) == len(NotificationCategory)
        assert all(setting.stored is None for setting in settings)
        assert all(setting.effective is expected for setting in settings)
        channel: NotificationPreference = await service.channel_for(
            session, "member-1", NotificationCategory.MENTIONS
        )
        assert channel is expected
    await harness.close()


async def test_update_and_null_delete_explicit_preference() -> None:
    """Persist a preference and remove it when the submitted value is null."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", None))
    async with harness.sessions.begin() as session:
        updated: tuple[CategorySetting, ...] = await service.update(
            session,
            "member-1",
            ((NotificationCategory.MENTIONS, NotificationPreference.OFF),),
        )
        setting: CategorySetting = next(
            item for item in updated if item.category is NotificationCategory.MENTIONS
        )
        assert setting.stored is NotificationPreference.OFF
    async with harness.sessions.begin() as session:
        updated = await service.update(
            session, "member-1", ((NotificationCategory.MENTIONS, None),)
        )
        setting: CategorySetting = next(
            item for item in updated if item.category is NotificationCategory.MENTIONS
        )
        assert setting.stored is None
        rows: tuple[NotificationSettingRow, ...] = tuple(
            (await session.scalars(select(NotificationSettingRow))).all()
        )
        assert rows == ()
    await harness.close()


async def test_slack_without_slack_id_rejects_batch_without_partial_write() -> None:
    """Reject Slack settings as unreachable before applying any item in the batch."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", None))
    with pytest.raises(TelescreenError) as caught:
        async with harness.sessions.begin() as session:
            await service.update(
                session,
                "member-1",
                (
                    (NotificationCategory.ASSIGNED, NotificationPreference.OFF),
                    (NotificationCategory.MENTIONS, NotificationPreference.SLACK),
                ),
            )
    assert caught.value.code is ErrorCode.RECIPIENT_UNREACHABLE
    async with harness.sessions() as session:
        rows: tuple[NotificationSettingRow, ...] = tuple(
            (await session.scalars(select(NotificationSettingRow))).all()
        )
    assert rows == ()
    await harness.close()
