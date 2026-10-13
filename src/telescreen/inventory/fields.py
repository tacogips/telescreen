"""Administrative metadata field schema and safe audit operations."""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import ActorKind, DataFieldRole, DataFieldType, ErrorCode
from telescreen.db.models import DataFieldRow, DataFieldValueRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog

_KEY: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_OPTION: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class FieldOption:
    """One safe select option."""

    value: str
    label: str


@dataclass(frozen=True)
class FieldService:
    """Validate and mutate the administrator-defined field schema."""

    audit: AuditLog
    redactor: Redactor
    clock: Clock

    async def list(self, session: AsyncSession) -> tuple[DataFieldRow, ...]:
        """Return fields with active fields first in configured order."""
        rows: tuple[DataFieldRow, ...] = tuple((await session.scalars(select(DataFieldRow))).all())
        return tuple(
            sorted(rows, key=lambda row: (row.archived_at is not None, row.position, row.key))
        )

    async def create(
        self,
        session: AsyncSession,
        key: str,
        label: str,
        description: str | None,
        field_type: DataFieldType,
        options: tuple[FieldOption, ...],
        required: bool,
        role: DataFieldRole | None,
        actor_ref: str,
    ) -> DataFieldRow:
        """Create one validated field and append a safe audit record."""
        rows: tuple[DataFieldRow, ...] = await self.list(session)
        if len(rows) >= 100:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "field limit reached")
        if any(row.key == key for row in rows):
            raise TelescreenError(ErrorCode.CONFLICT, "field key already exists")
        self._validate(key, label, description, field_type, options, role)
        await self._check_role(rows, role)
        now: datetime = self.clock.now()
        row: DataFieldRow = DataFieldRow(
            id=new_id(),
            key=key,
            label=label,
            description=description,
            field_type=field_type.value,
            options_json=self._options_json(options),
            required=required,
            position=max((item.position for item in rows), default=-1) + 1,
            role=role.value if role is not None else None,
            archived_at=None,
            created_by=actor_ref,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        await self._audit(session, actor_ref, "data_field.created", row.id, (row.key,))
        return row

    async def update(
        self,
        session: AsyncSession,
        field_id: str,
        label: str,
        description: str | None,
        field_type: DataFieldType,
        options: tuple[FieldOption, ...],
        required: bool,
        role: DataFieldRole | None,
        actor_ref: str,
    ) -> DataFieldRow:
        """Update editable field attributes while keeping its key immutable."""
        row: DataFieldRow = await self._require(session, field_id)
        self._validate(row.key, label, description, field_type, options, role)
        if row.archived_at is None:
            await self._check_role(await self.list(session), role, row.id)
        value_count: int = await self._value_count(session, row.id)
        if row.field_type != field_type.value and value_count:
            raise TelescreenError(ErrorCode.FIELD_IN_USE, "field has values")
        old_options: tuple[FieldOption, ...] = self._decode_options(row.options_json)
        removed: frozenset[str] = frozenset(option.value for option in old_options) - frozenset(
            option.value for option in options
        )
        if removed and await self._uses_options(session, row.id, removed):
            raise TelescreenError(ErrorCode.FIELD_IN_USE, "field option is in use")
        row.label = label
        row.description = description
        row.field_type = field_type.value
        row.options_json = self._options_json(options)
        row.required = required
        row.role = role.value if role is not None else None
        row.updated_at = self.clock.now()
        await self._audit(session, actor_ref, "data_field.updated", row.id, (row.key,))
        return row

    async def archive(self, session: AsyncSession, field_id: str, actor_ref: str) -> DataFieldRow:
        """Archive a field without deleting values."""
        row: DataFieldRow = await self._require(session, field_id)
        if row.archived_at is None:
            now: datetime = self.clock.now()
            row.archived_at = now
            row.updated_at = now
            await self._audit(session, actor_ref, "data_field.archived", row.id, (row.key,))
        return row

    async def unarchive(self, session: AsyncSession, field_id: str, actor_ref: str) -> DataFieldRow:
        """Restore a field after checking its semantic role is still unique."""
        row: DataFieldRow = await self._require(session, field_id)
        if row.archived_at is not None:
            await self._check_role(
                await self.list(session), DataFieldRole(row.role) if row.role else None, row.id
            )
            row.archived_at = None
            row.updated_at = self.clock.now()
            await self._audit(session, actor_ref, "data_field.unarchived", row.id, (row.key,))
        return row

    async def delete(self, session: AsyncSession, field_id: str, actor_ref: str) -> None:
        """Hard-delete a field only when it has no values."""
        row: DataFieldRow = await self._require(session, field_id)
        if await self._value_count(session, row.id):
            raise TelescreenError(ErrorCode.FIELD_IN_USE, "field has values")
        await self._audit(session, actor_ref, "data_field.deleted", row.id, (row.key,))
        await session.delete(row)

    async def reorder(
        self, session: AsyncSession, ids: tuple[str, ...], actor_ref: str
    ) -> tuple[DataFieldRow, ...]:
        """Reorder exactly the active fields and append one audit record."""
        rows: tuple[DataFieldRow, ...] = await self.list(session)
        active: tuple[DataFieldRow, ...] = tuple(row for row in rows if row.archived_at is None)
        if len(ids) != len(active) or frozenset(ids) != frozenset(row.id for row in active):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "reorder must include every active field"
            )
        for position, field_id in enumerate(ids):
            row: DataFieldRow = next(row for row in active if row.id == field_id)
            row.position = position
        keys: tuple[str, ...] = tuple(
            next(row.key for row in active if row.id == field_id) for field_id in ids
        )
        await self._audit(
            session,
            actor_ref,
            "data_field.reordered",
            "schema",
            keys,
        )
        return await self.list(session)

    async def _require(self, session: AsyncSession, field_id: str) -> DataFieldRow:
        """Fetch one field or raise a static not-found error."""
        row: DataFieldRow | None = await session.get(DataFieldRow, field_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data field not found")
        return row

    async def _value_count(self, session: AsyncSession, field_id: str) -> int:
        """Count values currently using a field."""
        count: int | None = await session.scalar(
            select(func.count())
            .select_from(DataFieldValueRow)
            .where(DataFieldValueRow.field_id == field_id)
        )
        return count or 0

    async def _uses_options(
        self, session: AsyncSession, field_id: str, removed: frozenset[str]
    ) -> bool:
        """Check stored canonical values for removed select options."""
        values: tuple[DataFieldValueRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataFieldValueRow).where(DataFieldValueRow.field_id == field_id)
                )
            ).all()
        )
        return any(
            any(
                option.as_str() in removed
                for option in _string_values(parse_json(value.value_json))
            )
            for value in values
        )

    def _validate(
        self,
        key: str,
        label: str,
        description: str | None,
        field_type: DataFieldType,
        options: tuple[FieldOption, ...],
        role: DataFieldRole | None,
    ) -> None:
        """Validate safe field names, type-specific options, and role compatibility."""
        if (
            _KEY.fullmatch(key) is None
            or not 1 <= len(label) <= 80
            or (description is not None and len(description) > 500)
        ):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid field definition")
        if self.redactor.has_hits(label) or (
            description is not None and self.redactor.has_hits(description)
        ):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive value detected")
        select_type: bool = field_type in (DataFieldType.SINGLE_SELECT, DataFieldType.MULTI_SELECT)
        if select_type != bool(options) or len(options) > 100:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid field options")
        option_values: tuple[str, ...] = tuple(option.value for option in options)
        if len(option_values) != len(set(option_values)):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "duplicate field option")
        for option in options:
            if _OPTION.fullmatch(option.value) is None or not 1 <= len(option.label) <= 80:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid field option")
            if self.redactor.has_hits(option.label):
                raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive value detected")
        purpose_types: tuple[DataFieldType, ...] = (
            DataFieldType.TEXT,
            DataFieldType.LONG_TEXT,
            DataFieldType.SINGLE_SELECT,
            DataFieldType.MULTI_SELECT,
        )
        compatible: bool = (
            role is None
            or (role is DataFieldRole.PURPOSE and field_type in purpose_types)
            or (role is DataFieldRole.OWNER and field_type is DataFieldType.REFERENCE)
            or (role is DataFieldRole.DELETION_DUE and field_type is DataFieldType.DATE)
        )
        if not compatible:
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "field role is incompatible with type"
            )

    async def _check_role(
        self, rows: tuple[DataFieldRow, ...], role: DataFieldRole | None, except_id: str = ""
    ) -> None:
        """Ensure a role is assigned to at most one active field."""
        if role is not None and any(
            row.id != except_id and row.archived_at is None and row.role == role.value
            for row in rows
        ):
            raise TelescreenError(ErrorCode.CONFLICT, "field role already exists")

    async def _audit(
        self,
        session: AsyncSession,
        actor_ref: str,
        action: str,
        target_id: str,
        keys: tuple[str, ...],
    ) -> None:
        """Append only safe field ids, keys and counts to the audit log."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                actor_ref,
                action,
                "data_field",
                target_id,
                "Data field schema changed",
                json_object(
                    (("keys", json_str(",".join(keys))), ("changed_count", json_int(len(keys))))
                ),
            ),
        )

    @staticmethod
    def _options_json(options: tuple[FieldOption, ...]) -> str | None:
        """Serialize option values and labels as canonical JSON."""
        if not options:
            return None
        return dump_json(_options_value(options))

    @staticmethod
    def _decode_options(raw: str | None) -> tuple[FieldOption, ...]:
        """Decode the persisted option list into typed options."""
        if raw is None:
            return ()
        value: JsonValue = parse_json(raw)
        options: list[FieldOption] = []
        for item in value.items:
            option_value: JsonValue | None = item.get("value")
            option_label: JsonValue | None = item.get("label")
            if option_value is not None and option_label is not None:
                options.append(
                    FieldOption(option_value.as_str() or "", option_label.as_str() or "")
                )
        return tuple(options)


def _options_value(options: tuple[FieldOption, ...]) -> JsonValue:
    """Build closed JSON option data."""
    return json_array(
        tuple(
            json_object((("value", json_str(option.value)), ("label", json_str(option.label))))
            for option in options
        )
    )


def _string_values(value: JsonValue) -> tuple[JsonValue, ...]:
    """Return scalar strings from a select value."""
    return value.items if value.items else (value,)
