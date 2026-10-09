"""Leak-matrix sink readers for the full Reporting Bureau container."""

import json
from pathlib import Path

from sqlalchemy import select

from telescreen.db.base import Base
from telescreen.search.memory import InMemorySearchBackend
from tests.leak.harness import LeakWorld


async def database_text(world: LeakWorld) -> tuple[tuple[str, str], ...]:
    """Read every stored scalar value that can represent text."""
    values: list[tuple[str, str]] = []
    async with world.database.sessions() as session:
        for table in Base.metadata.sorted_tables:
            for row in await session.execute(select(table)):
                for index, value in enumerate(row):
                    if isinstance(value, str):
                        values.append((f"db:{table.name}:{index}", value))
                    elif isinstance(value, bytes):
                        values.append((f"db:{table.name}:{index}", value.decode(errors="replace")))
    return tuple(values)


def logs(world: LeakWorld, captured: str) -> tuple[tuple[str, str], ...]:
    """Return the configured masked root buffer and pytest-captured logs."""
    return (("logs:root-buffer", world.log_buffer.getvalue()), ("logs:caplog", captured))


def search_documents(world: LeakWorld) -> tuple[tuple[str, str], ...]:
    """Serialize all in-memory search documents as potential raw sinks."""
    if not isinstance(world.container.search, InMemorySearchBackend):
        raise AssertionError("test container does not use in-memory search")
    backend: InMemorySearchBackend = world.container.search
    return tuple(
        (f"search:{index.value}:{document.id}", json.dumps(document.fields))
        for index, document in backend.documents
    )


def outbound_bodies(world: LeakWorld) -> tuple[tuple[str, str], ...]:
    """Decode each recorded outbound request body for leak inspection."""
    return tuple(
        (f"outbound:{url}", body.decode("utf-8", errors="replace"))
        for url, body in world.recorder.requests
    )


def evidence_files(world: LeakWorld) -> tuple[tuple[str, str], ...]:
    """Read evidence files using byte-preserving and replacement decoders."""
    root: Path = world.tmp / "evidence"
    if not root.exists():
        return ()
    sinks: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            raw: bytes = path.read_bytes()
            sinks.append((f"evidence:{path.name}:latin1", raw.decode("latin-1")))
            sinks.append((f"evidence:{path.name}:utf8", raw.decode("utf-8", errors="replace")))
    return tuple(sinks)


def api_responses(world: LeakWorld) -> tuple[tuple[str, str], ...]:
    """Return eagerly buffered API responses."""
    return tuple(
        (f"api-response:{index}", text) for index, text in enumerate(world.recorder.responses)
    )


def named_sink(label: str, text: str) -> tuple[tuple[str, str], ...]:
    """Wrap one manually captured exceptional path in the common sink shape."""
    return ((label, text),)
