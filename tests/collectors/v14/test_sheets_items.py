"""List row count reporting from complete Google Sheets scans."""

import httpx

from telescreen.collectors.google.adapter import GoogleAdapter
from telescreen.core.vocabulary import CollectorKind
from telescreen.ports.collectors import ResourceContent, ResourceRef
from tests.collectors.google.support import (
    GoogleTestBundle,
    adapters_for,
    drive_config,
    make_bundle,
)


async def test_complete_sheets_fetch_counts_nonempty_rows() -> None:
    """The item count sums rows containing at least one non-empty cell."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/sheet-1"):
            return httpx.Response(
                200,
                json={
                    "id": "sheet-1",
                    "name": "inventory",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                },
            )
        if request.url.path.endswith("/spreadsheets/sheet-1"):
            return httpx.Response(
                200,
                json={
                    "sheets": [{"properties": {"title": "One"}}, {"properties": {"title": "Two"}}]
                },
            )
        if request.url.path.endswith("/values:batchGet"):
            return httpx.Response(
                200,
                json={
                    "valueRanges": [
                        {"range": "'One'!A:ZZZ", "values": [["a"], [""], ["b"], ["e"]]},
                        {"range": "'Two'!A:ZZZ", "values": [["c"], ["d"]]},
                    ]
                },
            )
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "test", "expires_in": 3600})
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[2]
    try:
        ref: ResourceRef = adapter.ref_from_json('{"file_id":"sheet-1"}')
        content: ResourceContent = await adapter.fetch(drive_config(CollectorKind.GSHEETS), ref)
        assert content.complete
        assert content.items is not None
        assert content.items.count == 5
        adapter.max_bytes = 1
        partial: ResourceContent = await adapter.fetch(drive_config(CollectorKind.GSHEETS), ref)
        assert not partial.complete
        assert partial.items is None
    finally:
        await bundle.close()
