"""Google v1.1 exposure, owner, placement, timestamp, and coverage tests."""

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import TypeAdapter

from telescreen.collectors.google.adapter import GoogleAdapter
from telescreen.collectors.google.drive import DriveFile, DrivePermission, DrivePermissionPage
from telescreen.collectors.google.exposure import drive_exposure
from telescreen.core.vocabulary import (
    CoverageState,
    IdentityKind,
    PlacementKind,
    UnsupportedReason,
    Visibility,
)
from telescreen.ports.collectors import (
    Coverage,
    Exposure,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
)
from tests.collectors.google.support import (
    GoogleTestBundle,
    adapters_for,
    drive_config,
    make_bundle,
)


def test_drive_exposure_reduces_permissions_to_visibility_and_counts() -> None:
    """Drive grants are reduced to visibility and external counts only."""
    assert (
        drive_exposure(
            (DrivePermission("anyone", "reader", None, None, True),), None, ()
        ).visibility
        is Visibility.PUBLIC
    )
    assert drive_exposure(
        (DrivePermission("anyone", "reader", None, None, False),), None, ()
    ).visibility is (Visibility.ANYONE_WITH_LINK)
    external: tuple[DrivePermission, ...] = (
        DrivePermission("user", "reader", None, "one@outside.invalid", False),
        DrivePermission("group", "reader", None, "two@outside.invalid", False),
    )
    external_exposure: Exposure = drive_exposure(external, None, ("internal.invalid",))
    assert external_exposure.visibility is Visibility.EXTERNAL_SHARED
    assert external_exposure.external_principal_count == 2
    domain_exposure: Exposure = drive_exposure(
        (DrivePermission("domain", "reader", "INTERNAL.INVALID", None, False),),
        "drive-1",
        ("internal.invalid",),
    )
    assert domain_exposure.visibility is Visibility.DOMAIN
    assert domain_exposure.shared_drive
    assert domain_exposure == drive_exposure(
        (DrivePermission("domain", "reader", "internal.invalid", None, False),),
        "drive-1",
        ("internal.invalid",),
    )
    assert (
        drive_exposure(
            (
                DrivePermission("user", "owner", None, "owner@internal.invalid", False),
                DrivePermission("user", "writer", None, "member@internal.invalid", False),
            ),
            None,
            ("internal.invalid",),
        ).visibility
        is Visibility.INTERNAL
    )
    assert (
        drive_exposure(
            (DrivePermission("user", "owner", None, "owner@internal.invalid", False),),
            None,
            ("internal.invalid",),
        ).visibility
        is Visibility.PRIVATE
    )
    assert (
        drive_exposure(
            (DrivePermission("user", "writer", None, "member@somewhere.invalid", False),),
            None,
            (),
        ).visibility
        is Visibility.UNKNOWN
    )


def test_drive_principal_aliases_parse_without_repr_leaks() -> None:
    """Transient owner and permission principals are parsed but hidden from repr."""
    drive_file: DriveFile = TypeAdapter(DriveFile).validate_json(
        b'{"id":"file-1","owners":[{"emailAddress":"owner@internal.invalid"}]}'
    )
    permission_page: DrivePermissionPage = TypeAdapter(DrivePermissionPage).validate_json(
        b'{"permissions":[{"type":"user","role":"reader",'
        b'"emailAddress":"grantee@external.invalid","domain":"external.invalid"}]}'
    )
    owner_repr: str = repr(drive_file.owners[0])
    file_repr: str = repr(drive_file)
    permission: DrivePermission = permission_page.permissions[0]
    permission_repr: str = repr(permission)

    assert drive_file.owners[0].email_address == "owner@internal.invalid"
    assert permission.email_address == "grantee@external.invalid"
    assert permission.domain == "external.invalid"
    assert "@" not in file_repr and "internal.invalid" not in file_repr
    assert "@" not in owner_repr and "internal.invalid" not in owner_repr
    assert "@" not in permission_repr and "external.invalid" not in permission_repr


