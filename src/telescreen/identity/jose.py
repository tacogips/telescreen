"""Compact JWS parsing and signature verification through typed PyJWT entry points."""

import base64
import binascii
import re
from dataclasses import dataclass

import jwt
from jwt.api_jws import PyJWS

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.core.vocabulary import ErrorCode

MAX_TOKEN_LENGTH: int = 8192
_SEGMENT_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]+")
_FORBIDDEN_HEADERS: tuple[str, ...] = ("crit", "jku", "x5u", "jwk", "x5c")


@dataclass(frozen=True)
class JwsHeader:
    """Protected header members used to select a verification key."""

    alg: str
    kid: str
    typ: str | None


def invalid_token() -> TelescreenError:
    """Return the static invalid-token error used for every rejection."""
    return TelescreenError(ErrorCode.INVALID_TOKEN, "token is invalid")


def decode_segment(segment: str) -> bytes:
    """Decode one unpadded base64url segment, rejecting non-alphabet input."""
    if not _SEGMENT_PATTERN.fullmatch(segment) or len(segment) % 4 == 1:
        raise invalid_token()
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        raise invalid_token() from None


def split_token(token: str) -> tuple[str, str, str]:
    """Split a compact JWS into header, payload and signature segments."""
    if len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        raise invalid_token()
    parts: list[str] = token.split(".")
    if len(parts) != 3:
        raise invalid_token()
    for part in parts:
        decode_segment(part)
    return parts[0], parts[1], parts[2]


def parse_header(segment: str) -> JwsHeader:
    """Parse the protected header and reject embedded or remote key references."""
    try:
        header: JsonValue = parse_json(decode_segment(segment))
    except TelescreenError:
        raise invalid_token() from None
    if header.kind is not JsonKind.OBJECT:
        raise invalid_token()
    if any(header.get(name) is not None for name in _FORBIDDEN_HEADERS):
        raise invalid_token()
    alg_value: JsonValue | None = header.get("alg")
    kid_value: JsonValue | None = header.get("kid")
    typ_value: JsonValue | None = header.get("typ")
    alg: str | None = alg_value.as_str() if alg_value is not None else None
    kid: str | None = kid_value.as_str() if kid_value is not None else None
    typ: str | None = typ_value.as_str() if typ_value is not None else None
    if not alg or not kid or (typ_value is not None and typ is None):
        raise invalid_token()
    return JwsHeader(alg=alg, kid=kid, typ=typ)


def verify_signature(token: str, jwk_json: str, alg: str) -> bytes:
    """Verify the signature with one JWK and algorithm, returning the payload bytes."""
    try:
        key: jwt.PyJWK = jwt.PyJWK.from_json(jwk_json, alg)
        payload: bytes | str = PyJWS().decode(token, key=key, algorithms=[alg])
    except (jwt.PyJWTError, ValueError, TypeError):
        raise invalid_token() from None
    if not isinstance(payload, bytes):
        raise invalid_token()
    return payload
