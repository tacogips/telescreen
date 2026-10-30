"""Offline end-to-end sweep: the Go gdrive police worker against the real Bureau app.

The Bureau is the hermetic harness app (``tests/e2e/harness.py``, SQLite) served over
loopback by ``uvicorn.Server`` in the test's event loop. Google is the loopback fake in
``tests/go_police/e2e_fakes.py``. The worker binary is built into the ``go/bin`` artifact
root and runs as a subprocess.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import func, select

from telescreen.api.app import create_app
from telescreen.core.vocabulary import IssueStatus, MapItemStatus, MapJobStatus
from telescreen.db.models import (
    AuditRecordRow,
    DataAssetRow,
    IssueRow,
    MapItemRow,
    MapJobRow,
)
from telescreen.db.models.inventory_handoff import InventoryOutboxRow
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.go_police.e2e_fakes import (
    FakeEstate,
    FakeGoogle,
    default_estate,
    generate_private_key,
    start_fake_google,
    write_service_account,
)
from tests.support.samples import (
    assert_no_leaks,
    aws_access_key_id,
    credit_card,
    email_address,
    password_assignment,
)

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
GO_MODULE: Path = REPO_ROOT / "go"
E2E_BINARY: Path = GO_MODULE / "bin" / "telescreen-gdrive-police-e2e"
ENROLLMENT_TOKEN: str = "e2e-enrollment-" + "x" * 32
FINGERPRINT_KEY: str = "e2e-go-police-fingerprint-" + "k" * 32
OVERALL_TIMEOUT_SECONDS: float = 180.0
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    (
        MapJobStatus.SUCCEEDED.value,
        MapJobStatus.PARTIAL.value,
        MapJobStatus.FAILED.value,
        MapJobStatus.CANCELLED.value,
    )
)
KINDS: tuple[str, ...] = ("gdrive", "gdocs", "gsheets")


@dataclass(frozen=True)
class SweepConfig:
    """One police collector config and the map job its sweep started."""

    kind: str
    config_id: str
    name: str
    job_id: str

    @property
    def source(self) -> str:
        """Return the issue source the Bureau records police findings under."""
        return "police:" + self.name


@dataclass(frozen=True)
class IssueView:
    """The issue columns this test asserts."""

    source: str
    locator: str
    rule_class: str
    status: str
    visibility: str


@dataclass
class WorkerProcess:
    """The running Go worker and its captured output."""

    process: asyncio.subprocess.Process
    stdout: asyncio.Task[bytes]
    stderr: asyncio.Task[bytes]
    output: str = field(default="", repr=False)


def _inherited(names: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return the named variables that exist in the current environment."""
    return [(name, os.environ[name]) for name in names if name in os.environ]


def _build_worker() -> None:
    """Build the worker binary into the go/bin artifact root with the mise toolchain."""
    go: str | None = shutil.which("go")
    if go is None:
        pytest.fail("go toolchain not found on PATH; run via mise (mise exec -- uv run pytest)")
    pairs: list[tuple[str, str]] = _inherited(("PATH", "HOME", "GOPATH", "GOCACHE", "GOMODCACHE"))
    pairs.extend((("GOTOOLCHAIN", "local"), ("GOFLAGS", "-mod=vendor")))
    try:
        subprocess.run(
            [go, "build", "-trimpath", "-o", str(E2E_BINARY), "./cmd/telescreen-gdrive-police"],
            cwd=GO_MODULE,
            env=dict(pairs),
            timeout=300,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        pytest.fail(f"go build failed with exit {error.returncode}:\n{error.stderr}")


async def _serve_bureau(app: FastAPI) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    """Serve the Bureau app on a loopback port in this event loop."""
    server: uvicorn.Server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=5,
        )
    )
    task: asyncio.Task[None] = asyncio.create_task(server.serve())
    async with asyncio.timeout(30):
        while not server.started:
            if task.done():
                pytest.fail("uvicorn exited before it started serving")
            await asyncio.sleep(0.05)
    port: int = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


async def _create_configs(world: E2EWorld) -> tuple[tuple[str, str, str], ...]:
    """Create one police collector config per selector kind; return (kind, id, name)."""
    created: list[tuple[str, str, str]] = []
    for kind in KINDS:
        name: str = "e2e-" + kind
        response: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": name,
                "kind": "police",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": json.dumps({"labels": ["police=gdrive"], "input": {"kind": kind}}),
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "all",
            },
        )
        assert response.status_code == 200, response.text
        created.append((kind, str(response.json()["id"]), name))
    return tuple(created)


