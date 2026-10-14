"""Exercise v1.4 inventory paths against every raw-value leak sink."""

from email.message import EmailMessage
from pathlib import Path

import pytest

from telescreen.notifications.email import SmtplibTransport
from tests.leak.flows_v14 import run_v14_flows
from tests.leak.harness import LeakWorld, make_leak_world
from tests.leak.sinks import (
    api_responses,
    database_text,
    logs,
    outbound_bodies,
    search_documents,
)
from tests.support.samples import all_samples, find_leaks


@pytest.mark.asyncio
async def test_v14_leak_matrix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assert seven real-container inventory flows leave no raw sample in any sink."""
    caplog.set_level("INFO")
    world: LeakWorld = await make_leak_world(tmp_path, False, independent_sessions=True)
    smtp_messages: list[str] = []

    def capture_smtp(_transport: SmtplibTransport, message: EmailMessage) -> None:
        smtp_messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture_smtp)
    try:
        values: tuple[str, ...] = await run_v14_flows(world, smtp_messages)
        sinks: tuple[tuple[str, str], ...] = (
            await database_text(world)
            + logs(world, caplog.text)
            + search_documents(world)
            + outbound_bodies(world)
            + api_responses(world)
            + tuple((f"smtp:{index}", message) for index, message in enumerate(smtp_messages))
        )
        for sample in all_samples():
            for label, text in sinks:
                assert find_leaks(text) == (), (sample.name, label)
        for value in values:
            assert all(value not in text for _, text in sinks)
    finally:
        await world.close()
