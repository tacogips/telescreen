"""Shared lifecycle and probe helpers for split-service process tests."""

from __future__ import annotations

import asyncio
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx


def free_port() -> int:
    """Return a currently available loopback TCP port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def run_cli(command: tuple[str, ...], values: tuple[tuple[str, str], ...]) -> str:
    """Run a bounded service CLI and return standard output."""
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


def start_service(
    command: tuple[str, ...], values: tuple[tuple[str, str], ...], log: Path
) -> subprocess.Popen[bytes]:
    """Start a process, appending its output to a persistent diagnostic log."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as output:
        return subprocess.Popen(
            (sys.executable, "-m", *command),
            env={
                key: value for key, value in os.environ.items() if not key.startswith("TELESCREEN_")
            }
            | dict(values),
            stdout=output,
            stderr=subprocess.STDOUT,
        )


async def wait_ready(
    http: httpx.AsyncClient,
    url: str,
    process: subprocess.Popen[bytes],
    service_token: str | None = None,
) -> None:
    """Poll a process and health endpoint for up to 30 seconds."""
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


def stop_service(process: subprocess.Popen[bytes]) -> None:
    """Terminate a process, then kill it after bounded graceful shutdown."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def fetch_rows(path: Path, table: str) -> tuple[tuple[str, ...], ...]:
    """Read every value from a SQLite table without acquiring write access."""
    import sqlite3

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
        cursor: sqlite3.Cursor = connection.execute(f'SELECT * FROM "{table}"')
        rows: list[tuple[str, ...]] = []
        for row in cursor.fetchall():
            values: tuple[str, ...] = tuple(
                value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
                for value in row
            )
            rows.append(values)
        return tuple(rows)


def scalar_int(path: Path, sql: str, params: tuple[str, ...] = ()) -> int:
    """Read one integer scalar from a SQLite store in read-only mode."""
    import sqlite3

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
        value: int | None = connection.execute(sql, params).fetchone()[0]
    assert value is not None
    return int(value)


def wait_for(check: CallableCheck, timeout: float, what: str) -> None:
    """Poll a synchronous predicate at 200 ms intervals with a 90 s maximum."""
    deadline: float = time.monotonic() + min(timeout, 90)
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError(what)


type CallableCheck = Callable[[], bool]


class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 localhost ESMTP\r\n")
        while True:
            line: bytes = self.rfile.readline()
            if not line:
                return
            command: str = line.decode("utf-8", errors="replace").strip()
            upper: str = command.upper()
            if upper.startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-localhost\r\n250 OK\r\n")
            elif upper.startswith(("MAIL", "RCPT")):
                self.wfile.write(b"250 OK\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                chunks: list[str] = []
                while True:
                    data_line: bytes = self.rfile.readline()
                    if data_line in (b".\r\n", b".\n", b""):
                        break
                    chunks.append(data_line.decode("utf-8", errors="replace"))
                assert isinstance(self.server, _ThreadingSMTPServer)
                with self.server.message_lock:
                    self.server.messages.append("".join(chunks))
                self.wfile.write(b"250 queued\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 OK\r\n")


@dataclass(init=False)
class _ThreadingSMTPServer(socketserver.ThreadingTCPServer):
    """SMTP TCP server with synchronized captured payload storage."""

    allow_reuse_address: bool
    daemon_threads: bool
    messages: list[str]
    message_lock: threading.Lock

    def __init__(
        self, address: tuple[str, int], handler: type[socketserver.BaseRequestHandler]
    ) -> None:
        self.allow_reuse_address = True
        self.daemon_threads = True
        super().__init__(address, handler)
        self.messages = []
        self.message_lock = threading.Lock()


@dataclass
class SmtpRecorder:
    """Threaded minimal SMTP sink used by process tests."""

    requested_port: int = 0
    server: _ThreadingSMTPServer = field(init=False)
    thread: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self.server = _ThreadingSMTPServer(("127.0.0.1", self.requested_port), _SmtpHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def messages(self) -> tuple[str, ...]:
        with self.server.message_lock:
            return tuple(self.server.messages)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@dataclass
class ServiceStack:
    """Paths, ports, environments and process handles for one split deployment."""

    main_path: Path
    inventory_path: Path
    notification_path: Path
    api_port: int
    inventory_port: int
    notification_port: int
    smtp_port: int
    main_env: tuple[tuple[str, str], ...]
    inventory_env: tuple[tuple[str, str], ...]
    notification_env: tuple[tuple[str, str], ...]
    log_dir: Path
    api_process: subprocess.Popen[bytes] | None = None
    worker_process: subprocess.Popen[bytes] | None = None
    inventory_process: subprocess.Popen[bytes] | None = None
    notification_process: subprocess.Popen[bytes] | None = None
    admin_token: str | None = None

    @classmethod
    def create(cls, root: Path) -> ServiceStack:
        """Create unique SQLite stores, endpoint ports and process environments."""
        api_port: int = free_port()
        inventory_port: int = free_port()
        notification_port: int = free_port()
        smtp_port: int = free_port()
        ports: tuple[int, ...] = (api_port, inventory_port, notification_port, smtp_port)
        while len(set(ports)) != len(ports):
            api_port = free_port()
            inventory_port = free_port()
            notification_port = free_port()
            smtp_port = free_port()
            ports = (api_port, inventory_port, notification_port, smtp_port)
        main_path: Path = root / "main.db"
        inventory_path: Path = root / "inventory.db"
        notification_path: Path = root / "notification.db"
        main_env: tuple[tuple[str, str], ...] = (
            ("TELESCREEN_DATABASE_URL", f"sqlite+aiosqlite:///{main_path}"),
            ("TELESCREEN_INVENTORY_DATABASE_URL", f"sqlite+aiosqlite:///{inventory_path}"),
            ("TELESCREEN_NOTIFICATION_DATABASE_URL", f"sqlite+aiosqlite:///{notification_path}"),
            ("TELESCREEN_FINGERPRINT_KEY", "env:P08_FP"),
            ("P08_FP", "p" * 32),
            ("TELESCREEN_WORKER_POLL_SECONDS", "0.1"),
        )
        inventory_env: tuple[tuple[str, str], ...] = (
            ("TELESCREEN_INVENTORY_DATABASE_URL", f"sqlite+aiosqlite:///{inventory_path}"),
            ("TELESCREEN_NOTIFICATION_SERVICE_URL", f"http://127.0.0.1:{notification_port}"),
            ("TELESCREEN_NOTIFICATION_INVENTORY_TOKEN", "env:P08_INVENTORY_TOKEN"),
            ("P08_INVENTORY_TOKEN", "inventory-process-token"),
            ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:P08_INVENTORY_TOKEN"),
            ("TELESCREEN_FINGERPRINT_KEY", "env:P08_FP"),
            ("P08_FP", "p" * 32),
            ("TELESCREEN_WORKER_POLL_SECONDS", "0.1"),
            ("TELESCREEN_DATA_OVERDUE_CRON", ""),
        )
        notification_env: tuple[tuple[str, str], ...] = (
            ("TELESCREEN_NOTIFICATION_DATABASE_URL", f"sqlite+aiosqlite:///{notification_path}"),
            ("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:P08_NOTIFICATION_TOKEN"),
            ("P08_NOTIFICATION_TOKEN", "notification-process-token"),
            ("TELESCREEN_NOTIFICATION_INVENTORY_TOKEN", "env:P08_INVENTORY_TOKEN"),
            ("P08_INVENTORY_TOKEN", "inventory-process-token"),
            ("TELESCREEN_FINGERPRINT_KEY", "env:P08_FP"),
            ("P08_FP", "p" * 32),
            ("TELESCREEN_SMTP_HOST", "127.0.0.1"),
            ("TELESCREEN_SMTP_PORT", str(smtp_port)),
            ("TELESCREEN_SMTP_FROM", "telescreen@example.invalid"),
            ("TELESCREEN_SMTP_STARTTLS", "false"),
        )
        return cls(
            main_path,
            inventory_path,
            notification_path,
            api_port,
            inventory_port,
            notification_port,
            smtp_port,
            main_env,
            inventory_env,
            notification_env,
            root / "logs",
        )

    def start(self, name: str) -> subprocess.Popen[bytes]:
        """Start one named process, retaining the process handle on this stack."""
        if name == "api":
            command: tuple[str, ...] = (
                "telescreen",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
            )
            values: tuple[tuple[str, str], ...] = self._main_runtime()
            process: subprocess.Popen[bytes] = start_service(
                command, values, self.log_dir / "telescreen.log"
            )
            self.api_process = process
            return process
        if name == "worker":
            process = start_service(
                ("telescreen", "worker"), self._main_runtime(), self.log_dir / "worker.log"
            )
            self.worker_process = process
            return process
        if name == "inventory":
            command = (
                "telescreen.inventory_service",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.inventory_port),
            )
            values = self.inventory_env
            process = start_service(command, values, self.log_dir / "inventory.log")
            self.inventory_process = process
            return process
        if name == "notification":
            command = (
                "telescreen.notification_service",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.notification_port),
            )
            process = start_service(
                command, self.notification_env, self.log_dir / "notification.log"
            )
            self.notification_process = process
            return process
        raise ValueError(f"unknown service process: {name}")

    async def start_all(self, http: httpx.AsyncClient) -> str:
        """Migrate, start all service processes, resync replicas and return admin token."""
        _ = run_cli(("telescreen", "migrate", "--store", "main"), self.main_env)
        _ = run_cli(("telescreen.inventory_service", "migrate"), self.inventory_env)
        _ = run_cli(("telescreen.notification_service", "migrate"), self.notification_env)
        self.admin_token = run_cli(
            (
                "telescreen",
                "token",
                "create",
                "--label",
                "p08-process",
                "--scopes",
                "admin,read,write",
            ),
            self._main_runtime(),
        )
        notification: subprocess.Popen[bytes] = self.start("notification")
        inventory: subprocess.Popen[bytes] = self.start("inventory")
        api: subprocess.Popen[bytes] = self.start("api")
        _ = self.start("worker")
        await wait_ready(
            http,
            f"http://127.0.0.1:{self.api_port}/api/health",
            api,
        )
        await wait_ready(
            http,
            f"http://127.0.0.1:{self.inventory_port}/internal/inventory/v1/health",
            inventory,
            "inventory-process-token",
        )
        await wait_ready(
            http,
            f"http://127.0.0.1:{self.notification_port}/internal/notification/v1/health",
            notification,
            "notification-process-token",
        )
        _ = run_cli(("telescreen", "projections", "resync"), self._main_runtime())
        return self.admin_token

    def stop(self, name: str) -> None:
        """Stop one named process and clear its handle after exit."""
        process: subprocess.Popen[bytes] | None = self._process(name)
        if process is not None:
            stop_service(process)
            self._set_process(name, None)

    def restart(
        self, name: str, extra_env: tuple[tuple[str, str], ...] = ()
    ) -> subprocess.Popen[bytes]:
        """Restart one service with optional environment overrides."""
        self.stop(name)
        if extra_env:
            if name == "inventory":
                self.inventory_env = self._merge(self.inventory_env, extra_env)
            elif name == "notification":
                self.notification_env = self._merge(self.notification_env, extra_env)
            elif name in ("api", "worker"):
                self.main_env = self._merge(self.main_env, extra_env)
        return self.start(name)

    def stop_all(self) -> None:
        """Stop every process started by this stack."""
        for name in ("api", "worker", "inventory", "notification"):
            self.stop(name)

    def _main_runtime(self) -> tuple[tuple[str, str], ...]:
        return self._merge(
            self.main_env,
            (
                ("TELESCREEN_INVENTORY_SERVICE_URL", f"http://127.0.0.1:{self.inventory_port}"),
                (
                    "TELESCREEN_NOTIFICATION_SERVICE_URL",
                    f"http://127.0.0.1:{self.notification_port}",
                ),
                ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:P08_INVENTORY_TOKEN"),
                ("P08_INVENTORY_TOKEN", "inventory-process-token"),
                ("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:P08_NOTIFICATION_TOKEN"),
                ("P08_NOTIFICATION_TOKEN", "notification-process-token"),
            ),
        )

    def main_runtime(self) -> tuple[tuple[str, str], ...]:
        """Return the Telescreen API and worker environment with service endpoints."""
        return self._main_runtime()

    def _process(self, name: str) -> subprocess.Popen[bytes] | None:
        if name == "api":
            return self.api_process
        if name == "worker":
            return self.worker_process
        if name == "inventory":
            return self.inventory_process
        if name == "notification":
            return self.notification_process
        raise ValueError(f"unknown service process: {name}")

    def _set_process(self, name: str, process: subprocess.Popen[bytes] | None) -> None:
        if name == "api":
            self.api_process = process
        elif name == "worker":
            self.worker_process = process
        elif name == "inventory":
            self.inventory_process = process
        elif name == "notification":
            self.notification_process = process
        else:
            raise ValueError(f"unknown service process: {name}")

    @staticmethod
    def _merge(
        values: tuple[tuple[str, str], ...], extra: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        extra_keys: tuple[str, ...] = tuple(key for key, _ in extra)
        retained: tuple[tuple[str, str], ...] = tuple(
            pair for pair in values if pair[0] not in extra_keys
        )
        return retained + extra


async def seed_member(
    stack: ServiceStack, http: httpx.AsyncClient, handle: str, bodies: list[str]
) -> str:
    """Create a member and wait until both independent directory replicas contain it."""
    assert stack.admin_token is not None
    api_url: str = f"http://127.0.0.1:{stack.api_port}"
    response: httpx.Response = await http.post(
        f"{api_url}/api/members",
        headers={"Authorization": f"Bearer {stack.admin_token}"},
        json={
            "handle": handle,
            "display_name": handle.title(),
            "email": f"{handle}@example.invalid",
            "slack_user_id": None,
        },
    )
    bodies.append(response.text)
    assert response.status_code == 201, response.text
    member_id: str = response.json()["id"]
    _ = run_cli(("telescreen", "projections", "resync"), stack.main_runtime())
    wait_for(
        lambda: (
            scalar_int(
                stack.inventory_path,
                "SELECT count(*) FROM inventory_directory_entries "
                "WHERE kind='member' AND id=? AND present=1",
                (member_id,),
            )
            == 1
        ),
        90,
        f"Inventory directory replica did not receive {handle}",
    )
    wait_for(
        lambda: (
            scalar_int(
                stack.notification_path,
                "SELECT count(*) FROM notification_directory_entries "
                "WHERE kind='member' AND id=? AND present=1",
                (member_id,),
            )
            == 1
        ),
        90,
        f"Notification directory replica did not receive {handle}",
    )
    return member_id


async def seed_issue(
    stack: ServiceStack, http: httpx.AsyncClient, text: str, bodies: list[str]
) -> str:
    """Create a custom collector, ingest detectable text, and resolve its issue id."""
    assert stack.admin_token is not None
    api_url: str = f"http://127.0.0.1:{stack.api_port}"
    headers: tuple[tuple[str, str], ...] = (("Authorization", f"Bearer {stack.admin_token}"),)
    collector: httpx.Response = await http.post(
        f"{api_url}/api/collectors",
        headers=headers,
        json={
            "name": "p08-process-seed",
            "kind": "custom",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": "{}",
            "credential_ref": None,
            "enabled": True,
        },
    )
    bodies.append(collector.text)
    assert collector.status_code in (200, 201), collector.text
    accepted: httpx.Response = await http.post(
        f"{api_url}/api/ingest/v1/scan",
        headers=headers,
        json={
            "source": "custom:p08-process-seed",
            "resources": [
                {
                    "locator": "p08-process-seed.txt",
                    "segments": [{"locator": "line:1", "text": text}],
                }
            ],
        },
    )
    bodies.append(accepted.text)
    assert accepted.status_code == 200, accepted.text
    issue_ids: list[str] = accepted.json()["issue_ids"]
    assert issue_ids
    listed: httpx.Response = await http.get(
        f"{api_url}/api/issues", headers=headers, params={"period": "all"}
    )
    bodies.append(listed.text)
    assert listed.status_code == 200, listed.text
    issue_id: str = issue_ids[0]
    matching: tuple[str, ...] = tuple(
        item["id"] for item in listed.json()["items"] if item["id"] == issue_id
    )
    assert matching == (issue_id,)
    return issue_id
