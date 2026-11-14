"""End-to-end v1.3 weekly scheduler and member report flow."""

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import EngineTaskKind, NotificationCategory, NotificationStatus
from telescreen.db.models import EngineTaskRow, NotificationRow
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world


def _string_field(value: JsonValue, name: str) -> str | None:
    """Return one string field from a typed JSON object."""
    member: JsonValue | None = value.get(name)
    return member.as_str() if member is not None else None


@pytest.mark.asyncio
async def test_e5_weekly_scheduler_is_idempotent_and_respects_opt_out(tmp_path: Path) -> None:
    """Enqueue one weekly fire and notify each opted-in active member once."""
    world: E2EWorld = await make_world(
        tmp_path,
        (("TELESCREEN_WEEKLY_REPORT_CRON", "0 9 * * 1"),),
        OutboundRecorder(),
    )
    try:

        async def member_token(handle: str, email: str) -> str:
            created: httpx.Response = await world.client.post(
                "/api/members",
                headers=bearer(world.admin_token),
                json={"handle": handle, "display_name": handle.title(), "email": email},
            )
            assert created.status_code == 201, created.text
            role: httpx.Response = await world.client.put(
                f"/api/users/{handle}/role",
                headers=bearer(world.admin_token),
                json={"role": "responder"},
            )
            assert role.status_code == 200, role.text
            token: httpx.Response = await world.client.post(
                "/api/tokens",
                headers=bearer(world.admin_token),
                json={"label": f"e5-{handle}", "scopes": ["read"], "member_handle": handle},
            )
            assert token.status_code == 201, token.text
            return token.json()["token"]

        opted_in_token: str = await member_token("e5-in", "weekly-in@example.test")
        opted_out_token: str = await member_token("e5-out", "weekly-out@example.test")
        opted_in_settings: httpx.Response = await world.client.get(
            "/api/auth/me/notification-settings", headers=bearer(opted_in_token)
        )
        assert opted_in_settings.status_code == 200, opted_in_settings.text
        settings_document: JsonValue = parse_json(opted_in_settings.content)
        settings_value: JsonValue | None = settings_document.get("items")
        assert settings_value is not None and settings_value.as_list() is not None
        weekly_setting: JsonValue = next(
            item
            for item in settings_value.as_list() or ()
            if _string_field(item, "category") == "weekly_report"
        )
        effective_value: JsonValue | None = weekly_setting.get("effective")
        assert effective_value is not None and effective_value.as_str() == "email"
        opted_out: httpx.Response = await world.client.put(
            "/api/auth/me/notification-settings",
            headers=bearer(opted_out_token),
            json={"items": [{"category": "weekly_report", "stored": "off"}]},
        )
        assert opted_out.status_code == 200, opted_out.text

        world.clock.advance(timedelta(days=4, hours=9))
        first_tick: int = await world.container.worker.scheduler.tick()
        async with world.database.sessions() as session:
            before_second_tick: tuple[EngineTaskRow, ...] = tuple(
                await session.scalars(
                    select(EngineTaskRow).where(
                        EngineTaskRow.kind == EngineTaskKind.WEEKLY_REPORT.value
                    )
                )
            )
        assert len(before_second_tick) == 1
        assert before_second_tick[0].idempotency_key.startswith("weekly:")
        second_tick: int = await world.container.worker.scheduler.tick()
        async with world.database.sessions() as session:
            after_second_tick: tuple[EngineTaskRow, ...] = tuple(
                await session.scalars(
                    select(EngineTaskRow).where(
                        EngineTaskRow.kind == EngineTaskKind.WEEKLY_REPORT.value
                    )
                )
            )
        assert first_tick >= 1
        assert second_tick == 0
        assert len(after_second_tick) == 1

        processed: int = await world.container.worker.run_until_idle()
        assert processed >= 1
        async with world.database.sessions() as session:
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(
                        NotificationRow.category == NotificationCategory.WEEKLY_REPORT.value
                    )
                )
            )
        delivered_to_opted_in: tuple[NotificationRow, ...] = tuple(
            row for row in notifications if row.recipient_ref == "member:e5-in"
        )
        opted_out_rows: tuple[NotificationRow, ...] = tuple(
            row for row in notifications if row.recipient_ref == "member:e5-out"
        )
        assert len(delivered_to_opted_in) == 1
        assert delivered_to_opted_in[0].status == NotificationStatus.QUEUED.value
        assert len(opted_out_rows) == 1
        assert opted_out_rows[0].status == NotificationStatus.SKIPPED.value
        assert opted_out_rows[0].error_code == "recipient_opted_out"
    finally:
        await world.close()
