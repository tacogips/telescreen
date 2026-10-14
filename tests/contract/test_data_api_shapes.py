"""Compare real inventory API object keys with the web mock shape manifest."""

from pathlib import Path

import httpx
import pytest

from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world


def _keys(manifest: JsonValue, name: str) -> frozenset[str]:
    """Read one expected key set from the shape manifest."""
    value: JsonValue | None = manifest.get(name)
    assert value is not None
    items: tuple[JsonValue, ...] | None = value.as_list()
    assert items is not None
    keys: list[str] = []
    for item in items:
        key: str | None = item.as_str()
        if key is not None:
            keys.append(key)
    return frozenset(keys)


def _first_item(response: httpx.Response) -> JsonValue:
    """Return the first object in a paged API response."""
    items: JsonValue | None = parse_json(response.content).get("items")
    assert items is not None
    values: tuple[JsonValue, ...] | None = items.as_list()
    assert values is not None and values
    return values[0]


def _first_member_item(response: httpx.Response, member: str) -> JsonValue:
    """Return the first object in a named response array."""
    items: JsonValue | None = parse_json(response.content).get(member)
    assert items is not None
    values: tuple[JsonValue, ...] | None = items.as_list()
    assert values is not None and values
    return values[0]


@pytest.mark.asyncio
async def test_real_data_api_key_sets_match_v14_mock_manifest(tmp_path: Path) -> None:
    """Exercise real routes and require each object to match its mock shape exactly."""
    manifest: JsonValue = parse_json(
        Path("src/web/src/api/mock/fixtures/v14-shapes.json").read_text(encoding="utf-8")
    )
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder(), independent_sessions=True)
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "p08-shapes",
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
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "p08-shapes-ingest", frozenset((ApiScope.INGEST,)), None)
        ingest: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest_token.token),
            json={
                "source": "custom:p08-shapes",
                "resources": [
                    {
                        "locator": "table/shape",
                        "title": "Shape table",
                        "item_keys": ["row-a", "row-b"],
                    }
                ],
            },
        )
        assert ingest.status_code == 200, ingest.text
        await world.drain()

        created_field: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin_token),
            json={
                "key": "purpose",
                "label": "Purpose",
                "field_type": "text",
                "required": False,
                "role": "purpose",
            },
        )
        assert created_field.status_code == 201, created_field.text
        listed: httpx.Response = await world.client.get(
            "/api/data/assets", headers=bearer(world.admin_token)
        )
        assert listed.status_code == 200, listed.text
        asset: JsonValue = _first_item(listed)
        asset_id_value: JsonValue | None = asset.get("id")
        assert asset_id_value is not None and asset_id_value.as_str() is not None
        asset_id: str = asset_id_value.as_str() or ""
        update: httpx.Response = await world.client.put(
            f"/api/data/assets/{asset_id}/fields",
            headers=bearer(world.admin_token),
            json={"values": [{"key": "purpose", "value_json": '"Operations"'}]},
        )
        assert update.status_code == 200, update.text

        detail: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}", headers=bearer(world.admin_token)
        )
        events: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/events", headers=bearer(world.admin_token)
        )
        items: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/items", headers=bearer(world.admin_token)
        )
        series: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/item-series", headers=bearer(world.admin_token)
        )
        fields: httpx.Response = await world.client.get(
            "/api/data/fields", headers=bearer(world.admin_token)
        )
        counts: httpx.Response = await world.client.get(
            "/api/data/counts", headers=bearer(world.admin_token)
        )
        for response in (detail, events, items, series, fields, counts):
            assert response.status_code == 200, response.text

        assert frozenset(asset.member_names()) == _keys(manifest, "DataAsset")
        detail_body: JsonValue = parse_json(detail.content)
        assert frozenset(detail_body.member_names()) == _keys(manifest, "DataAssetDetail")
        detail_fields: JsonValue | None = detail_body.get("fields")
        field_slots: tuple[JsonValue, ...] | None = (
            detail_fields.as_list() if detail_fields is not None else None
        )
        assert field_slots is not None and field_slots
        assert frozenset(field_slots[0].member_names()) == _keys(manifest, "DataFieldSlot")
        assert frozenset(_first_item(events).member_names()) == _keys(manifest, "DataEvent")
        assert frozenset(_first_item(items).member_names()) == _keys(manifest, "DataItem")
        assert frozenset(_first_item(fields).member_names()) == _keys(manifest, "DataField")
        assert frozenset(parse_json(counts.content).member_names()) == _keys(manifest, "DataCounts")
        assert frozenset(_first_member_item(series, "points").member_names()) == _keys(
            manifest, "DataItemSeriesPoint"
        )
    finally:
        await world.close()
