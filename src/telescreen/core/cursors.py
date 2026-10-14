"""Opaque value cursors without HTTP framework or persistence dependencies."""

import base64
import binascii
from collections.abc import Sequence

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, dump_json, json_array, json_str, parse_json
from telescreen.core.vocabulary import ErrorCode


def encode_cursor(parts: Sequence[str]) -> str:
    """Encode cursor fields as canonical JSON in unpadded base64url."""
    payload: str = dump_json(json_array(tuple(json_str(part) for part in parts)))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(text: str) -> tuple[str, ...]:
    """Decode a cursor, mapping malformed input to a static validation error."""
    try:
        padded: str = text + "=" * (-len(text) % 4)
        decoded: bytes = base64.b64decode(padded, altchars=b"-_", validate=True)
        value: JsonValue = parse_json(decoded)
    except (ValueError, binascii.Error):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
    if value.kind is not JsonKind.ARRAY or any(
        item.kind is not JsonKind.STRING for item in value.items
    ):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
    return tuple(item.str_value for item in value.items)
