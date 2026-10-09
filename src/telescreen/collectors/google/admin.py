"""Google Workspace Admin Directory API operations for domain-wide sweeps."""

from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from pydantic import Field, TypeAdapter, ValidationError

from telescreen.collectors.google.auth import GoogleAuth
from telescreen.collectors.google.drive import _is_rate_limited, _parse
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.collectors import RateLimited, ResourceGoneError

ADMIN_SCOPE: str = "https://www.googleapis.com/auth/admin.directory.user.readonly"
DRIVE_SCOPE: str = "https://www.googleapis.com/auth/drive.readonly"


@dataclass(frozen=True)
class DirectoryUser:
    """Admin user record with email hidden from repr."""

    id: str
    primary_email: str = Field(alias="primaryEmail", repr=False)


@dataclass(frozen=True)
class UsersPage:
    """Page of Workspace users."""

    users: tuple[DirectoryUser, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


@dataclass
class AdminApi:
    """List Workspace users and resolve transient impersonation email addresses."""

    http: httpx.AsyncClient
    auth: GoogleAuth
    admin_subject: str
    base: str = "https://admin.googleapis.com"
    email_cache: list[tuple[str, str]] = field(default_factory=list, repr=False)

    async def users_list(self, page_token: str | None = None) -> UsersPage:
        """List domain users while impersonating the configured administrator."""
        params: list[tuple[str, str | int]] = [("customer", "my_customer"), ("maxResults", 500)]
        if page_token is not None:
            params.append(("pageToken", page_token))
        access_token: str = await self.auth.token((ADMIN_SCOPE,), self.admin_subject)
        try:
            response: httpx.Response = await self.http.get(
                f"{self.base}/admin/directory/v1/users",
                params=tuple(params),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Admin request failed") from None
        self._check(response)
        return _parse(response, UsersPage)

    async def user_email(self, user_id: str) -> str:
        """Resolve a user id to an email for transient Drive impersonation."""
        cached: tuple[str, str] | None = next(
            (item for item in self.email_cache if item[0] == user_id), None
        )
        if cached is not None:
            return cached[1]
        access_token: str = await self.auth.token((ADMIN_SCOPE,), self.admin_subject)
        try:
            response: httpx.Response = await self.http.get(
                f"{self.base}/admin/directory/v1/users/{quote(user_id, safe='')}",
                params=(("fields", "primaryEmail"),),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Admin request failed") from None
        self._check(response)
        try:
            user: DirectoryUser = TypeAdapter(DirectoryUser).validate_json(response.content)
        except ValidationError:
            raise TelescreenError(
                ErrorCode.FETCH_FAILED, "Google Admin response is invalid"
            ) from None
        self.email_cache.append((user_id, user.primary_email))
        return user.primary_email

    def _check(self, response: httpx.Response) -> None:
        """Map Admin API failures without including response details."""
        if response.status_code == 404:
            raise ResourceGoneError("Google Admin resource is gone")
        if response.status_code == 429 or _is_rate_limited(response):
            retry_header: str | None = response.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_header is not None:
                try:
                    retry_after = float(retry_header)
                except ValueError:
                    retry_after = None
            raise RateLimited(retry_after)
        if response.is_error:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Admin request failed")
