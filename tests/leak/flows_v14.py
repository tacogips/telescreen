"""Seven production-path data inventory flows for the raw-value leak matrix."""

from dataclasses import asdict
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from telescreen.api.routers.ingest import IngestScanBody, ResourceBody, SegmentBody
from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import DataAssetRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.leak.harness import LeakWorld, auth
from tests.leak.sinks import (
    api_responses,
    database_text,
    logs,
    outbound_bodies,
    search_documents,
)
from tests.support.samples import Sample, all_samples, aws_access_key_id, find_leaks


async def run_v14_flows(world: LeakWorld, smtp_messages: list[str]) -> tuple[str, ...]:
    """Exercise seven v1.4 paths and inspect every sink after each one."""
    sample: Sample = aws_access_key_id()
    collector: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v14-leak-matrix",
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
    world.recorder.responses.append(collector.text)
    assert collector.status_code == 200, collector.text
    source: str = "custom:v14-leak-matrix"
    locator: str = "records/" + sample.value
    async with world.database.sessions.begin() as session:
        ingest_token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session, "v14-leak-ingest", frozenset((ApiScope.INGEST,)), None
        )

    async def ingest(removed: bool = False) -> httpx.Response:
        body: ResourceBody = ResourceBody(
            locator=locator,
            title=sample.value,
            segments=[] if removed else [SegmentBody("line:1", sample.value)],
            complete=True,
            removed=removed,
            item_keys=None if removed else [sample.value, "safe-row"],
        )
        response: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=auth(ingest_token.token),
            json=asdict(IngestScanBody(source, [body])),
        )
        world.recorder.responses.append(response.text)
        return response

    created: httpx.Response = await ingest()
    assert created.status_code == 200, created.text
    assert sample.value not in created.text
    await _assert_flow_sinks(world, smtp_messages)

    listing: httpx.Response = await world.client.get(
        "/api/data/assets?source=" + source, headers=auth(world.admin_token)
    )
    world.recorder.responses.append(listing.text)
    assert listing.status_code == 200, listing.text
    assets: tuple[DataAssetRow, ...]
    async with world.database.sessions() as session:
        assets = tuple(
            await session.scalars(select(DataAssetRow).where(DataAssetRow.source == source))
        )
    assert len(assets) == 1
    asset_id: str = assets[0].id
    detail: httpx.Response = await world.client.get(
        f"/api/data/assets/{asset_id}", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(detail.text)
    assert detail.status_code == 200, detail.text
    assert sample.value not in detail.text
    await _assert_flow_sinks(world, smtp_messages)

    for suffix in ("items", "events", "item-series"):
        response: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/{suffix}", headers=auth(world.admin_token)
        )
        world.recorder.responses.append(response.text)
        assert response.status_code == 200, response.text
        assert sample.value not in response.text
    await _assert_flow_sinks(world, smtp_messages)

    exported: httpx.Response = await world.client.get(
        "/api/data/export", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(exported.text)
    assert exported.status_code == 200, exported.text
    assert sample.value not in exported.text
    await _assert_flow_sinks(world, smtp_messages)

    purpose: httpx.Response = await world.client.post(
        "/api/data/fields",
        headers=auth(world.admin_token),
        json={"key": "purpose", "label": "Purpose", "field_type": "text"},
    )
    world.recorder.responses.append(purpose.text)
    assert purpose.status_code == 201, purpose.text
    rejected: httpx.Response = await world.client.put(
        f"/api/data/assets/{asset_id}/fields",
        headers=auth(world.admin_token),
        json={"values": [{"key": "purpose", "value_json": f'"{sample.value}"'}]},
    )
    world.recorder.responses.append(rejected.text)
    assert rejected.status_code == 422
    assert sample.value not in rejected.text
    safe_value: httpx.Response = await world.client.put(
        f"/api/data/assets/{asset_id}/fields",
        headers=auth(world.admin_token),
        json={"values": [{"key": "purpose", "value_json": '"Operations"'}]},
    )
    world.recorder.responses.append(safe_value.text)
    assert safe_value.status_code == 200, safe_value.text
    await _assert_flow_sinks(world, smtp_messages)

    removed: httpx.Response = await ingest(True)
    assert removed.status_code == 200, removed.text
    restored: httpx.Response = await ingest()
    assert restored.status_code == 200, restored.text
    await _assert_flow_sinks(world, smtp_messages)

    member: httpx.Response = await world.client.post(
        "/api/members",
        headers=auth(world.admin_token),
        json={
            "handle": "v14-overdue-owner",
            "display_name": "Inventory Owner",
            "email": "inventory-owner@example.invalid",
        },
    )
    world.recorder.responses.append(member.text)
    assert member.status_code == 201, member.text
    owner_field: httpx.Response = await world.client.post(
        "/api/data/fields",
        headers=auth(world.admin_token),
        json={
            "key": "owner",
            "label": "Owner",
            "field_type": "reference",
            "role": "owner",
        },
    )
    due_field: httpx.Response = await world.client.post(
        "/api/data/fields",
        headers=auth(world.admin_token),
        json={
            "key": "deletion_due",
            "label": "Deletion due",
            "field_type": "date",
            "role": "deletion_due",
        },
    )
    assert owner_field.status_code == 201, owner_field.text
    assert due_field.status_code == 201, due_field.text
    assigned: httpx.Response = await world.client.put(
        f"/api/data/assets/{asset_id}/fields",
        headers=auth(world.admin_token),
        json={
            "values": [
                {"key": "owner", "value_json": '"member:v14-overdue-owner"'},
                {"key": "deletion_due", "value_json": '"2026-10-09"'},
            ]
        },
    )
    world.recorder.responses.append(assigned.text)
    assert assigned.status_code == 200, assigned.text
    world.clock.set(datetime(2026, 10, 12, 9, 0, tzinfo=UTC))
    await world.container.worker.scheduler.tick()
    await world.drain()
    assert any("DATA-" in message for message in smtp_messages)
    await _assert_flow_sinks(world, smtp_messages)
    return tuple(item.value for item in all_samples())


async def _assert_flow_sinks(world: LeakWorld, smtp_messages: list[str]) -> None:
    """Reject every synthetic raw value from each inventory flow's observable sinks."""
    sinks: tuple[tuple[str, str], ...] = (
        await database_text(world)
        + logs(world, world.log_buffer.getvalue())
        + search_documents(world)
        + outbound_bodies(world)
        + api_responses(world)
        + tuple((f"smtp:{index}", message) for index, message in enumerate(smtp_messages))
    )
    for sample in all_samples():
        for label, text in sinks:
            assert find_leaks(text) == (), (sample.name, label)
            assert sample.value not in text, (sample.name, label)
