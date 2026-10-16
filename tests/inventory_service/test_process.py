"""Real TCP/process proof for all three independently deployed services."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.leak.flows_split import assert_split_sinks_clean
from tests.support.samples import Sample, all_samples, aws_access_key_id


def _port() -> int:
    """Select an available loopback port for an isolated subprocess."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run(command: tuple[str, ...], values: tuple[tuple[str, str], ...]) -> str:
    """Run a bounded owner CLI command and return its captured standard output."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        (sys.executable, "-m", *command),
        env={key: value for key, value in os.environ.items() if not key.startswith("TELESCREEN_")}
        | dict(values),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _start(
    command: tuple[str, ...], values: tuple[tuple[str, str], ...], log: Path
) -> subprocess.Popen[bytes]:
    """Start one service and retain output for a failure diagnosis."""
    with log.open("wb") as output:
        return subprocess.Popen(
            (sys.executable, "-m", *command),
            env={
                key: value for key, value in os.environ.items() if not key.startswith("TELESCREEN_")
            }
            | dict(values),
            stdout=output,
            stderr=subprocess.STDOUT,
        )


async def _ready(
    http: httpx.AsyncClient,
    url: str,
    process: subprocess.Popen[bytes],
    service_token: str | None = None,
) -> None:
    """Poll a live process and health endpoint with a bounded startup deadline."""
    deadline: float = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "service exited before readiness"
        try:
            response: httpx.Response = await http.get(
                url,
                headers=(
                    {"x-telescreen-service-token": service_token}
                    if service_token is not None
                    else {}
                ),
            )
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError(f"service did not become ready: {url}")


async def run_three_processes_with_leak_probe(tmp_path: Path) -> None:
    """Migrate and start the API, Inventory and Notification over real TCP sockets."""
    main_url: str = f"sqlite+aiosqlite:///{tmp_path / 'main.db'}"
    inventory_url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    notification_url: str = f"sqlite+aiosqlite:///{tmp_path / 'notification.db'}"
    api_port: int = _port()
    inventory_port: int = _port()
    notification_port: int = _port()
    while len({api_port, inventory_port, notification_port}) != 3:
        inventory_port = _port()
        notification_port = _port()
    main_values: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_DATABASE_URL", main_url),
        ("TELESCREEN_FINGERPRINT_KEY", "env:PROCESS_FP"),
        ("PROCESS_FP", "p" * 32),
        ("TELESCREEN_INVENTORY_DATABASE_URL", inventory_url),
        ("TELESCREEN_NOTIFICATION_DATABASE_URL", notification_url),
        ("TELESCREEN_WORKER_POLL_SECONDS", "0.1"),
    )
    inventory_values: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_INVENTORY_DATABASE_URL", inventory_url),
        (
            "TELESCREEN_NOTIFICATION_SERVICE_URL",
            f"http://127.0.0.1:{notification_port}",
        ),
        ("TELESCREEN_NOTIFICATION_INVENTORY_TOKEN", "env:PROCESS_INVENTORY_TOKEN"),
        ("PROCESS_INVENTORY_TOKEN", "inventory-process-token"),
        ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:PROCESS_INVENTORY_TOKEN"),
        ("TELESCREEN_FINGERPRINT_KEY", "env:PROCESS_FP"),
        ("PROCESS_FP", "p" * 32),
    )
    notification_values: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_NOTIFICATION_DATABASE_URL", notification_url),
        ("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:PROCESS_NOTIFICATION_TOKEN"),
        ("PROCESS_NOTIFICATION_TOKEN", "notification-process-token"),
        ("TELESCREEN_NOTIFICATION_INVENTORY_TOKEN", "env:PROCESS_INVENTORY_TOKEN"),
        ("PROCESS_INVENTORY_TOKEN", "inventory-process-token"),
        ("TELESCREEN_FINGERPRINT_KEY", "env:PROCESS_FP"),
        ("PROCESS_FP", "p" * 32),
    )
    _run(("telescreen", "migrate", "--store", "main"), main_values)
    _run(("telescreen.inventory_service", "migrate"), inventory_values)
    _run(("telescreen.notification_service", "migrate"), notification_values)

    main_runtime: tuple[tuple[str, str], ...] = main_values + (
        ("TELESCREEN_INVENTORY_SERVICE_URL", f"http://127.0.0.1:{inventory_port}"),
        ("TELESCREEN_NOTIFICATION_SERVICE_URL", f"http://127.0.0.1:{notification_port}"),
        ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:PROCESS_INVENTORY_TOKEN"),
        ("PROCESS_INVENTORY_TOKEN", "inventory-process-token"),
        ("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:PROCESS_NOTIFICATION_TOKEN"),
        ("PROCESS_NOTIFICATION_TOKEN", "notification-process-token"),
    )
    admin_token: str = _run(
        ("telescreen", "token", "create", "--label", "p08-process", "--scopes", "admin,read,write"),
        main_runtime,
    )
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(
            _start(
                ("telescreen", "serve", "--host", "127.0.0.1", "--port", str(api_port)),
                main_runtime,
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-telescreen.log",
            )
        )
        processes.append(
            _start(
                ("telescreen", "worker"),
                main_runtime,
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-worker.log",
            )
        )
        processes.append(
            _start(
                (
                    "telescreen.inventory_service",
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(inventory_port),
                ),
                inventory_values,
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-inventory.log",
            )
        )
        processes.append(
            _start(
                (
                    "telescreen.notification_service",
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(notification_port),
                ),
                notification_values,
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-notification.log",
            )
        )
        async with httpx.AsyncClient(timeout=2, trust_env=False) as http:
            await _ready(http, f"http://127.0.0.1:{api_port}/api/health", processes[0])
            await _ready(
                http,
                f"http://127.0.0.1:{inventory_port}/internal/inventory/v1/health",
                processes[2],
                "inventory-process-token",
            )
            await _ready(
                http,
                f"http://127.0.0.1:{notification_port}/internal/notification/v1/health",
                processes[3],
                "notification-process-token",
            )
            _run(("telescreen", "projections", "resync"), main_runtime)
            sample: Sample = aws_access_key_id()
            response: httpx.Response = await http.post(
                f"http://127.0.0.1:{api_port}/api/data/assets",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "Process inventory",
                    "source": "manual",
                    "locator": "process/split",
                    "kind": "table",
                },
            )
            assert response.status_code == 201, response.text
            asset_id: str = response.json()["id"]
            detail: httpx.Response = await http.get(
                f"http://127.0.0.1:{api_port}/api/data/assets/{asset_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["name"] == "Process inventory"
            collector: httpx.Response = await http.post(
                f"http://127.0.0.1:{api_port}/api/collectors",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "split-process-leak",
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": "{}",
                    "credential_ref": None,
                    "enabled": True,
                },
            )
            assert collector.status_code in (200, 201), collector.text
            accepted: httpx.Response = await http.post(
                f"http://127.0.0.1:{api_port}/api/ingest/v1/scan",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "source": "custom:split-process-leak",
                    "resources": [
                        {
                            "locator": "accepted-sensitive-source",
                            "segments": [{"locator": "line:1", "text": sample.value}],
                        }
                    ],
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert sample.value not in accepted.text
            rejected: httpx.Response = await http.post(
                f"http://127.0.0.1:{api_port}/api/data/assets",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "Sensitive locator probe",
                    "source": "manual",
                    "locator": "process/" + sample.value,
                    "kind": "table",
                },
            )
            assert rejected.status_code == 422, rejected.text
            assert sample.value not in rejected.text
            log_paths: tuple[Path, ...] = (
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-telescreen.log",
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-worker.log",
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-inventory.log",
                Path("/tmp") / f"telescreen-svc-p08-{tmp_path.name}-notification.log",
            )
            assert_split_sinks_clean(
                all_samples(),
                (tmp_path / "main.db", tmp_path / "inventory.db", tmp_path / "notification.db"),
                log_paths,
                (response.text, detail.text, collector.text, accepted.text, rejected.text),
            )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
        assert all(process.poll() is not None for process in processes)


@pytest.mark.asyncio
async def test_three_processes_serve_from_three_owned_databases(tmp_path: Path) -> None:
    """Start all services, register an asset through the proxy and scan raw-value sinks."""
    await run_three_processes_with_leak_probe(tmp_path)
