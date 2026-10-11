"""V1.2 canary and identity-token leak matrix across every configured sink."""

from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest
from sqlalchemy import select

from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.vocabulary import OidcProvisioning
from telescreen.db.models import NotificationRow, NotificationTemplateRow
from telescreen.notifications.email import SmtplibTransport
from tests.identity.oidc_fixtures import AUDIENCE, ISSUER, base_settings
from tests.leak.flows_v12 import run_v12_flows
from tests.leak.harness import LeakWorld, make_leak_world
from tests.leak.sinks import (
    api_responses,
    database_text,
    evidence_files,
    logs,
    outbound_bodies,
    search_documents,
    v11_record_text,
)
from tests.support.oidc import TestIdp as LocalTestIdp
from tests.support.samples import all_samples, find_leaks


@pytest.mark.asyncio
async def test_v12_leak_matrix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run v1.2 flows and reject raw sample values, tokens, and subjects at every sink."""
    caplog.set_level("INFO")
    idp: LocalTestIdp = LocalTestIdp.generate(ISSUER, AUDIENCE)
    oidc_settings: OidcSettings = replace(base_settings(), provisioning=OidcProvisioning.CREATE)
    world: LeakWorld = await make_leak_world(tmp_path, False, oidc_settings, idp)
    smtp_messages: list[str] = []

    def capture_smtp(_transport: SmtplibTransport, message: EmailMessage) -> None:
        smtp_messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture_smtp)
    try:
        secrets: tuple[str, ...] = await run_v12_flows(world, idp, smtp_messages)
        await world.drain()
        async with world.database.sessions() as session:
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(select(NotificationRow))
            )
        assert any(row.status == "sent" for row in notifications)
        assert any(row.status == "skipped" for row in notifications)
        sinks: tuple[tuple[str, str], ...] = (
            await database_text(world)
            + logs(world, caplog.text)
            + search_documents(world)
            + outbound_bodies(world)
            + evidence_files(world)
            + api_responses(world)
            + await v11_record_text(world)
            + tuple((f"smtp:{index}", message) for index, message in enumerate(smtp_messages))
        )
        for sample in all_samples():
            for label, text in sinks:
                assert find_leaks(text) == (), (sample.name, label)
        for secret in secrets:
            assert all(secret not in text for _, text in sinks)
        async with world.database.sessions() as session:
            stored_templates: int = len(
                tuple(await session.scalars(select(NotificationTemplateRow)))
            )
        assert stored_templates == 0
    finally:
        await world.close()
