"""Mask audit payloads before durable publication and again at chain insertion."""

from telescreen.core.json import JsonKind, JsonValue, json_array, json_object, json_str
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_types import AuditEntry


def _redact_json(value: JsonValue, redactor: Redactor) -> JsonValue:
    """Redact string leaves recursively without changing JSON structure."""
    if value.kind is JsonKind.STRING:
        return json_str(redactor.redact(value.str_value).text)
    if value.kind is JsonKind.ARRAY:
        return json_array(tuple(_redact_json(item, redactor) for item in value.items))
    if value.kind is JsonKind.OBJECT:
        members: list[tuple[str, JsonValue]] = [
            (member.name, _redact_json(member.value, redactor)) for member in value.members
        ]
        return json_object(members)
    return value


def redact_audit_entry(entry: AuditEntry, redactor: Redactor) -> AuditEntry:
    """Return a detached payload with sensitive summary and detail text masked."""
    return AuditEntry(
        entry.actor_kind,
        entry.actor_ref,
        entry.action,
        entry.target_kind,
        entry.target_id,
        redactor.redact(entry.summary).text,
        _redact_json(entry.details, redactor),
    )
