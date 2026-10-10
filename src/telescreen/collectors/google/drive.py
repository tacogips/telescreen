"""Typed Google Drive v3 HTTP operations used by event and sweep collectors."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Annotated, TypeVar
from urllib.parse import quote

import httpx
from pydantic import Field, TypeAdapter, ValidationError

from telescreen.collectors.google.auth import GoogleAuth
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.collectors import RateLimited, ResourceGoneError

DRIVE_SCOPE: str = "https://www.googleapis.com/auth/drive.readonly"
T = TypeVar("T")


@dataclass(frozen=True)
class DriveFile:
    """Drive metadata fields needed to select and identify a file."""

    id: str
    name: str = ""
    mime_type: str = Field(default="", alias="mimeType")
    trashed: bool = False
    drive_id: str | None = Field(default=None, alias="driveId")
    parents: tuple[str, ...] = ()
    owners: tuple[DriveOwner, ...] = field(default=(), repr=False)
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    size: str | None = None


@dataclass(frozen=True)
class DriveOwner:
    """Transient Drive owner principal."""

    email_address: Annotated[str, Field(alias="emailAddress")] = field(repr=False)


@dataclass(frozen=True)
class DrivePermission:
    """Transient permission fields needed to compute exposure."""

    type: str
    role: str
    domain: str | None = field(default=None, repr=False)
    email_address: Annotated[str | None, Field(alias="emailAddress")] = field(
        default=None, repr=False
    )
    allow_file_discovery: bool = Field(default=False, alias="allowFileDiscovery")


@dataclass(frozen=True)
class DrivePermissionPage:
    """One page from Drive permissions.list."""

    permissions: tuple[DrivePermission, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


@dataclass(frozen=True)
class DriveChange:
    """One Drive changes feed entry."""

    file_id: str = Field(alias="fileId")
    removed: bool = False
    file: DriveFile | None = None


@dataclass(frozen=True)
class DrivePage:
    """Page returned by the changes or files list endpoints."""

    changes: tuple[DriveChange, ...] = ()
    files: tuple[DriveFile, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")
    new_start_page_token: str | None = Field(default=None, alias="newStartPageToken")
    start_page_token: str | None = Field(default=None, alias="startPageToken")


@dataclass(frozen=True)
class DriveListPage:
    """Typed files.list response."""

    files: tuple[DriveFile, ...] = ()
    drives: tuple[DriveFile, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


@dataclass(frozen=True)
class WatchResponse:
    """Drive watch channel response."""

    expiration: str
    resource_id: str = Field(alias="resourceId")


@dataclass(frozen=True)
class WatchRequest:
    """Drive watch channel request body."""

    id: str
    type: str
    address: str
    token: str
    expiration: int


@dataclass(frozen=True)
class GoogleErrorReason:
    """Google API error reason used only for rate-limit classification."""

    reason: str = ""


@dataclass(frozen=True)
class GoogleErrorDetails:
    """Google API error details."""

    errors: tuple[GoogleErrorReason, ...] = ()


@dataclass(frozen=True)
class GoogleErrorBody:
    """Google API error body, never included in a public exception."""

    error: GoogleErrorDetails | None = None


@dataclass
class DriveApi:
    """Drive v3 client with safe error conversion and typed response parsing."""

    http: httpx.AsyncClient
    auth: GoogleAuth
    base: str = "https://www.googleapis.com"

    async def get_start_page_token(self, subject: str | None = None) -> str:
        """Return the initial Drive changes cursor."""
        response: httpx.Response = await self._get("/drive/v3/changes/startPageToken", (), subject)
        page: DrivePage = _parse(response, DrivePage)
        if page.start_page_token is None:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive response is invalid")
        return page.start_page_token

    async def changes_list(
        self, page_token: str, drive_id: str | None = None, subject: str | None = None
    ) -> DrivePage:
        """Read one page of Drive changes."""
        params: list[tuple[str, str | int | bool]] = [
            ("pageToken", page_token),
            ("includeItemsFromAllDrives", "true"),
            ("supportsAllDrives", "true"),
            (
                "fields",
                "changes(fileId,removed,file(id,name,mimeType,trashed,driveId,parents,"
                "owners(emailAddress),modifiedTime,size)),"
                "nextPageToken,newStartPageToken",
            ),
        ]
        if drive_id is not None:
            params.append(("driveId", drive_id))
        response: httpx.Response = await self._get("/drive/v3/changes", tuple(params), subject)
        return _parse(response, DrivePage)

    async def watch_changes(
        self,
        channel_id: str,
        token: str,
        address: str,
        expiration: datetime,
        subject: str | None = None,
    ) -> WatchResponse:
        """Create a push notification channel for Drive changes."""
        request: WatchRequest = WatchRequest(
            id=channel_id,
            type="web_hook",
            address=address,
            token=token,
            expiration=int(expiration.timestamp() * 1000),
        )
        url: str = f"{self.base}/drive/v3/changes/watch"
        access_token: str = await self.auth.token((DRIVE_SCOPE,), subject)
        try:
            response: httpx.Response = await self.http.post(
                url,
                params=(("pageToken", await self.get_start_page_token(subject)),),
                headers={"Authorization": f"Bearer {access_token}"},
                json=asdict(request),
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive request failed") from None
        self._check(response)
        return _parse(response, WatchResponse)

    async def stop_channel(self, channel_id: str, resource_id: str) -> None:
        """Stop an existing Drive notification channel."""
        access_token: str = await self.auth.token((DRIVE_SCOPE,))
        try:
            response: httpx.Response = await self.http.post(
                f"{self.base}/drive/v3/channels/stop",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"id": channel_id, "resourceId": resource_id},
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive request failed") from None
        self._check(response)

    async def files_list(
        self,
        q: str,
        corpora: str,
        drive_id: str | None,
        page_token: str | None,
        page_size: int,
        subject: str | None,
    ) -> DriveListPage:
        """List files with an explicit corpus, cursor, and optional impersonated user."""
        params: list[tuple[str, str | int | bool]] = [
            ("q", q),
            ("corpora", corpora),
            ("pageSize", min(page_size, 1000)),
            ("supportsAllDrives", "true"),
            ("includeItemsFromAllDrives", "true"),
            (
                "fields",
                "files(id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size),"
                "nextPageToken",
            ),
        ]
        if drive_id is not None:
            params.append(("driveId", drive_id))
        if page_token is not None:
            params.append(("pageToken", page_token))
        response: httpx.Response = await self._get("/drive/v3/files", tuple(params), subject)
        return _parse(response, DriveListPage)

    async def files_get_meta(self, file_id: str, subject: str | None = None) -> DriveFile:
        """Read metadata for one file."""
        response: httpx.Response = await self._get(
            f"/drive/v3/files/{quote(file_id, safe='')}",
            (
                (
                    "fields",
                    "id,name,mimeType,trashed,driveId,parents,owners(emailAddress),modifiedTime,size",
                ),
            ),
            subject,
        )
        return _parse(response, DriveFile)

    async def list_permissions(
        self, file_id: str, subject: str | None = None
    ) -> tuple[DrivePermission, ...]:
        """Read transient permissions with read-only paginated GET requests."""
        permissions: list[DrivePermission] = []
        page_token: str | None = None
        while True:
            params: list[tuple[str, str | int | bool]] = [
                ("supportsAllDrives", "true"),
                (
                    "fields",
                    "permissions(type,role,domain,emailAddress,allowFileDiscovery),nextPageToken",
                ),
            ]
            if page_token is not None:
                params.append(("pageToken", page_token))
            response: httpx.Response = await self._get(
                f"/drive/v3/files/{quote(file_id, safe='')}/permissions", tuple(params), subject
            )
            page: DrivePermissionPage = _parse(response, DrivePermissionPage)
            permissions.extend(page.permissions)
            page_token = page.next_page_token
            if page_token is None:
                return tuple(permissions)

    async def files_get_media(self, file_id: str, subject: str | None = None) -> bytes:
        """Download file bytes without retaining or logging the response body."""
        response: httpx.Response = await self._get(
            f"/drive/v3/files/{quote(file_id, safe='')}", (("alt", "media"),), subject
        )
        return response.content

    async def files_export(
        self, file_id: str, mime: str = "text/plain", subject: str | None = None
    ) -> bytes:
        """Export a Google-native file as text."""
        response: httpx.Response = await self._get(
            f"/drive/v3/files/{quote(file_id, safe='')}/export",
            (("mimeType", mime),),
            subject,
        )
        return response.content

    async def drives_list(
        self, page_token: str | None = None, subject: str | None = None
    ) -> DriveListPage:
        """List shared drives for a domain or impersonated user."""
        params: list[tuple[str, str | int | bool]] = [
            ("pageSize", 100),
            ("fields", "drives(id,name),nextPageToken"),
        ]
        if page_token is not None:
            params.append(("pageToken", page_token))
        response: httpx.Response = await self._get(
            "/drive/v3/drives", tuple(params), subject, drive_list=True
        )
        return _parse(response, DriveListPage)

    async def _get(
        self,
        path: str,
        params: tuple[tuple[str, str | int | bool], ...],
        subject: str | None,
        drive_list: bool = False,
    ) -> httpx.Response:
        """Make an authenticated GET and translate provider failures safely."""
        access_token: str = await self.auth.token((DRIVE_SCOPE,), subject)
        try:
            response: httpx.Response = await self.http.get(
                f"{self.base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive request failed") from None
        self._check(response)
        return response

    def _check(self, response: httpx.Response) -> None:
        """Map provider status codes without exposing response data."""
        if response.status_code == 404:
            raise ResourceGoneError("Google Drive resource is gone")
        if response.status_code == 429 or _is_rate_limited(response):
            retry_header: str | None = response.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_header is not None:
                try:
                    retry_after = float(retry_header)
                except ValueError:
                    retry_after = None
            raise RateLimited(retry_after)
        if response.status_code == 403:
            raise TelescreenError(ErrorCode.FORBIDDEN, "Google Drive access is forbidden")
        if response.is_error:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive request failed")


def _parse[T](response: httpx.Response, model: type[T]) -> T:
    """Parse a provider JSON response without returning its contents on failure."""
    try:
        return TypeAdapter(model).validate_json(response.content)
    except ValidationError:
        raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Drive response is invalid") from None


def _is_rate_limited(response: httpx.Response) -> bool:
    """Check documented Google 403 quota reasons without exposing the body."""
    if response.status_code != 403:
        return False
    try:
        error: GoogleErrorBody = TypeAdapter(GoogleErrorBody).validate_json(response.content)
    except ValidationError:
        return False
    details: GoogleErrorDetails | None = error.error
    reasons: tuple[str, ...] = (
        tuple(item.reason for item in details.errors) if details is not None else ()
    )
    return any(reason in ("rateLimitExceeded", "userRateLimitExceeded") for reason in reasons)
