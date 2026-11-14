"""V1.3 issue, activity, alert, dashboard, and organization leak matrix."""

from email.message import EmailMessage
from pathlib import Path

import pytest

from telescreen.notifications.email import SmtplibTransport
from tests.leak.flows_v13 import run_v13_flows
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
async def test_v13_leak_matrix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise v1.3 routes and reject raw samples from every required sink."""
    caplog.set_level("INFO")
    world: LeakWorld = await make_leak_world(tmp_path, False)
    smtp_messages: list[str] = []

    def capture_smtp(_transport: SmtplibTransport, message: EmailMessage) -> None:
        smtp_messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture_smtp)
    try:
        sample_values: tuple[str, ...] = await run_v13_flows(world, smtp_messages)
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
        for sample_value in sample_values:
            assert all(sample_value not in text for _, text in sinks)
    finally:
        await world.close()
