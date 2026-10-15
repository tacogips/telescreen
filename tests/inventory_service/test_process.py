"""Real TCP/process proof for the independently deployed inventory runtime."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import SystemClock
from telescreen.core.vocabulary import ApiScope
from telescreen.db.migrate import upgrade_store
from telescreen.db.models import AuditRecordRow
from telescreen.db.ownership import Store
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.masking.redact import Redactor
from tests.e2e.harness import bearer


def _port() -> int:
    """Select an available loopback port for an isolated subprocess."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start(
    command: tuple[str, ...], values: tuple[tuple[str, str], ...], log: Path
) -> subprocess.Popen[bytes]:
    """Exclude inherited application configuration from both test processes."""
    with log.open("wb") as output:
        return subprocess.Popen(
            (
                sys.executable,
                "-m",
                "telescreen.inventory_service" if command[0] == "inventory-serve" else "telescreen",
                "serve" if command[0] == "inventory-serve" else command[0],
                *command[1:],
            ),
            env={
                key: value for key, value in os.environ.items() if not key.startswith("TELESCREEN_")
            }
            | dict(values),
            stdout=output,
            stderr=subprocess.STDOUT,
        )


async def _ready(http: httpx.AsyncClient, url: str, process: subprocess.Popen[bytes]) -> None:
    """Poll the actual live process and endpoint with a bounded startup deadline."""
    deadline: float = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "service exited before readiness"
        try:
            response: httpx.Response = await http.get(
                url, headers={"x-telescreen-service-token": "process-secret"}
            )
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError("service did not become ready")


async def test_two_processes_use_only_their_owned_database(tmp_path: Path) -> None:
    """Real CLI, lifespan worker and TCP callbacks work without inventory knowing main URL."""
    main_url: str = f"sqlite+aiosqlite:///{tmp_path / 'main.db'}"
    inventory_url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    await asyncio.to_thread(upgrade_store, main_url, Store.MAIN)
    await asyncio.to_thread(upgrade_store, inventory_url, Store.INVENTORY)
    engine: AsyncEngine = create_engine(main_url)
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    clock: SystemClock = SystemClock()
    tokens: TokenService = TokenService(AuditLogService(clock, Redactor.default()), clock)
    token: CreatedToken
    async with sessions.begin() as session:
        token = await tokens.create(
            session,
            "process-admin",
            frozenset((ApiScope.ADMIN, ApiScope.READ, ApiScope.WRITE)),
            None,
        )
    bureau_port: int = _port()
    inventory_port: int = _port()
    while bureau_port == inventory_port:
        inventory_port = _port()
    bureau_url: str = f"http://127.0.0.1:{bureau_port}"
    service_url: str = f"http://127.0.0.1:{inventory_port}"
    common: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_FINGERPRINT_KEY", "env:PROCESS_FP"),
        ("PROCESS_FP", "p" * 32),
        ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:PROCESS_SERVICE_TOKEN"),
        ("PROCESS_SERVICE_TOKEN", "process-secret"),
        ("TELESCREEN_WORKER_POLL_SECONDS", "0.1"),
        ("TELESCREEN_DATA_OVERDUE_CRON", ""),
    )
    bureau: subprocess.Popen[bytes] = _start(
        ("serve", "--host", "127.0.0.1", "--port", str(bureau_port)),
        common
        + (
            ("TELESCREEN_DATABASE_URL", main_url),
            ("TELESCREEN_INVENTORY_SERVICE_URL", service_url),
        ),
        tmp_path / "bureau.log",
    )
    inventory: subprocess.Popen[bytes] = _start(
        ("inventory-serve", "--host", "127.0.0.1", "--port", str(inventory_port)),
        common
        + (
            ("TELESCREEN_INVENTORY_DATABASE_URL", inventory_url),
            ("TELESCREEN_BUREAU_SERVICE_URL", bureau_url),
        ),
        tmp_path / "inventory.log",
    )
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as http:
            await _ready(http, bureau_url + "/api/health", bureau)
            await _ready(http, service_url + "/internal/inventory/v1/health", inventory)
            response: httpx.Response = await http.post(
                bureau_url + "/api/data/assets",
                headers=bearer(token.token),
                json={
                    "name": "Independent process",
                    "source": "manual",
                    "locator": "process/master",
                    "kind": "table",
                },
            )
            assert response.status_code == 201, response.text
            asset_id: str = response.json()["id"]
            response = await http.get(
                bureau_url + f"/api/data/assets/{asset_id}", headers=bearer(token.token)
            )
            assert response.status_code == 200, response.text
            assert response.json()["name"] == "Independent process"
            deadline: float = time.monotonic() + 15
            while True:
                async with sessions() as session:
                    count: int = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(AuditRecordRow)
                            .where(AuditRecordRow.action == "data_asset.registered")
                        )
                        or 0
                    )
                if count == 1:
                    break
                assert time.monotonic() < deadline, "inventory lifespan did not deliver audit"
                assert inventory.poll() is None
                await asyncio.sleep(0.1)
    finally:
        for process in (inventory, bureau):
            if process.poll() is None:
                process.terminate()
        for process in (inventory, bureau):
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
        await engine.dispose()
