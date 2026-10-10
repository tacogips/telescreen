"""v1.1 canary and raw-value leak matrix for records, APIs, and notifications."""

import logging
from pathlib import Path

import pytest
from sqlalchemy import func, select

from telescreen.db.models import (
    CollectorHealthRow,
    IssueExceptionRow,
    MemberIdentityRow,
    NotificationRow,
    PlacementPolicyRow,
    ResourceCoverageRow,
    ScanCoverageRow,
)
from tests.leak.flows_v11 import run_v11_flows
from tests.leak.harness import CANARY_EMAIL, CANARY_FILENAME, LeakWorld, make_leak_world
from tests.leak.sinks import (
    api_responses,
    database_text,
    evidence_files,
    logs,
    outbound_bodies,
    search_documents,
    v11_record_text,
)
from tests.support.samples import all_samples, find_leaks


@pytest.mark.asyncio
async def test_v11_leak_matrix(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Run the v1.1 flow suite and inspect new records alongside every v1 sink."""
    caplog.set_level(logging.INFO)
    world: LeakWorld = await make_leak_world(tmp_path, with_llm=False)
    try:
        await run_v11_flows(world)
        await world.drain()
        sinks: tuple[tuple[str, str], ...] = (
            await database_text(world)
            + logs(world, caplog.text)
            + search_documents(world)
            + outbound_bodies(world)
            + evidence_files(world)
            + api_responses(world)
            + await v11_record_text(world)
        )
        for sample in all_samples():
            for label, text in sinks:
                assert find_leaks(text) == (), (sample.name, label)
        record_sinks: tuple[tuple[str, str], ...] = await v11_record_text(world)
        for canary in (CANARY_EMAIL, CANARY_FILENAME):
            assert all(canary not in text for _, text in record_sinks)
        protected_responses: tuple[str, ...] = tuple(
            text
            for path, text in zip(
                world.recorder.response_paths, world.recorder.responses, strict=True
            )
            if path.startswith(("/api/exceptions", "/api/policies", "/api/collectors/"))
            or (path.startswith("/api/members/") and "/identities" in path)
            or (path.startswith("/api/issues/") and path.endswith("/occurrences"))
        )
        for canary in (CANARY_EMAIL, CANARY_FILENAME):
            assert all(canary not in text for text in protected_responses)
        assert all("canary-login" not in text for _, text in sinks)
        async with world.database.sessions() as session:
            expected_counts: tuple[int, ...] = (
                await session.scalar(select(func.count()).select_from(PlacementPolicyRow)) or 0,
                await session.scalar(select(func.count()).select_from(IssueExceptionRow)) or 0,
                await session.scalar(select(func.count()).select_from(MemberIdentityRow)) or 0,
                await session.scalar(select(func.count()).select_from(ResourceCoverageRow)) or 0,
                await session.scalar(select(func.count()).select_from(ScanCoverageRow)) or 0,
                await session.scalar(select(func.count()).select_from(CollectorHealthRow)) or 0,
                await session.scalar(select(func.count()).select_from(NotificationRow)) or 0,
            )
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(select(NotificationRow))
            )
        assert all(count > 0 for count in expected_counts), expected_counts
        assert any(row.subject == "Telescreen owner digest" for row in notifications)
        assert any(row.subject.startswith("Telescreen health:") for row in notifications)
    finally:
        await world.close()
