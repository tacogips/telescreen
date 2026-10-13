"""Production-path end-to-end flows for v1.4 inventory lifecycle and item changes."""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.api.routers.ingest import IngestScanBody, ResourceBody, SegmentBody
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    ApiScope,
    CollectorKind,
    DataAssetState,
    DataEventKind,
    PoliceTaskStatus,
)
from telescreen.db.models import (
    DataAssetEventRow,
    DataAssetItemRow,
    DataAssetRow,
    PoliceTaskRow,
)
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    ResourceContent,
    ResourceRef,
    WatchState,
)
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    Manifest,
    Mode,
    Registration,
    ResourceInventory,
    ResultEnvelope,
    ResultStatus,
    TaskEnvelope,
)
from telescreen_police_sdk.worker import PullWorker
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.e2e.test_flow_sweep import (
    GitHubSweepMock,
    _launch_sweep,
    _make_sweep_world,
)
from tests.support.samples import aws_access_key_id

# A ref whose JSON carries an e-mail address is skipped by the redactor before listing.
RAW_LISTING_EMAIL: str = "listing-owner@example.com"


@pytest.mark.asyncio
async def test_e1_e2_e3_e7_inventory_modes_items_lifecycle_and_export(tmp_path: Path) -> None:
    """Exercise push discovery, item deltas, lifecycle signals, and safe CSV export."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:

        async def collector(name: str, mode: str) -> str:
            response: httpx.Response = await world.client.post(
                "/api/collectors",
                headers=bearer(world.admin_token),
                json={
                    "name": name,
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": "{}",
                    "credential_ref": None,
                    "enabled": True,
                    "inventory_mode": mode,
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["id"]

        sensitive_id: str = await collector("v14-sensitive", "sensitive_only")
        await collector("v14-all", "all")
        await collector("v14-off", "off")
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "v14-inventory-ingest", frozenset((ApiScope.INGEST,)), None)

        async def ingest(
            source: str,
            locator: str,
            item_keys: list[str] | None = None,
            complete: bool = True,
            removed: bool = False,
            sensitive: bool = False,
        ) -> httpx.Response:
            resource: ResourceBody = ResourceBody(
                locator=locator,
                segments=([SegmentBody("line:1", aws_access_key_id().value)] if sensitive else []),
                complete=complete,
                removed=removed,
                item_keys=item_keys,
            )
            return await world.client.post(
                "/api/ingest/v1/scan",
                headers=bearer(ingest_token.token),
                json=asdict(IngestScanBody(source, [resource])),
            )

        no_finding: httpx.Response = await ingest("custom:v14-sensitive", "clean.txt")
        assert no_finding.status_code == 200, no_finding.text
        sensitive_assets: httpx.Response = await world.client.get(
            "/api/data/assets?source=custom:v14-sensitive", headers=bearer(world.admin_token)
        )
        assert sensitive_assets.json()["items"] == []
        finding: httpx.Response = await ingest("custom:v14-sensitive", "secret.txt", sensitive=True)
        assert finding.status_code == 200, finding.text
        sensitive_assets = await world.client.get(
            "/api/data/assets?source=custom:v14-sensitive", headers=bearer(world.admin_token)
        )
        assert len(sensitive_assets.json()["items"]) == 1

        safe_first: httpx.Response = await ingest(
            "custom:v14-all",
            "records.csv",
            ["raw-row-a", "raw-row-b", "raw-row-c", "raw-row-d", "raw-row-e"],
        )
        assert safe_first.status_code == 200, safe_first.text
        all_assets: httpx.Response = await world.client.get(
            "/api/data/assets?source=custom:v14-all", headers=bearer(world.admin_token)
        )
        asset_id: str = all_assets.json()["items"][0]["id"]
        first_items: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/items", headers=bearer(world.admin_token)
        )
        assert first_items.status_code == 200, first_items.text
        first_prefixes: tuple[str, ...] = tuple(
            item["key_prefix"] for item in first_items.json()["items"]
        )
        assert len(first_prefixes) == 5
        assert all(len(prefix) == 12 for prefix in first_prefixes)
        assert all("raw-row" not in prefix for prefix in first_prefixes)

        partial: httpx.Response = await ingest(
            "custom:v14-all", "records.csv", ["raw-row-a", "raw-row-b", "raw-row-c"], False
        )
        assert partial.status_code == 200, partial.text
        async with world.database.sessions() as session:
            item_rows: tuple[DataAssetItemRow, ...] = tuple(
                await session.scalars(
                    select(DataAssetItemRow).where(DataAssetItemRow.asset_id == asset_id)
                )
            )
            assert sum(row.removed_at is not None for row in item_rows) == 0
        complete: httpx.Response = await ingest(
            "custom:v14-all", "records.csv", ["raw-row-a", "raw-row-b", "raw-row-c"]
        )
        assert complete.status_code == 200, complete.text
        events: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/events", headers=bearer(world.admin_token)
        )
        assert events.status_code == 200, events.text
        assert any(
            event["kind"] == "items_changed" and event["items_removed"] == 2
            for event in events.json()["items"]
        )

        removed: httpx.Response = await ingest(
            "custom:v14-sensitive", "secret.txt", complete=True, removed=True
        )
        assert removed.status_code == 200, removed.text
        issue_ids: list[str] = finding.json()["issue_ids"]
        if issue_ids:
            issue: httpx.Response = await world.client.get(
                f"/api/issues/{issue_ids[0]}", headers=bearer(world.admin_token)
            )
            assert issue.status_code == 200, issue.text
        restored: httpx.Response = await ingest(
            "custom:v14-sensitive", "secret.txt", sensitive=True
        )
        assert restored.status_code == 200, restored.text
        sensitive_assets = await world.client.get(
            "/api/data/assets?source=custom:v14-sensitive", headers=bearer(world.admin_token)
        )
        sensitive_asset_id: str = sensitive_assets.json()["items"][0]["id"]
        sensitive_events: httpx.Response = await world.client.get(
            f"/api/data/assets/{sensitive_asset_id}/events",
            headers=bearer(world.admin_token),
        )
        assert sensitive_events.status_code == 200, sensitive_events.text
        assert any(event["kind"] == "deleted" for event in sensitive_events.json()["items"])
        assert any(event["kind"] == "restored" for event in sensitive_events.json()["items"])

        patch_off: httpx.Response = await world.client.patch(
            f"/api/collectors/{sensitive_id}",
            headers=bearer(world.admin_token),
            json={
                "name": "v14-sensitive",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "off",
            },
        )
        assert patch_off.status_code == 200, patch_off.text
        stopped: httpx.Response = await world.client.get(
            "/api/data/assets?source=custom:v14-sensitive", headers=bearer(world.admin_token)
        )
        assert stopped.json()["items"][0]["state"] == "unknown"
        ignored: httpx.Response = await ingest("custom:v14-off", "ignored.txt")
        assert ignored.status_code == 200, ignored.text
        off_assets: httpx.Response = await world.client.get(
            "/api/data/assets?source=custom:v14-off", headers=bearer(world.admin_token)
        )
        assert off_assets.json()["items"] == []

        registered: httpx.Response = await world.client.post(
            "/api/data/assets",
            headers=bearer(world.admin_token),
            json={
                "name": "=SUM(1,1)",
                "source": "manual",
                "locator": "report/formula",
                "kind": "file",
                "item_count": 2,
            },
        )
        assert registered.status_code == 201, registered.text
        exported: httpx.Response = await world.client.get(
            "/api/data/export", headers=bearer(world.admin_token)
        )
        assert exported.status_code == 200, exported.text
        assert "'=SUM(1,1)" in exported.text

        async with world.database.sessions() as session:
            deletion_events: tuple[DataAssetEventRow, ...] = tuple(
                await session.scalars(
                    select(DataAssetEventRow).where(
                        DataAssetEventRow.kind.in_(("deleted", "restored"))
                    )
                )
            )
            assert {event.kind for event in deletion_events} == {"deleted", "restored"}
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_e4_police_pull_inventory_creates_rekeyed_items(tmp_path: Path) -> None:
    """Receive a Police inventory through its registered pull worker and result API."""
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_POLICE_ENROLLMENT_TOKEN", "env:E2E_ENROLL"),
            ("E2E_ENROLL", "e2e-enrollment-" + "x" * 32),
        ),
        OutboundRecorder(),
    )
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v14-police-inventory",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "all",
            },
        )
        assert collector.status_code == 200, collector.text
        collector_id: str = collector.json()["id"]
        client: BureauClient = BureauClient("http://test", world.client)
        registration: Registration = await client.register(
            Manifest("v14-inventory-worker", "1.0", Mode.PULL, ("detect",), (), 1),
            "e2e-enrollment-" + "x" * 32,
        )
        assert registration.worker_token is not None
        task_id: str = new_id()
        async with world.database.sessions.begin() as session:
            session.add(
                PoliceTaskRow(
                    id=task_id,
                    task_type="detect",
                    labels="",
                    input_json="{}",
                    status=PoliceTaskStatus.QUEUED.value,
                    deadline=world.clock.now() + timedelta(minutes=5),
                    collector_config_id=collector_id,
                    created_at=world.clock.now(),
                )
            )

        stop: asyncio.Event = asyncio.Event()
        raw_fingerprint: str = "a" * 64

        async def detect(envelope: TaskEnvelope) -> ResultEnvelope:
            result: ResultEnvelope = ResultEnvelope(
                envelope.task_id,
                ResultStatus.COMPLETED,
                complete_locators=("police/records",),
                resource_inventories=(
                    ResourceInventory("police/records", (raw_fingerprint,), kind="list"),
                ),
            )
            stop.set()
            return result

        worker: PullWorker = PullWorker(
            client,
            registration.worker_id,
            registration.worker_token,
            (("detect", detect),),
            1,
            30.0,
            1,
        )
        await worker.run(stop)
        async with world.database.sessions() as session:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            assert task is not None and task.status == PoliceTaskStatus.COMPLETED.value, (
                task.result_json if task is not None else "missing Police task"
            )
            asset: DataAssetRow | None = await session.scalar(
                select(DataAssetRow).where(
                    DataAssetRow.collector_config_id == collector_id,
                    DataAssetRow.display_locator == "police:v14-police-inventory:police/records",
                )
            )
            assert asset is not None
            items: tuple[DataAssetItemRow, ...] = tuple(
                await session.scalars(
                    select(DataAssetItemRow).where(DataAssetItemRow.asset_id == asset.id)
                )
            )
            assert len(items) == 1
            assert items[0].fingerprint != raw_fingerprint
            assert len(items[0].fingerprint) == 64
    finally:
        await world.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("partial", "expected_state"),
    ((False, DataAssetState.DELETED.value), (True, DataAssetState.PRESENT.value)),
)
async def test_e3_sweep_reconciles_inventory_only_after_success(
    tmp_path: Path, partial: bool, expected_state: str
) -> None:
    """A successful sweep removes absent inventory; a partial sweep preserves it."""
    setup: tuple[E2EWorld, str, GitHubSweepMock] = await _make_sweep_world(
        tmp_path, fail_repo_b=partial
    )
    world: E2EWorld = setup[0]
    collector_id: str = setup[1]
    try:
        async with world.database.sessions() as session:
            initial_assets: tuple[DataAssetRow, ...] = tuple(
                await session.scalars(
                    select(DataAssetRow).where(DataAssetRow.collector_config_id == collector_id)
                )
            )
            assert len(initial_assets) == 1
            asset_id: str = initial_assets[0].id
            assert initial_assets[0].state == DataAssetState.PRESENT.value
        response: httpx.Response = await _launch_sweep(world, collector_id)
        assert response.status_code == 200, response.text
        await world.drain()
        async with world.database.sessions() as session:
            asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
            assert asset is not None and asset.state == expected_state
    finally:
        await world.close()


@dataclass
class ListingAdapter:
    """Slack-kind adapter fake whose listing is steered by the test between runs."""

    kind: CollectorKind = CollectorKind.SLACK
    listed: tuple[str, ...] = ()
    skipped_ref_json: str | None = None
    full_listing: bool = True
    fail_listing: bool = False

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Accept every event; this flow never sends webhooks."""
        del event, secrets
        return EventCheck(EventCheckKind.VALID)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Return no event resources."""
        del event, config
        return EventResources((), ())

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """Return one page with the configured refs, or fail the listing."""
        del config, cursor
        if self.fail_listing:
            raise RuntimeError("listing unavailable")
        refs: tuple[ResourceRef, ...] = tuple(
            ResourceRef(self.kind, json.dumps({"id": name}), name) for name in self.listed
        )
        if self.skipped_ref_json is not None:
            refs = (*refs, ResourceRef(self.kind, self.skipped_ref_json, "skipped"))
        return ChangePage(refs, (), None, self.full_listing, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Return an empty, complete, sweep-covered resource."""
        return ResourceContent(
            config.kind.value, config.id, (), True, True, ref.raw_locator, ref.raw_locator, ref
        )

    async def renew_watch(self, config: CollectorConfig, current: WatchState | None) -> WatchState:
        """Return a fixed watch; this flow never renews watches."""
        del config, current
        return WatchState("channel-1", "resource-1", datetime(2026, 12, 1, tzinfo=UTC))

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Rebuild a listed ref from its stored JSON."""
        name: str = str(json.loads(ref_json)["id"])
        return ResourceRef(self.kind, ref_json, name)


@pytest.mark.asyncio
async def test_e3_full_listing_deletes_absent_asset_only_when_authoritative(
    tmp_path: Path,
) -> None:
    """Only a completed full listing with no skipped ref deletes; a later sighting restores."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    adapter: ListingAdapter = ListingAdapter(listed=("doc-a", "doc-b"))
    world.container.collector_registry.register(adapter)
    try:
        created: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v14-e3-listing",
                "kind": "slack",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": json.dumps({"channel_ids": ["C123"], "include_files": False}),
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "all",
            },
        )
        assert created.status_code == 200, created.text
        collector_id: str = created.json()["id"]

        async def run() -> None:
            world.clock.advance(timedelta(minutes=1))
            response: httpx.Response = await world.client.post(
                f"/api/collectors/{collector_id}/run", headers=bearer(world.admin_token)
            )
            assert response.status_code == 200, response.text
            await world.drain()

        async def states() -> tuple[tuple[str, str], ...]:
            async with world.database.sessions() as session:
                rows: tuple[DataAssetRow, ...] = tuple(
                    await session.scalars(
                        select(DataAssetRow)
                        .where(DataAssetRow.collector_config_id == collector_id)
                        .order_by(DataAssetRow.display_locator)
                    )
                )
            return tuple((row.display_locator, row.state) for row in rows)

        async def event_kinds(locator: str) -> list[str]:
            async with world.database.sessions() as session:
                asset: DataAssetRow | None = await session.scalar(
                    select(DataAssetRow).where(
                        DataAssetRow.collector_config_id == collector_id,
                        DataAssetRow.display_locator == locator,
                    )
                )
                assert asset is not None
                events: tuple[DataAssetEventRow, ...] = tuple(
                    await session.scalars(
                        select(DataAssetEventRow)
                        .where(DataAssetEventRow.asset_id == asset.id)
                        .order_by(DataAssetEventRow.recorded_at)
                    )
                )
            return [event.kind for event in events]

        present: str = DataAssetState.PRESENT.value
        await run()
        assert await states() == (("doc-a", present), ("doc-b", present))

        # An incremental listing that omits doc-b is not authoritative.
        adapter.listed = ("doc-a",)
        adapter.full_listing = False
        await run()
        assert await states() == (("doc-a", present), ("doc-b", present))

        # A full listing with a redactor-skipped ref cannot prove doc-b is gone.
        adapter.full_listing = True
        adapter.skipped_ref_json = json.dumps({"id": RAW_LISTING_EMAIL})
        await run()
        assert await states() == (("doc-a", present), ("doc-b", present))

        # A failed listing never infers deletion.
        adapter.skipped_ref_json = None
        adapter.fail_listing = True
        await run()
        assert await states() == (("doc-a", present), ("doc-b", present))
        assert DataEventKind.DELETED.value not in await event_kinds("doc-b")

        # A completed full listing without skipped refs deletes doc-b only.
        adapter.fail_listing = False
        await run()
        assert await states() == (("doc-a", present), ("doc-b", DataAssetState.DELETED.value))
        assert (await event_kinds("doc-b")).count(DataEventKind.DELETED.value) == 1

        # Seeing doc-b again restores it.
        adapter.listed = ("doc-a", "doc-b")
        await run()
        assert await states() == (("doc-a", present), ("doc-b", present))
        assert (await event_kinds("doc-b"))[-1] == DataEventKind.RESTORED.value

        # With inventory off, a full listing that omits doc-a deletes nothing.
        patched: httpx.Response = await world.client.patch(
            f"/api/collectors/{collector_id}",
            headers=bearer(world.admin_token),
            json={
                "name": "v14-e3-listing",
                "kind": "slack",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": json.dumps({"channel_ids": ["C123"], "include_files": False}),
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "off",
            },
        )
        assert patched.status_code == 200, patched.text
        adapter.listed = ("doc-b",)
        await run()
        unknown: str = DataAssetState.UNKNOWN.value
        assert await states() == (("doc-a", unknown), ("doc-b", unknown))
        assert (await event_kinds("doc-a"))[-1] == DataEventKind.TRACKING_STOPPED.value
        assert DataEventKind.DELETED.value not in await event_kinds("doc-a")
    finally:
        await world.close()
