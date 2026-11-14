"""Field schema and value validation API routes."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import DataFieldRole, DataFieldType
from telescreen.db.models import DataFieldRow, DataFieldValueRow
from telescreen.inventory.fields import FieldOption, FieldService
from telescreen.inventory.values import ValueInput, ValueIssue, ValueValidator


@pydantic.dataclasses.dataclass(frozen=True)
class FieldOptionBody:
    """One select option submitted by an administrator."""

    value: str
    label: str


@pydantic.dataclasses.dataclass(frozen=True)
class DataFieldCreateBody:
    """New field definition body."""

    key: str
    label: str
    field_type: DataFieldType
    description: str | None = None
    options: list[FieldOptionBody] = pydantic.Field(default_factory=list)
    required: bool = False
    role: DataFieldRole | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class DataFieldUpdateBody:
    """Editable field definition attributes; the key is intentionally absent."""

    label: str
    field_type: DataFieldType
    description: str | None = None
    options: list[FieldOptionBody] = pydantic.Field(default_factory=list)
    required: bool = False
    role: DataFieldRole | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class DataFieldReorderBody:
    """Complete ordered active field id list."""

    ids: list[str]


@pydantic.dataclasses.dataclass(frozen=True)
class ValueBody:
    """One value submitted for validation or update."""

    key: str
    value_json: str | None


@pydantic.dataclasses.dataclass(frozen=True)
class ValuesBody:
    """Set of values submitted together."""

    values: list[ValueBody]


@dataclass(frozen=True)
class DataFieldsRouterDeps:
    """Dependencies for field schema routes."""

    ctx: ApiContext
    fields: FieldService
    validator: ValueValidator
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None


async def _value_count(session: AsyncSession, field_id: str) -> int:
    """Count values for one field."""
    count: int | None = await session.scalar(
        select(func.count())
        .select_from(DataFieldValueRow)
        .where(DataFieldValueRow.field_id == field_id)
    )
    return count or 0


async def _field_json(session: AsyncSession, row: DataFieldRow) -> JsonValue:
    """Serialize one field and its current value count."""
    options: JsonValue = (
        parse_json(row.options_json) if row.options_json is not None else json_array(())
    )
    return json_object(
        (
            ("id", json_str(row.id)),
            ("key", json_str(row.key)),
            ("label", json_str(row.label)),
            (
                "description",
                json_str(row.description) if row.description is not None else json_null(),
            ),
            ("field_type", json_str(row.field_type)),
            ("options", options),
            ("required", json_bool(row.required)),
            ("position", json_int(row.position)),
            ("role", json_str(row.role) if row.role is not None else json_null()),
            ("archived", json_bool(row.archived_at is not None)),
            ("value_count", json_int(await _value_count(session, row.id))),
            ("created_at", json_str(row.created_at.isoformat())),
            ("updated_at", json_str(row.updated_at.isoformat())),
        )
    )


def build_data_fields_router(deps: DataFieldsRouterDeps) -> APIRouter:
    """Build schema routes with fixed route ordering and one permission each."""
    data_sessions: async_sessionmaker[AsyncSession] = deps.inventory_sessions or deps.ctx.sessions
    router: APIRouter = APIRouter()

    @router.get("/api/data/fields")
    async def list_fields(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        """Return all fields, including archived definitions."""
        del principal
        async with data_sessions() as session:
            rows: tuple[DataFieldRow, ...] = await deps.fields.list(session)
            items: list[JsonValue] = [await _field_json(session, row) for row in rows]
        return Response(
            dump_json(json_object((("items", json_array(items)),))), media_type="application/json"
        )

    @router.post("/api/data/fields/validate-values")
    async def validate_values(
        body: ValuesBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        """Validate values and return only field keys and issue codes."""
        del principal
        values: tuple[ValueInput, ...] = tuple(
            ValueInput(item.key, item.value_json) for item in body.values
        )
        async with data_sessions() as session:
            fields: tuple[DataFieldRow, ...] = await deps.fields.list(session)
            issues: tuple[ValueIssue, ...] = await deps.validator.validate(fields, values)
            supplied: frozenset[str] = frozenset(
                item.key for item in values if item.value_json is not None
            )
            missing: tuple[ValueIssue, ...] = tuple(
                ValueIssue(row.key, "required")
                for row in fields
                if row.archived_at is None and row.required and row.key not in supplied
            )
            all_issues: tuple[ValueIssue, ...] = issues + missing
        errors: list[JsonValue] = [
            json_object((("field", json_str(item.field)), ("code", json_str(item.code))))
            for item in all_issues
        ]
        return Response(
            dump_json(
                json_object((("valid", json_bool(not all_issues)), ("errors", json_array(errors))))
            ),
            media_type="application/json",
        )

    @router.post("/api/data/fields/reorder")
    async def reorder_fields(
        body: DataFieldReorderBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Set the order of all active fields."""
        async with data_sessions.begin() as session:
            rows: tuple[DataFieldRow, ...] = await deps.fields.reorder(
                session, tuple(body.ids), principal.actor_ref()
            )
            items: list[JsonValue] = [await _field_json(session, row) for row in rows]
        return Response(
            dump_json(json_object((("items", json_array(items)),))), media_type="application/json"
        )

    @router.post("/api/data/fields", status_code=201)
    async def create_field(
        body: DataFieldCreateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Create a field definition."""
        options: tuple[FieldOption, ...] = tuple(
            FieldOption(item.value, item.label) for item in body.options
        )
        async with data_sessions.begin() as session:
            row: DataFieldRow = await deps.fields.create(
                session,
                body.key,
                body.label,
                body.description,
                body.field_type,
                options,
                body.required,
                body.role,
                principal.actor_ref(),
            )
            content: JsonValue = await _field_json(session, row)
        return Response(dump_json(content), status_code=201, media_type="application/json")

    @router.patch("/api/data/fields/{field_id}")
    async def update_field(
        field_id: str,
        body: DataFieldUpdateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Update a field without accepting key mutation."""
        options: tuple[FieldOption, ...] = tuple(
            FieldOption(item.value, item.label) for item in body.options
        )
        async with data_sessions.begin() as session:
            row: DataFieldRow = await deps.fields.update(
                session,
                field_id,
                body.label,
                body.description,
                body.field_type,
                options,
                body.required,
                body.role,
                principal.actor_ref(),
            )
            content: JsonValue = await _field_json(session, row)
        return Response(dump_json(content), media_type="application/json")

    @router.delete("/api/data/fields/{field_id}", status_code=204)
    async def delete_field(
        field_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Delete an unused field."""
        async with data_sessions.begin() as session:
            await deps.fields.delete(session, field_id, principal.actor_ref())
        return Response(status_code=204)

    @router.post("/api/data/fields/{field_id}/archive")
    async def archive_field(
        field_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Archive a field."""
        async with data_sessions.begin() as session:
            row: DataFieldRow = await deps.fields.archive(session, field_id, principal.actor_ref())
            content: JsonValue = await _field_json(session, row)
        return Response(dump_json(content), media_type="application/json")

    @router.post("/api/data/fields/{field_id}/unarchive")
    async def unarchive_field(
        field_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_FIELDS_ADMIN))
        ],
    ) -> Response:
        """Unarchive a field."""
        async with data_sessions.begin() as session:
            row: DataFieldRow = await deps.fields.unarchive(
                session, field_id, principal.actor_ref()
            )
            content: JsonValue = await _field_json(session, row)
        return Response(dump_json(content), media_type="application/json")

    return router
