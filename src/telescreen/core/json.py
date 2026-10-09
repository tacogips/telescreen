"""Closed JSON value model and JSON/YAML boundaries."""

from __future__ import annotations

import json as json_library
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

import yaml

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode


class JsonKind(StrEnum):
    """JSON value kind."""

    NULL = "null"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"


@dataclass(frozen=True)
class JsonMember:
    """One named member of a JSON object."""

    name: str
    value: JsonValue


@dataclass(frozen=True)
class JsonValue:
    """A recursively closed JSON value without native untyped mappings."""

    kind: JsonKind
    bool_value: bool = False
    int_value: int = 0
    float_value: float = 0.0
    str_value: str = ""
    items: tuple[JsonValue, ...] = ()
    members: tuple[JsonMember, ...] = ()

    def get(self, name: str) -> JsonValue | None:
        """Return an object member by name."""
        return next((member.value for member in self.members if member.name == name), None)

    def as_str(self) -> str | None:
        """Return the value when it is a string."""
        return self.str_value if self.kind is JsonKind.STRING else None

    def as_int(self) -> int | None:
        """Return the value when it is an integer, excluding booleans."""
        return self.int_value if self.kind is JsonKind.INT else None

    def as_float(self) -> float | None:
        """Return numeric values as a float."""
        if self.kind is JsonKind.FLOAT:
            return self.float_value
        if self.kind is JsonKind.INT:
            return float(self.int_value)
        return None

    def as_bool(self) -> bool | None:
        """Return the value when it is a boolean."""
        return self.bool_value if self.kind is JsonKind.BOOL else None

    def as_list(self) -> tuple[JsonValue, ...] | None:
        """Return array values as a tuple."""
        return self.items if self.kind is JsonKind.ARRAY else None

    def is_null(self) -> bool:
        """Return whether the value is JSON null."""
        return self.kind is JsonKind.NULL

    def member_names(self) -> tuple[str, ...]:
        """Return object member names in input order."""
        return tuple(member.name for member in self.members)


def json_null() -> JsonValue:
    """Construct a JSON null value."""
    return JsonValue(JsonKind.NULL)


def json_bool(value: bool) -> JsonValue:
    """Construct a JSON boolean."""
    return JsonValue(JsonKind.BOOL, bool_value=value)


def json_int(value: int) -> JsonValue:
    """Construct a JSON integer."""
    return JsonValue(JsonKind.INT, int_value=value)


def json_float(value: float) -> JsonValue:
    """Construct a finite JSON number."""
    if not math.isfinite(value):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON number")
    return JsonValue(JsonKind.FLOAT, float_value=value)


def json_str(value: str) -> JsonValue:
    """Construct a JSON string."""
    return JsonValue(JsonKind.STRING, str_value=value)


def json_array(items: Sequence[JsonValue]) -> JsonValue:
    """Construct a JSON array."""
    return JsonValue(JsonKind.ARRAY, items=tuple(items))


def json_object(members: Sequence[tuple[str, JsonValue]]) -> JsonValue:
    """Construct a JSON object and reject duplicate names."""
    names: tuple[str, ...] = tuple(name for name, _ in members)
    if len(names) != len(set(names)):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "duplicate JSON object member")
    return JsonValue(
        JsonKind.OBJECT,
        members=tuple(JsonMember(name, value) for name, value in members),
    )


def _from_native(value: object) -> JsonValue:
    """Convert decoded standard-library JSON/YAML values to the closed model."""
    if value is None:
        return json_null()
    if isinstance(value, bool):
        return json_bool(value)
    if isinstance(value, int):
        return json_int(value)
    if isinstance(value, float):
        return json_float(value)
    if isinstance(value, str):
        return json_str(value)
    if isinstance(value, (datetime, date)):
        return json_str(value.isoformat())
    if isinstance(value, list):
        return json_array(tuple(_from_native(item) for item in value))
    if isinstance(value, dict):
        pairs: list[tuple[str, JsonValue]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON document")
            pairs.append((key, _from_native(item)))
        return json_object(pairs)
    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON document")


def parse_json(text: str | bytes) -> JsonValue:
    """Parse JSON without echoing invalid input in errors."""
    try:
        native: object = json_library.loads(text, parse_constant=_reject_constant)
        return _from_native(native)
    except (
        json_library.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON document") from None


def _reject_constant(value: str) -> None:
    """Reject non-standard NaN and Infinity tokens."""
    raise ValueError("non-finite JSON number")


def parse_yaml(text: str) -> JsonValue:
    """Parse safe YAML into JSON values, stringifying YAML dates."""
    try:
        native: object = yaml.safe_load(text)
        return _from_native(native)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON document") from None


def _to_native(value: JsonValue) -> object:
    """Convert a closed JSON value into native values for serialization."""
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


def dump_json(value: JsonValue) -> str:
    """Write canonical JSON with sorted keys and compact separators."""
    return json_library.dumps(
        _to_native(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
