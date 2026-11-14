"""Load and validate the bundled Police v1 JSON contract."""

import json
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

_SCHEMA_NAMES: tuple[str, ...] = (
    "manifest.schema.json",
    "registration.schema.json",
    "claim-request.schema.json",
    "claim-response.schema.json",
    "heartbeat-request.schema.json",
    "heartbeat-response.schema.json",
    "task-envelope.schema.json",
    "result-envelope.schema.json",
    "masked-finding.schema.json",
    "resource-exposure.schema.json",
    "partition-result.schema.json",
    "error.schema.json",
)


@dataclass(frozen=True)
class SchemaError:
    """A safe schema validation location and keyword."""

    path: str
    keyword: str


def _contract_root() -> Traversable:
    """Return the Police v1 package resource directory."""
    return files("telescreen.contracts").joinpath("police", "v1")


def load_schema(name: str) -> str:
    """Load a known Police v1 schema by its file name."""
    if name not in _SCHEMA_NAMES and name != "openapi.json":
        raise TelescreenError(ErrorCode.NOT_FOUND, "contract document not found")
    return _contract_root().joinpath(name).read_text(encoding="utf-8")


def openapi_document() -> str:
    """Return the bundled Police v1 OpenAPI document."""
    return load_schema("openapi.json")


def _error_path(error_path: tuple[str | int, ...]) -> str:
    """Render a validation path as a JSON pointer."""
    escaped: tuple[str, ...] = tuple(
        str(part).replace("~", "~0").replace("/", "~1") for part in error_path
    )
    return "" if not escaped else "/" + "/".join(escaped)


def validate(name: str, raw: bytes) -> list[SchemaError]:
    """Validate JSON bytes without returning instance values or messages."""
    try:
        return [
            SchemaError(_error_path(tuple(error.absolute_path)), str(error.validator))
            for error in Draft202012Validator(
                json.loads(load_schema(name)),
                registry=Registry().with_resources(
                    [
                        (
                            f"https://schemas.telescreen.invalid/police/v1/{schema_name}",
                            Resource.from_contents(json.loads(load_schema(schema_name))),
                        )
                        for schema_name in _SCHEMA_NAMES
                    ]
                ),
                format_checker=FormatChecker(),
            ).iter_errors(json.loads(raw))
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return [SchemaError("", "json")]
