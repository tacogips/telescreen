"""Raw-value leak matrix and focused evidence/input rejection regressions."""

import logging
from pathlib import Path

import pytest
from sqlalchemy import select

from telescreen.db.models import IssueRow
from tests.leak.flows import (
    chat,
    collector_scan,
    forced_exception,
    image_evidence,
    issue_and_search_reads,
    notify_workflow,
    police_results,
    push_ingestion,
    scanner_unavailable,
    text_evidence,
)
from tests.leak.harness import LeakWorld, make_leak_world
from tests.leak.sinks import (
    api_responses,
    database_text,
    evidence_files,
    logs,
    named_sink,
    outbound_bodies,
    search_documents,
)
from tests.support.samples import find_leaks, github_token


async def _first_issue(world: LeakWorld, ids: tuple[str, ...]) -> str:
    """Return the first created issue id and assert it exists in storage."""
    del world
    assert ids
    return ids[0]


async def _assert_issue_counts(world: LeakWorld) -> None:
    """Require every issue created by the matrix to have a positive event count."""
    async with world.database.sessions() as session:
        issues: tuple[IssueRow, ...] = tuple(await session.scalars(select(IssueRow)))
    assert issues
    assert all(issue.times_seen >= 1 for issue in issues)


@pytest.mark.asyncio
async def test_leak_matrix(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the broad request matrix and require every captured sink to stay clean."""
    caplog.set_level(logging.INFO)
    world: LeakWorld = await make_leak_world(tmp_path, with_llm=True)
    no_llm: LeakWorld = await make_leak_world(tmp_path / "no-llm", with_llm=False)
    exception_body: str = ""
    exception_log: str = ""
    stderr: str = ""
    try:
        issue_ids: tuple[str, ...] = await push_ingestion(world)
        issue_id: str = await _first_issue(world, issue_ids)
        await collector_scan(world)
        await issue_and_search_reads(world, issue_ids)
        await notify_workflow(world)
        await text_evidence(world, issue_id)
        await image_evidence(world, issue_id)
        await scanner_unavailable(no_llm, issue_id)
        await police_results(world)
        await chat(world)
        exception_body, exception_log, stderr = await forced_exception(
            world, github_token().value, monkeypatch, capsys
        )
        await no_llm.drain()
        await _assert_issue_counts(world)
        sinks: tuple[tuple[str, str], ...] = (
            await database_text(world)
            + logs(world, caplog.text)
            + search_documents(world)
            + outbound_bodies(world)
            + evidence_files(world)
            + api_responses(world)
            + named_sink("traceback:500", exception_body)
            + named_sink("traceback:excepthook", exception_log)
            + named_sink("exception:logger", world.log_buffer.getvalue())
            + named_sink("stderr", stderr)
            + await database_text(no_llm)
            + evidence_files(no_llm)
            + api_responses(no_llm)
        )
        assert sinks
        for label, text in sinks:
            assert find_leaks(text) == (), label
        labels: tuple[str, ...] = tuple(label for label, _ in sinks)
        assert any(label.startswith("db:") for label in labels)
        assert any(label.startswith("logs:") for label in labels)
        assert any(label.startswith("search:") for label in labels)
        assert any(label.startswith("outbound:https://slack.com") for label in labels)
        assert any("api.anthropic.com" in label for label in labels)
        assert any(label.startswith("evidence:") for label in labels)
        assert len(tuple(label for label in labels if label.startswith("api-response:"))) >= 20
        assert "Uncaught exception" in exception_log
    finally:
        await world.close()
        await no_llm.close()


@pytest.mark.asyncio
async def test_image_metadata_is_stripped_before_storage(tmp_path: Path) -> None:
    """Ensure accepted image evidence is stripped of sample metadata."""
    world: LeakWorld = await make_leak_world(tmp_path, with_llm=True)
    try:
        ids: tuple[str, ...] = await push_ingestion(world)
        await image_evidence(world, ids[0])
        values: tuple[tuple[str, str], ...] = evidence_files(world)
        assert values
        for label, value in values:
            assert find_leaks(value) == (), label
            assert "tEXt" not in value and "Exif\x00\x00" not in value, label
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_scanner_unavailable_returns_503_and_stores_nothing(tmp_path: Path) -> None:
    """Fail closed when image scanning is unconfigured and retain no bytes."""
    world: LeakWorld = await make_leak_world(tmp_path, with_llm=False)
    try:
        ids: tuple[str, ...] = await push_ingestion(world)
        await scanner_unavailable(world, ids[0])
        assert evidence_files(world) == ()
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_raw_findings_push_is_rejected_without_echo(tmp_path: Path) -> None:
    """Reject raw external findings while keeping the response and stores clean."""
    world: LeakWorld = await make_leak_world(tmp_path, with_llm=False)
    try:
        await push_ingestion(world)
        for label, text in await database_text(world) + api_responses(world):
            assert find_leaks(text) == (), label
    finally:
        await world.close()