async def _start_sweeps(
    world: E2EWorld, configs: tuple[tuple[str, str, str], ...]
) -> tuple[SweepConfig, ...]:
    """Start one sweep per config through the public collectors endpoint."""
    sweeps: list[SweepConfig] = []
    for kind, config_id, name in configs:
        response: httpx.Response = await world.client.post(
            f"/api/collectors/{config_id}/sweep", headers=bearer(world.admin_token)
        )
        assert response.status_code == 200, response.text
        sweeps.append(SweepConfig(kind, config_id, name, str(response.json()["map_job_id"])))
    return tuple(sweeps)


async def _start_worker(
    bureau_url: str, google: FakeGoogle, service_account: Path, admin: str
) -> WorkerProcess:
    """Spawn the worker with only its own variables, stdout and stderr piped."""
    pairs: list[tuple[str, str]] = [
        ("TELESCREEN_URL", bureau_url),
        ("TELESCREEN_POLICE_ENROLLMENT_TOKEN", ENROLLMENT_TOKEN),
        ("TELESCREEN_POLICE_FINGERPRINT_KEY", FINGERPRINT_KEY),
        ("TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE", str(service_account)),
        ("TELESCREEN_GOOGLE_DELEGATED_ADMIN", admin),
        ("TELESCREEN_INTERNAL_DOMAINS", "example.com"),
        ("TELESCREEN_GOOGLE_API_BASE_URL", google.base_url),
        ("TELESCREEN_GOOGLE_SHEETS_BASE_URL", google.base_url),
        ("TELESCREEN_GOOGLE_ADMIN_BASE_URL", google.base_url),
        ("TELESCREEN_POLICE_ALLOW_HTTP", "true"),
        ("TELESCREEN_POLICE_CONCURRENCY", "4"),
    ]
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        str(E2E_BINARY),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(pairs),
    )
    assert process.stdout is not None and process.stderr is not None
    return WorkerProcess(
        process,
        asyncio.create_task(process.stdout.read()),
        asyncio.create_task(process.stderr.read()),
    )


async def _stop_worker(worker: WorkerProcess) -> int:
    """SIGTERM the worker, wait up to 30 s, then kill; collect its output."""
    if worker.process.returncode is None:
        worker.process.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(worker.process.wait(), timeout=30)
        except TimeoutError:
            worker.process.kill()
            await asyncio.wait_for(worker.process.wait(), timeout=10)
    out: bytes = await asyncio.wait_for(worker.stdout, timeout=10)
    err: bytes = await asyncio.wait_for(worker.stderr, timeout=10)
    worker.output = out.decode(errors="replace") + err.decode(errors="replace")
    return worker.process.returncode if worker.process.returncode is not None else -1


async def _job_statuses(world: E2EWorld, sweeps: tuple[SweepConfig, ...]) -> tuple[str, ...]:
    """Read the current status of every sweep's map job."""
    async with world.database.sessions() as session:
        rows: tuple[MapJobRow, ...] = tuple(
            await session.scalars(
                select(MapJobRow).where(MapJobRow.id.in_([sweep.job_id for sweep in sweeps]))
            )
        )
    return tuple(row.status for row in sorted(rows, key=lambda row: row.id))


async def _pending_inventory(world: E2EWorld) -> int:
    """Count inventory outbox commands not yet delivered to the inventory store."""
    async with world.database.sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(InventoryOutboxRow)
                .where(InventoryOutboxRow.delivered_at.is_(None))
            )
            or 0
        )