async def test_fetch_adds_transient_context_and_reads_only_paginated_permissions() -> None:
    """A Drive fetch includes safe context and sends GET requests only."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/file-1/permissions"):
            if request.url.params.get("pageToken") == "next":
                return httpx.Response(
                    200,
                    json={
                        "permissions": [
                            {
                                "type": "user",
                                "role": "writer",
                                "emailAddress": "peer@internal.invalid",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "permissions": [
                        {"type": "user", "role": "owner", "emailAddress": "owner@internal.invalid"}
                    ],
                    "nextPageToken": "next",
                },
            )
        if request.url.path.endswith("/files/file-1"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"transient content")
            return httpx.Response(
                200,
                json={
                    "id": "file-1",
                    "name": "private-name.txt",
                    "mimeType": "text/plain",
                    "driveId": "drive-1",
                    "parents": ["folder-a", "folder-b"],
                    "owners": [{"emailAddress": "owner@internal.invalid"}],
                    "modifiedTime": "2026-10-09T12:34:56Z",
                    "size": "17",
                },
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    adapter.internal_domains = ("internal.invalid",)
    try:
        ref: ResourceRef = adapter.ref_from_json('{"file_id":"file-1"}')
        content: ResourceContent = await adapter.fetch(drive_config(), ref)
        assert content.complete
        assert content.exposure is not None
        assert content.exposure.visibility is Visibility.INTERNAL
        assert content.owner is not None and content.owner.kind is IdentityKind.EMAIL
        assert content.owner.value == "owner@internal.invalid"
        assert "@" not in repr(content)
        assert tuple((item.kind, item.value) for item in content.placement) == (
            (PlacementKind.GDRIVE_DRIVE, "drive-1"),
            (PlacementKind.GDRIVE_FOLDER, "folder-a"),
            (PlacementKind.GDRIVE_FOLDER, "folder-b"),
        )
        assert content.modified_at == datetime(2026, 10, 9, 12, 34, 56, tzinfo=UTC)
        assert content.coverage == Coverage(CoverageState.SCANNED)
        assert (
            len(
                [
                    request
                    for request in bundle.api_requests
                    if request.url.path.endswith("/permissions")
                ]
            )
            == 2
        )
        assert all(request.method == "GET" for request in bundle.api_requests)
        assert all(
            "permissions(" in request.url.params.get("fields", "")
            for request in bundle.api_requests
            if request.url.path.endswith("/permissions")
        )
    finally:
        await bundle.close()


async def test_permission_failure_keeps_content_and_exposure_unknown() -> None:
    """A permissions endpoint failure does not fail the content scan."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/permissions"):
            return httpx.Response(500, json={"message": "private"})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"content")
        return httpx.Response(200, json={"id": "file-1", "mimeType": "text/plain"})

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    try:
        content: ResourceContent = await adapter.fetch(
            drive_config(), adapter.ref_from_json('{"file_id":"file-1"}')
        )
        assert content.complete
        assert content.exposure is None
        assert content.coverage is not None and content.coverage.state is CoverageState.SCANNED
    finally:
        await bundle.close()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, CoverageState.PERMISSION_DENIED),
        (404, None),
    ],
)
async def test_fetch_maps_forbidden_and_preserves_gone_resource(
    status: int, expected: CoverageState | None
) -> None:
    """A non-quota 403 is permission coverage while Drive 404 stays gone."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "private"})

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    try:
        try:
            content: ResourceContent = await adapter.fetch(
                drive_config(), adapter.ref_from_json('{"file_id":"file-1"}')
            )
        except ResourceGoneError:
            assert expected is None
        else:
            assert expected is CoverageState.PERMISSION_DENIED
            assert content.coverage == Coverage(expected)
        if status == 404:
            try:
                await bundle.drive.files_get_meta("file-1")
            except ResourceGoneError:
                pass
            else:
                raise AssertionError("Drive 404 did not raise ResourceGoneError")
    finally:
        await bundle.close()


async def test_quota_403_remains_rate_limited() -> None:
    """A quota reason remains RateLimited rather than becoming FORBIDDEN."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
        )

    bundle: GoogleTestBundle = make_bundle(handler)
    try:
        with pytest.raises(RateLimited):
            await bundle.drive.files_get_meta("file-1")
    finally:
        await bundle.close()


async def test_image_resource_is_marked_unsupported() -> None:
    """Image MIME types are returned as unsupported coverage without downloading bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/permissions"):
            return httpx.Response(200, json={"permissions": []})
        return httpx.Response(
            200,
            json={"id": "image-1", "mimeType": "image/png", "owners": []},
        )

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    try:
        content: ResourceContent = await adapter.fetch(
            drive_config(), adapter.ref_from_json('{"file_id":"image-1"}')
        )
        assert content.coverage == Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.IMAGE)
        assert not any(request.url.params.get("alt") == "media" for request in bundle.api_requests)
        assert all(request.method == "GET" for request in bundle.api_requests)
    finally:
        await bundle.close()


async def test_oversized_text_is_partial_with_known_truncation_count() -> None:
    """Oversized fetched content reports its known unscanned byte count."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/permissions"):
            return httpx.Response(200, json={"permissions": []})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"123456")
        return httpx.Response(200, json={"id": "file-1", "mimeType": "text/plain"})

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle, max_bytes=3)[0]
    try:
        content: ResourceContent = await adapter.fetch(
            drive_config(), adapter.ref_from_json('{"file_id":"file-1"}')
        )
        assert not content.complete
        assert content.coverage == Coverage(CoverageState.PARTIAL, truncated_bytes=3)
    finally:
        await bundle.close()
