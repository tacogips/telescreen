"""Opaque cursor encoding and bounded page query parameters."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Query

from telescreen.core.cursors import decode_cursor as decode_cursor
from telescreen.core.cursors import encode_cursor as encode_cursor


@dataclass(frozen=True)
class PageParams:
    """Validated pagination inputs."""

    limit: int
    cursor: str | None


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> PageParams:
    """Build validated pagination parameters for FastAPI dependencies."""
    return PageParams(limit=limit, cursor=cursor)
