"""Static UI serving tests for the assembled API application."""

from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource
from telescreen.wiring import Container, build_container, close_container


@dataclass(frozen=True)
class _Environment(EnvSource):
    """Provide an in-memory environment for composition tests."""

    key: str

    def get(self, key: str, /) -> str | None:
        """Resolve only the fixture fingerprint secret."""
        return self.key if key == "TELESCREEN_TEST_KEY" else None


def _container(web_dist: str | None) -> Container:
    """Create a lightweight container backed by in-memory SQLite and search."""
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        web_dist=web_dist,
    )
    env: EnvSource = _Environment("t" * 32)
    client: httpx.AsyncClient = httpx.AsyncClient()
    return build_container(settings, env, client)


@pytest.mark.asyncio
async def test_static_assets_spa_api_and_traversal(tmp_path: Path) -> None:
    """Serve the SPA and assets while keeping API paths and traversal separate."""
    dist: Path = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>shell</main>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    container: Container = _container(str(dist))
    app: FastAPI = create_app(container)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root: httpx.Response = await client.get("/")
        nested: httpx.Response = await client.get("/issues/iss-01")
        asset: httpx.Response = await client.get("/assets/app.js")
        unknown_api: httpx.Response = await client.get("/api/does-not-exist")
        health: httpx.Response = await client.get("/api/health")
        traversal: httpx.Response = await client.get("/%2e%2e/pyproject.toml")
    await close_container(container)
    assert root.status_code == 200
    assert root.text == "<main>shell</main>"
    assert root.headers["cache-control"] == "no-cache"
    assert nested.text == root.text
    assert nested.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert asset.text == "console.log('ok')"
    assert unknown_api.status_code == 404
    assert unknown_api.json()["error"]["code"] == "not_found"
    assert health.status_code == 200
    assert "pyproject.toml" not in traversal.text


@pytest.mark.asyncio
async def test_api_only_and_missing_index_configuration(tmp_path: Path) -> None:
    """Leave the root absent in API-only mode and reject an incomplete UI build."""
    container: Container = _container(None)
    app: FastAPI = create_app(container)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root: httpx.Response = await client.get("/")
    await close_container(container)
    assert root.status_code == 404
    empty_dist: Path = tmp_path / "empty"
    empty_dist.mkdir()
    broken: Container = _container(str(empty_dist))
    with pytest.raises(ConfigError, match="configured web directory is unavailable"):
        create_app(broken)
    await close_container(broken)
