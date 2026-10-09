"""Compile-time integration patterns for dependencies used by later plans."""

import asyncio

import anthropic
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from mcp.server.fastmcp import FastMCP
from pydantic.dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column


@dataclass
class EchoRequest:
    """Typed FastAPI request body."""

    value: str


@dataclass
class EchoResponse:
    """Typed FastAPI response body."""

    value: str


app: FastAPI = FastAPI()


@app.post("/echo", response_model=EchoResponse)
async def echo(body: EchoRequest) -> EchoResponse:
    """Return a typed request value."""
    return EchoResponse(value=body.value)


class Base(MappedAsDataclass, DeclarativeBase):
    """Declarative base for the SQLite typing spike."""


class Widget(Base):
    """Minimal dataclass-style SQLAlchemy model."""

    __tablename__ = "typing_spike_widgets"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column()


server: FastMCP[None] = FastMCP("typing-spike")


@server.tool()
def count_items(query: str) -> int:
    """Return a typed MCP tool result without running the tool."""
    return len(query)


def _mock_response(request: httpx.Request) -> httpx.Response:
    """Return a harmless mock response for SDK construction."""
    return httpx.Response(200, json={"ok": True}, request=request)


@dataclass(frozen=True)
class SpikeRow:
    """Typed row returned from the database exercise."""

    name: str


async def _exercise_route() -> None:
    """Call the FastAPI route through an in-process ASGI transport."""
    transport: ASGITransport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response: httpx.Response = await client.post("/echo", json={"value": "ok"})
    assert response.status_code == 200
    assert response.json() == {"value": "ok"}


async def _exercise_database() -> None:
    """Write and read a model using in-memory aiosqlite."""
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Widget(name="typed"))
        await session.commit()
        result: ScalarResult[Widget] = (await session.execute(select(Widget))).scalars()
        row: Widget = result.one()
        assert row.name == "typed"
        typed_row: SpikeRow = SpikeRow(name=row.name)
        assert typed_row.name == "typed"
    await engine.dispose()


def test_fastapi_pydantic_dataclass_over_asgi() -> None:
    """Pydantic dataclasses work as FastAPI request and response bodies."""
    asyncio.run(_exercise_route())


def test_sqlalchemy_dataclass_model_with_aiosqlite() -> None:
    """MappedAsDataclass supports the required unannotated table dunders."""
    asyncio.run(_exercise_database())


def test_mcp_typed_tool_registration() -> None:
    """FastMCP accepts one typed tool function at registration time."""
    assert server.name == "typing-spike"
    assert count_items("hello") == 5


def test_anthropic_mock_transport_construction() -> None:
    """Anthropic accepts an injected httpx mock transport without network use."""

    async def construct_and_close() -> None:
        transport: httpx.MockTransport = httpx.MockTransport(_mock_response)
        http_client: httpx.AsyncClient = httpx.AsyncClient(transport=transport)
        client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
            api_key="placeholder",
            http_client=http_client,
        )
        await client.close()

    asyncio.run(construct_and_close())
