"""Typed Google Sheets v4 reads for spreadsheet metadata and cell values."""

from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import quote

import httpx
from pydantic import Field, TypeAdapter, ValidationError

from telescreen.collectors.google.auth import GoogleAuth
from telescreen.collectors.google.drive import _is_rate_limited
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.collectors import RateLimited, ResourceGoneError

SHEETS_SCOPE: str = "https://www.googleapis.com/auth/spreadsheets.readonly"
T = TypeVar("T")
type CellValue = str | int | float | bool


@dataclass(frozen=True)
class SheetProperties:
    """Spreadsheet tab metadata."""

    title: str


@dataclass(frozen=True)
class SheetItem:
    """A spreadsheet tab and its properties."""

    properties: SheetProperties


@dataclass(frozen=True)
class SheetMetadata:
    """Spreadsheet metadata response."""

    sheets: tuple[SheetItem, ...] = ()


@dataclass(frozen=True)
class SheetValues:
    """Values returned for a sheet range."""

    range: str
    values: tuple[tuple[CellValue, ...], ...] = ()


@dataclass(frozen=True)
class ValuesPage:
    """Google Sheets batch values response."""

    value_ranges: tuple[SheetValues, ...] = Field(default=(), alias="valueRanges")


@dataclass
class SheetsApi:
    """Read spreadsheet titles and batch cell ranges through the Sheets API."""

    http: httpx.AsyncClient
    auth: GoogleAuth
    base: str = "https://sheets.googleapis.com"

    async def sheet_titles(
        self, spreadsheet_id: str, subject: str | None = None
    ) -> tuple[str, ...]:
        """Return tab titles without exposing spreadsheet contents in errors."""
        response: httpx.Response = await self._get(
            f"/v4/spreadsheets/{quote(spreadsheet_id, safe='')}",
            (("fields", "sheets(properties(title))"),),
            subject,
        )
        metadata: SheetMetadata = _parse(response, SheetMetadata)
        return tuple(sheet.properties.title for sheet in metadata.sheets)

    async def values_batch_get(
        self, spreadsheet_id: str, ranges: tuple[str, ...], subject: str | None = None
    ) -> tuple[SheetValues, ...]:
        """Fetch all requested A1 ranges in one API request."""
        params: tuple[tuple[str, str], ...] = tuple(
            [("ranges", value) for value in ranges] + [("valueRenderOption", "FORMATTED_VALUE")]
        )
        response: httpx.Response = await self._get(
            f"/v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values:batchGet",
            params,
            subject,
        )
        page: ValuesPage = _parse(response, ValuesPage)
        return page.value_ranges

    async def _get(
        self, path: str, params: tuple[tuple[str, str], ...], subject: str | None
    ) -> httpx.Response:
        """Make an authenticated Sheets request and map errors to safe codes."""
        access_token: str = await self.auth.token((SHEETS_SCOPE,), subject)
        try:
            response: httpx.Response = await self.http.get(
                f"{self.base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.RequestError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Sheets request failed") from None
        if response.status_code == 404:
            raise ResourceGoneError("Google Sheets resource is gone")
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
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Sheets request failed")
        return response


def _parse[T](response: httpx.Response, model: type[T]) -> T:
    """Parse JSON into a typed response without echoing provider values."""
    try:
        return TypeAdapter(model).validate_json(response.content)
    except ValidationError:
        raise TelescreenError(ErrorCode.FETCH_FAILED, "Google Sheets response is invalid") from None
