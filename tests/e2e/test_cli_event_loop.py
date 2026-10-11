"""CLI commands must close their container on the event loop that ran them."""

import argparse
import asyncio
from dataclasses import dataclass, field

import pytest

from telescreen import cli
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource


@dataclass
class LoopRecorder:
    """Record which event loop ran the command and which one closed the container."""

    command_loops: list[asyncio.AbstractEventLoop] = field(default_factory=list)
    close_loops: list[asyncio.AbstractEventLoop] = field(default_factory=list)


@dataclass(frozen=True)
class FakeContainer:
    """Stand-in container; only its identity matters to this test."""

    label: str = "cli"


def test_command_and_close_share_one_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncpg pools are bound to their loop, so a second asyncio.run cannot close them."""
    recorder: LoopRecorder = LoopRecorder()
    container: FakeContainer = FakeContainer()

    def build(settings: TelescreenSettings, env: EnvSource) -> FakeContainer:
        del settings, env
        return container

    async def run_command(target: FakeContainer, arguments: argparse.Namespace) -> int:
        del arguments
        assert target is container
        recorder.command_loops.append(asyncio.get_running_loop())
        return 0

    async def close(target: FakeContainer) -> None:
        assert target is container
        recorder.close_loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(cli, "_build_cli_container", build)
    monkeypatch.setattr(cli, "_run_async_command", run_command)
    monkeypatch.setattr(cli, "close_container", close)

    assert cli.main(["token", "create", "--label", "loop-check", "--scopes", "read"]) == 0
    assert len(recorder.command_loops) == 1
    assert recorder.close_loops == recorder.command_loops


def test_container_is_closed_when_the_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing command still closes its resources on the same loop."""
    recorder: LoopRecorder = LoopRecorder()
    container: FakeContainer = FakeContainer()

    def build(settings: TelescreenSettings, env: EnvSource) -> FakeContainer:
        del settings, env
        return container

    async def run_command(target: FakeContainer, arguments: argparse.Namespace) -> int:
        del target, arguments
        recorder.command_loops.append(asyncio.get_running_loop())
        raise RuntimeError("command failed")

    async def close(target: FakeContainer) -> None:
        del target
        recorder.close_loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(cli, "_build_cli_container", build)
    monkeypatch.setattr(cli, "_run_async_command", run_command)
    monkeypatch.setattr(cli, "close_container", close)

    assert cli.main(["token", "create", "--label", "loop-check", "--scopes", "read"]) != 0
    assert recorder.close_loops == recorder.command_loops
