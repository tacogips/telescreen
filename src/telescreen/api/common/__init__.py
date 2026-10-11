"""Shared API dependencies and response handling."""

from telescreen.api.common.auth import (
    ExternalAuthDeclaration,
    PermissionDependency,
    Principal,
    authenticate,
    declare_external,
    hash_token,
    require_permission,
)
from telescreen.api.common.body import read_limited_body
from telescreen.api.common.context import ApiContext
from telescreen.api.common.errors import http_status_for, install_error_handlers
from telescreen.api.common.pagination import PageParams, decode_cursor, encode_cursor, page_params

__all__: list[str] = [
    "ApiContext",
    "ExternalAuthDeclaration",
    "PageParams",
    "PermissionDependency",
    "Principal",
    "authenticate",
    "declare_external",
    "decode_cursor",
    "encode_cursor",
    "hash_token",
    "http_status_for",
    "install_error_handlers",
    "page_params",
    "read_limited_body",
    "require_permission",
]
