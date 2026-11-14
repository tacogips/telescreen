"""Validation and atomic persistence for asset metadata values."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import SplitResult, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import DataEventKind, DataFieldRole, DataFieldType, ErrorCode
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.inventory.repository import DataEventDraft, InventoryRepository
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.identity_context import AssigneeLookup, actor_kind_for

_DECIMAL: re.Pattern[str] = re.compile(r"^-?[0-9]{1,18}(\.[0-9]{1,6})?$")
_DATE: re.Pattern[str] = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_MAX_INTEGER: int = 2**53 - 1


@dataclass(frozen=True)
class ValueInput:
    """One requested field value; null clears the stored value."""

    key: str
    value_json: str | None


@dataclass(frozen=True)
class ValueIssue:
    """A safe validation issue containing a field key and static code only."""

    field: str
    code: str


@dataclass(frozen=True)
class ValueValidator:
    """Validate field values without echoing or retaining their contents."""

    recipients: AssigneeLookup
    redactor: Redactor

    async def validate(
        self,
        fields: tuple[DataFieldRow, ...],
        values: tuple[ValueInput, ...],
    ) -> tuple[ValueIssue, ...]:
        """Return all value errors without returning any supplied value."""
        issues: list[ValueIssue] = []
        seen: set[str] = set()
        for item in values:
            if item.key in seen:
                issues.append(ValueIssue(item.key, "invalid_type"))
                continue
            seen.add(item.key)
            field: DataFieldRow | None = next((row for row in fields if row.key == item.key), None)
            if field is None:
                issues.append(ValueIssue(item.key, "unknown_field"))
            elif field.archived_at is not None:
                issues.append(ValueIssue(item.key, "archived_field"))
            elif item.value_json is not None:
                issue: ValueIssue | None = await self._validate_one(field, item.value_json)
                if issue is not None:
                    issues.append(issue)
        return tuple(issues)

    async def _validate_one(self, field: DataFieldRow, raw: str) -> ValueIssue | None:
        """Validate a single encoded JSON value."""
        try:
            value: JsonValue = parse_json(raw)
        except TelescreenError:
            return ValueIssue(field.key, "invalid_type")
        if _contains_hit(value, self.redactor):
            return ValueIssue(field.key, "raw_value_detected")
        field_type: DataFieldType = DataFieldType(field.field_type)
        if not _valid_shape(field_type, value):
            return ValueIssue(field.key, "invalid_type")
        if field_type is DataFieldType.TEXT and not 1 <= len(value.str_value) <= 500:
            return ValueIssue(
                field.key, "too_long" if len(value.str_value) > 500 else "invalid_type"
            )
        if field_type is DataFieldType.TEXT and (
            "\n" in value.str_value or "\r" in value.str_value
        ):
            return ValueIssue(
                field.key, "too_long" if len(value.str_value) > 500 else "invalid_type"
            )
        if field_type is DataFieldType.LONG_TEXT and not 1 <= len(value.str_value) <= 4000:
            return ValueIssue(
                field.key, "too_long" if len(value.str_value) > 4000 else "invalid_type"
            )
        if field_type is DataFieldType.INTEGER and abs(value.int_value) > _MAX_INTEGER:
            return ValueIssue(field.key, "invalid_type")
        if field_type is DataFieldType.DECIMAL and _DECIMAL.fullmatch(value.str_value) is None:
            return ValueIssue(field.key, "invalid_type")
        if field_type is DataFieldType.DATE and not _valid_date(value.str_value):
            return ValueIssue(field.key, "invalid_type")
        if field_type in (DataFieldType.SINGLE_SELECT, DataFieldType.MULTI_SELECT):
            options: frozenset[str] = _option_values(field.options_json)
            selected: tuple[str, ...] = (
                (value.str_value,)
                if field_type is DataFieldType.SINGLE_SELECT
                else tuple(item.str_value for item in value.items)
            )
            if any(option not in options for option in selected):
                return ValueIssue(field.key, "invalid_option")
            if field_type is DataFieldType.MULTI_SELECT and (
                not 1 <= len(selected) <= 20 or len(selected) != len(set(selected))
            ):
                return ValueIssue(field.key, "invalid_type")
        if field_type is DataFieldType.REFERENCE:
            reference: str = value.str_value
            kind: str
            separator: str
            subject: str
            kind, separator, subject = reference.partition(":")
            if not separator or not subject or kind not in ("member", "team"):
                return ValueIssue(field.key, "unknown_reference")
            try:
                await self.recipients.assignee_of(reference)
            except TelescreenError as error:
                if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                    return ValueIssue(field.key, "unknown_reference")
                raise
        if field_type is DataFieldType.URL:
            parts: SplitResult = urlsplit(value.str_value)
            if (
                len(value.str_value) > 2048
                or parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
            ):
                return ValueIssue(field.key, "invalid_type")
        return None


@dataclass(frozen=True)
class ValueService:
    """Apply validated metadata atomically and record events and audit."""

    repository: InventoryRepository
    validator: ValueValidator
    audit: AuditLog
    clock: Clock

    async def put(
        self,
        session: AsyncSession,
        asset: DataAssetRow,
        fields: tuple[DataFieldRow, ...],
        values: tuple[ValueInput, ...],
        actor_ref: str,
    ) -> None:
        """Validate, apply, enforce required fields, and record changed values."""
        issues: tuple[ValueIssue, ...] = await self.validator.validate(fields, values)
        if issues:
            code: ErrorCode = (
                ErrorCode.RAW_VALUE_DETECTED
                if issues[0].code == "raw_value_detected"
                else ErrorCode.INVALID_FIELD_VALUE
            )
            raise TelescreenError(code, "invalid field value")
        active: tuple[DataFieldRow, ...] = tuple(row for row in fields if row.archived_at is None)
        current: tuple[DataFieldValueRow, ...] = await self.repository.value_rows(session, asset.id)
        resulting_ids: set[str] = {row.field_id for row in current}
        changed: list[tuple[DataFieldRow, str | None, str | None]] = []
        now: datetime = self.clock.now()
        for item in values:
            field: DataFieldRow = next(row for row in active if row.key == item.key)
            stored: DataFieldValueRow | None = next(
                (row for row in current if row.field_id == field.id), None
            )
            old_json: str | None = stored.value_json if stored is not None else None
            if item.value_json is None:
                if stored is not None:
                    resulting_ids.discard(field.id)
                    await session.delete(stored)
                    changed.append((field, old_json, None))
                continue
            canonical: str
            value_date: str | None
            canonical, value_date = canonical_value(field, item.value_json)
            if old_json == canonical:
                continue
            row: DataFieldValueRow = stored or DataFieldValueRow(
                asset_id=asset.id,
                field_id=field.id,
                value_json=canonical,
                value_date=value_date,
                updated_by=actor_ref,
                updated_at=now,
            )
            row.value_json = canonical
            row.value_date = value_date
            row.updated_by = actor_ref
            row.updated_at = now
            row.revision_id = new_id()
            if stored is None:
                session.add(row)
            resulting_ids.add(field.id)
            changed.append((field, old_json, canonical))
        missing: tuple[str, ...] = tuple(
            row.key for row in active if row.required and row.id not in resulting_ids
        )
        if missing:
            raise TelescreenError(ErrorCode.REQUIRED_FIELD_MISSING, "required field missing")
        for field, old_json, new_json in changed:
            if field.role == DataFieldRole.DELETION_DUE.value:
                asset.overdue_notified_for = None
            await self.repository.append_event(
                session,
                DataEventDraft(
                    asset_id=asset.id,
                    kind=DataEventKind.METADATA_CHANGED,
                    recorded_at=now,
                    actor_ref=actor_ref,
                    field_key=field.key,
                    old_value=display_value(DataFieldType(field.field_type), old_json)
                    if old_json is not None
                    else None,
                    new_value=display_value(DataFieldType(field.field_type), new_json)
                    if new_json is not None
                    else None,
                ),
            )
        if changed:
            keys: tuple[str, ...] = tuple(field.key for field, _, _ in changed)
            await self.audit.append(
                session,
                AuditEntry(
                    actor_kind_for(actor_ref),
                    actor_ref,
                    "data_asset.fields_changed",
                    "data_asset",
                    asset.id,
                    "Data asset fields changed",
                    json_object(
                        (
                            ("field_keys", json_str(",".join(keys))),
                            ("changed_count", json_int(len(keys))),
                        )
                    ),
                ),
            )


def canonical_value(field: DataFieldRow, raw: str) -> tuple[str, str | None]:
    """Return canonical JSON and its optional date index value."""
    value: JsonValue = parse_json(raw)
    value_date: str | None = (
        value.str_value if field.field_type == DataFieldType.DATE.value else None
    )
    return dump_json(value), value_date


def display_value(field_type: DataFieldType, raw: str | None) -> str:
    """Format a safe value for metadata-change events, clipped to 500 characters."""
    if raw is None:
        return ""
    value: JsonValue = parse_json(raw)
    if field_type is DataFieldType.MULTI_SELECT:
        text: str = ", ".join(item.str_value for item in value.items)
    elif value.kind is JsonKind.STRING:
        text = value.str_value
    else:
        text = dump_json(value)
    return text[:500]


def _valid_shape(field_type: DataFieldType, value: JsonValue) -> bool:
    """Check the JSON primitive shape for a field type."""
    if field_type in (
        DataFieldType.TEXT,
        DataFieldType.LONG_TEXT,
        DataFieldType.DECIMAL,
        DataFieldType.DATE,
        DataFieldType.SINGLE_SELECT,
        DataFieldType.REFERENCE,
        DataFieldType.URL,
    ):
        return value.kind is JsonKind.STRING
    if field_type is DataFieldType.INTEGER:
        return value.kind is JsonKind.INT
    if field_type is DataFieldType.BOOLEAN:
        return value.kind is JsonKind.BOOL
    return value.kind is JsonKind.ARRAY and all(
        item.kind is JsonKind.STRING for item in value.items
    )


def _contains_hit(value: JsonValue, redactor: Redactor) -> bool:
    """Scan every string leaf without retaining detector matches."""
    if value.kind is JsonKind.STRING:
        return redactor.has_hits(value.str_value)
    return any(_contains_hit(item, redactor) for item in value.items) or any(
        _contains_hit(member.value, redactor) for member in value.members
    )


def _valid_date(value: str) -> bool:
    """Require an exact and valid ISO calendar date."""
    if _DATE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _option_values(raw: str | None) -> frozenset[str]:
    """Read option values from canonical option JSON."""
    if raw is None:
        return frozenset()
    options: JsonValue = parse_json(raw)
    return frozenset(
        value.as_str() or "" for item in options.items if (value := item.get("value")) is not None
    )