async def _drive_until_terminal(
    world: E2EWorld, sweeps: tuple[SweepConfig, ...], worker: WorkerProcess
) -> None:
    """Run engine work and advance the fake clock in 1 s steps until every job ends.

    Inventory is applied asynchronously from the producer outbox (out-of-order deliveries
    retry 5 s later on the fake clock), so the loop also waits for the outbox to drain.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    deadline: float = loop.time() + OVERALL_TIMEOUT_SECONDS
    statuses: tuple[str, ...] = ()
    pending: int = -1
    while loop.time() < deadline:
        await world.container.worker.run_until_idle()
        statuses = await _job_statuses(world, sweeps)
        pending = await _pending_inventory(world)
        terminal: bool = len(statuses) == len(sweeps) and all(
            status in TERMINAL_JOB_STATUSES for status in statuses
        )
        if terminal and pending == 0:
            return
        if worker.process.returncode is not None:
            await _stop_worker(worker)
            pytest.fail(
                f"worker exited early ({worker.process.returncode}): {worker.output[-4000:]}"
            )
        world.clock.advance(timedelta(seconds=1))
        await asyncio.sleep(0.2)
    pytest.fail(
        f"not settled after {OVERALL_TIMEOUT_SECONDS} s: jobs {statuses}, "
        f"undelivered inventory commands {pending}"
    )


async def _assert_jobs(world: E2EWorld, sweeps: tuple[SweepConfig, ...]) -> None:
    """Every job succeeded with one succeeded shard and no failed resources."""
    async with world.database.sessions() as session:
        for sweep in sweeps:
            job: MapJobRow | None = await session.get(MapJobRow, sweep.job_id)
            assert job is not None and job.result_json is not None, sweep.kind
            assert job.status == MapJobStatus.SUCCEEDED.value, (sweep.kind, job.result_json)
            assert json.loads(job.result_json)["resources_failed"] == 0, job.result_json
            items: tuple[MapItemRow, ...] = tuple(
                await session.scalars(select(MapItemRow).where(MapItemRow.map_job_id == job.id))
            )
            assert len(items) == 1, (sweep.kind, len(items))
            for item in items:
                assert item.status == MapItemStatus.SUCCEEDED.value, (sweep.kind, item.error_code)
                assert item.summary_json is not None
                assert json.loads(item.summary_json)["resources_failed"] == 0, item.summary_json
    for sweep in sweeps:
        detail: httpx.Response = await world.client.get(
            f"/api/map-jobs/{sweep.job_id}", headers=bearer(world.admin_token)
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == MapJobStatus.SUCCEEDED.value, detail.text
        assert detail.json()["failed_shards"] == [], detail.text


async def _issues(world: E2EWorld, sweeps: tuple[SweepConfig, ...]) -> tuple[IssueView, ...]:
    """Read the police issues of the three sources."""
    async with world.database.sessions() as session:
        rows: tuple[IssueRow, ...] = tuple(
            await session.scalars(
                select(IssueRow).where(IssueRow.source.in_([sweep.source for sweep in sweeps]))
            )
        )
    return tuple(
        IssueView(
            row.source, row.display_locator, row.rule_class, row.status, row.exposure_visibility
        )
        for row in rows
    )


def _assert_findings(issues: tuple[IssueView, ...], sweeps: tuple[SweepConfig, ...]) -> None:
    """Findings, rule classes and applied exposures per source and resource."""
    expected: tuple[tuple[str, str, frozenset[str], str], ...] = (
        ("gdrive", "gdrive:f-text", frozenset(("credential", "email")), "anyone_with_link"),
        ("gdocs", "gdocs:f-doc", frozenset(("password",)), "external_shared"),
        ("gsheets", "gsheets:f-sheet", frozenset(("payment_card",)), "domain"),
    )
    for kind, locator, rule_classes, visibility in expected:
        sweep: SweepConfig = next(item for item in sweeps if item.kind == kind)
        found: tuple[IssueView, ...] = tuple(
            issue
            for issue in issues
            if issue.source == sweep.source and issue.locator == f"{sweep.source}:{locator}"
        )
        assert rule_classes <= {issue.rule_class for issue in found}, (kind, found)
        assert all(issue.status == IssueStatus.UNRESOLVED.value for issue in found), found
        assert {issue.visibility for issue in found} == {visibility}, (kind, found)
    for removed in ("gdrive:f-gone", "gdrive:f-trashed", "gdrive:f-image"):
        assert not any(issue.locator.endswith(":" + removed) for issue in issues), removed


async def _assert_inventories(world: E2EWorld, sweeps: tuple[SweepConfig, ...]) -> None:
    """Sheets inventory is a 3-row table; Drive and Docs assets are files."""
    async with world.database.sessions() as session:
        assets: tuple[DataAssetRow, ...] = tuple(
            await session.scalars(
                select(DataAssetRow).where(
                    DataAssetRow.collector_config_id.in_([sweep.config_id for sweep in sweeps])
                )
            )
        )
    by_locator: tuple[tuple[str, str, int | None], ...] = tuple(
        (asset.display_locator, asset.kind, asset.item_count) for asset in assets
    )
    sheet: tuple[tuple[str, str, int | None], ...] = tuple(
        entry for entry in by_locator if entry[0] == "police:e2e-gsheets:gsheets:f-sheet"
    )
    assert sheet == (("police:e2e-gsheets:gsheets:f-sheet", "table", 3),), by_locator
    for locator in (
        "police:e2e-gdrive:gdrive:f-text",
        "police:e2e-gdrive:gdrive:f-image",
        "police:e2e-gdocs:gdocs:f-doc",
    ):
        kinds: tuple[str, ...] = tuple(entry[1] for entry in by_locator if entry[0] == locator)
        assert kinds == ("file",), (locator, by_locator)


async def _assert_audit(world: E2EWorld) -> None:
    """No police result was rejected and no accepted result dropped members."""
    async with world.database.sessions() as session:
        rejected: int = int(
            await session.scalar(
                select(func.count())
                .select_from(AuditRecordRow)
                .where(AuditRecordRow.action == "police.result_rejected")
            )
            or 0
        )
        accepted: tuple[AuditRecordRow, ...] = tuple(
            await session.scalars(
                select(AuditRecordRow).where(AuditRecordRow.action == "police.result_accepted")
            )
        )
    assert rejected == 0
    assert accepted, "no police result was accepted"
    assert not any("dropped" in row.details_json for row in accepted), [
        row.details_json for row in accepted if "dropped" in row.details_json
    ]


def _assert_google_traffic(estate: FakeEstate) -> None:
    """The fake saw only expected requests and tokens for the users and the admin."""
    assert estate.unexpected == [], estate.unexpected
    subjects: set[str | None] = {grant.subject for grant in estate.grants}
    assert {estate.admin, *(user.email for user in estate.users)} <= subjects, subjects


def _assert_no_worker_leaks(output: str, estate: FakeEstate) -> None:
    """Worker output holds no sample, e-mail, file name or access token."""
    assert '"status":422' not in output
    assert "result rejected" not in output
    assert_no_leaks(output, "worker output")
    for sample in (aws_access_key_id(), email_address(), password_assignment(), credit_card()):
        assert sample.value not in output, sample.name
    private: list[str] = [estate.admin, "x@partner.example.org", ENROLLMENT_TOKEN, FINGERPRINT_KEY]
    private.extend(user.email for user in estate.users)
    private.extend(file.name for file in estate.files)
    private.extend(item.token for item in estate.issued)
    leaked: list[int] = [index for index, value in enumerate(private) if value in output]
    assert leaked == [], f"private values at indexes {leaked} appear in worker output"


@pytest.mark.asyncio
async def test_go_worker_sweeps_delegated_estate_offline(tmp_path: Path) -> None:
    """Three police sweeps run by the Go worker are accepted and applied by the Bureau."""
    _build_worker()
    private_key: rsa.RSAPrivateKey = generate_private_key()
    estate: FakeEstate = default_estate(private_key.public_key())
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_POLICE_ENROLLMENT_TOKEN", "env:E2E_ENROLL"),
            ("E2E_ENROLL", ENROLLMENT_TOKEN),
        ),
        OutboundRecorder(),
        independent_sessions=True,
    )
    google: FakeGoogle | None = None
    server: uvicorn.Server | None = None
    serving: asyncio.Task[None] | None = None
    worker: WorkerProcess | None = None
    try:
        google = start_fake_google(estate)
        service_account: Path = write_service_account(tmp_path, private_key, google.token_uri)
        port: int
        server, serving, port = await _serve_bureau(create_app(world.container))
        configs: tuple[tuple[str, str, str], ...] = await _create_configs(world)
        worker = await _start_worker(
            f"http://127.0.0.1:{port}", google, service_account, estate.admin
        )
        sweeps: tuple[SweepConfig, ...] = await _start_sweeps(world, configs)
        await _drive_until_terminal(world, sweeps, worker)

        await _assert_jobs(world, sweeps)
        _assert_findings(await _issues(world, sweeps), sweeps)
        await _assert_inventories(world, sweeps)
        await _assert_audit(world)
        _assert_google_traffic(estate)
        exit_code: int = await _stop_worker(worker)
        assert exit_code == 0, worker.output[-4000:]
        _assert_no_worker_leaks(worker.output, estate)
    finally:
        if worker is not None and worker.process.returncode is None:
            worker.process.kill()
            await asyncio.wait_for(worker.process.wait(), timeout=10)
        # Let an abandoned long-poll claim reach its fake-clock deadline before shutdown.
        world.clock.advance(timedelta(seconds=60))
        if server is not None and serving is not None:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=30)
        if google is not None:
            google.close()
        await world.close()
