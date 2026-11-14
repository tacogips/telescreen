"""Closed JSON values and canonical JSON encoding for the standalone SDK."""

from __future__ import annotations

import json as json_library
import math
from dataclasses import dataclass
from enum import StrEnum


class JsonKind(StrEnum):
    """JSON value kinds."""

    NULL = "null"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"


@dataclass(frozen=True)
class JsonMember:
    """A named member of a JSON object."""

    name: str
    value: Json


@dataclass(frozen=True)
class Json:
    """A recursive but closed JSON value."""

    kind: JsonKind
    bool_value: bool = False
    int_value: int = 0
    float_value: float = 0.0
    str_value: str = ""
    items: tuple[Json, ...] = ()
    members: tuple[JsonMember, ...] = ()

    def get(self, name: str) -> Json | None:
        """Return an object member, if present."""
        return next((member.value for member in self.members if member.name == name), None)

    def as_str(self) -> str | None:
        """Return the string value when this value is a string."""
        return self.str_value if self.kind is JsonKind.STRING else None

    def as_int(self) -> int | None:
        """Return the integer value when this value is an integer."""
        return self.int_value if self.kind is JsonKind.INT else None

    def as_float(self) -> float | None:
        """Return a numeric value as a float."""
        if self.kind is JsonKind.FLOAT:
            return self.float_value
        if self.kind is JsonKind.INT:
            return float(self.int_value)
        return None

    def as_bool(self) -> bool | None:
        """Return the boolean value when this value is a boolean."""
        return self.bool_value if self.kind is JsonKind.BOOL else None

    def as_list(self) -> tuple[Json, ...] | None:
        """Return array contents when this value is an array."""
        return self.items if self.kind is JsonKind.ARRAY else None


def null() -> Json:
    """Construct JSON null."""
    return Json(JsonKind.NULL)


def boolean(value: bool) -> Json:
    """Construct a JSON boolean."""
    return Json(JsonKind.BOOL, bool_value=value)


def integer(value: int) -> Json:
    """Construct a JSON integer."""
    return Json(JsonKind.INT, int_value=value)


def number(value: float) -> Json:
    """Construct a finite JSON number."""
    if not math.isfinite(value):
        raise ValueError("invalid JSON number")
    return Json(JsonKind.FLOAT, float_value=value)


def string(value: str) -> Json:
    """Construct a JSON string."""
    return Json(JsonKind.STRING, str_value=value)


def array(values: tuple[Json, ...]) -> Json:
    """Construct a JSON array."""
    return Json(JsonKind.ARRAY, items=values)


def obj(values: tuple[tuple[str, Json], ...]) -> Json:
    """Construct a JSON object and reject duplicate names."""
    names: tuple[str, ...] = tuple(name for name, _ in values)
    if len(names) != len(set(names)):
        raise ValueError("duplicate JSON member")
    return Json(JsonKind.OBJECT, members=tuple(JsonMember(name, value) for name, value in values))


def _from_native(value: object) -> Json:
    """Convert standard-library decoded JSON values into the closed model."""
    if value is None:
        return null()
    if isinstance(value, bool):
        return boolean(value)
    if isinstance(value, int):
        return integer(value)
    if isinstance(value, float):
        return number(value)
    if isinstance(value, str):
        return string(value)
    if isinstance(value, list):
        return array(tuple(_from_native(item) for item in value))
    if isinstance(value, dict):
        pairs: list[tuple[str, Json]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid JSON member")
            pairs.append((key, _from_native(item)))
        return obj(tuple(pairs))
    raise ValueError("invalid JSON value")


def parse(text: str | bytes) -> Json:
    """Parse JSON and hide decoder details from callers."""
    try:
        decoded: object = json_library.loads(text, parse_constant=_reject_constant)
        return _from_native(decoded)
    except (
        json_library.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        raise ValueError("invalid JSON document") from None


def _reject_constant(value: str) -> None:
    """Reject non-standard NaN and Infinity tokens."""
    raise ValueError("non-finite JSON number")


def _to_native(value: Json) -> object:
    """Convert a closed JSON value to standard-library JSON values."""
    if value.kind is JsonKind.NULL:
        return None
    if value.kind is JsonKind.BOOL:
        return value.bool_value
    if value.kind is JsonKind.INT:
        return value.int_value
    if value.kind is JsonKind.FLOAT:
        return value.float_value
    if value.kind is JsonKind.STRING:
        return value.str_value
    if value.kind is JsonKind.ARRAY:
        return [_to_native(item) for item in value.items]
    return {member.name: _to_native(member.value) for member in value.members}


def dump(value: Json) -> str:
    """Serialize canonical compact JSON with sorted object keys."""
    return json_library.dumps(
        _to_native(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
