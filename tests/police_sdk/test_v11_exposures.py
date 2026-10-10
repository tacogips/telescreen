"""Police SDK 1.1 exposure serialization and worker pass-through tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from telescreen_police_sdk import _json
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    CONTRACT_VERSION,
    ChannelKind,
    RepoVisibility,
    ResourceExposure,
    ResultEnvelope,
    ResultStatus,
    TaskEnvelope,
    Visibility,
)
from telescreen_police_sdk.worker import PullWorker


def test_resource_exposure_round_trips_full_and_required_shapes() -> None:
    """Round-trip required-only and fully populated exposure records."""
    minimal: ResourceExposure = ResourceExposure("docs/example.txt", Visibility.UNKNOWN)
    full: ResourceExposure = ResourceExposure(
        "repo/main",
        Visibility.EXTERNAL_SHARED,
        external_principal_count=3,
        shared_drive=False,
        channel_kind=ChannelKind.PUBLIC,
        repo_visibility=RepoVisibility.PUBLIC,
        is_fork=True,
    )
    assert ResourceExposure.from_json(minimal.to_json()) == minimal
    assert ResourceExposure.from_json(full.to_json()) == full


def test_result_envelope_omits_empty_exposures_and_uses_contract_11() -> None:
    """Default envelopes omit the optional key and the SDK advertises 1.1."""
    envelope: ResultEnvelope = ResultEnvelope("task", ResultStatus.COMPLETED)
    assert CONTRACT_VERSION == "1.1"
    assert all(member.name != "resource_exposures" for member in envelope.to_json().members)


@pytest.mark.asyncio
async def test_pull_worker_submits_handler_exposures() -> None:
    """Pass handler-provided exposure records through the pull submission body."""
    submitted: list[bytes] = []
    stop: asyncio.Event = asyncio.Event()
    exposure: ResourceExposure = ResourceExposure("docs/example.txt", Visibility.PUBLIC)
    task: TaskEnvelope = TaskEnvelope(
        "task-exposure",
        "detect",
        1,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:05:00Z",
        (),
        _json.obj(()),
    )

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                content=_json.dump(
                    _json.obj((("tasks", _json.array((task.to_json(),))),))
                ).encode(),
            )
        if request.url.path.endswith("/result"):
            submitted.append(request.content)
            stop.set()
            return httpx.Response(204)
        return httpx.Response(404)

    async def handler(envelope: TaskEnvelope) -> ResultEnvelope:
        return ResultEnvelope(
            envelope.task_id, ResultStatus.COMPLETED, resource_exposures=(exposure,)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        worker: PullWorker = PullWorker(
            BureauClient("https://bureau.example.invalid", http),
            "worker",
            "token",
            (("detect", handler),),
            1,
            0.01,
            0,
        )
        await asyncio.wait_for(worker.run(stop), timeout=1)

    assert len(submitted) == 1
    result: ResultEnvelope = ResultEnvelope.from_json(_json.parse(submitted[0]))
    assert result.resource_exposures == (exposure,)
