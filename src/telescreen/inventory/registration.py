"""Manual data asset registration with uniqueness and safe auditing."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    DataAssetKind,
    DataAssetOrigin,
    DataAssetState,
    DataEventKind,
    ErrorCode,
)
from telescreen.db.models import DataAssetRow, DataFieldRow
from telescreen.directory.actors import actor_kind_for
from telescreen.inventory.repository import DataEventDraft, InventoryRepository
from telescreen.inventory.values import ValueInput, ValueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog

_SOURCE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,63}$")


@dataclass(frozen=True)
class RegistrationBody:
    """Safe, validated input for one manual asset registration."""

    name: str
    source: str
    locator: str = field(repr=False)
    kind: DataAssetKind
    item_count: int | None = None
    values: tuple[ValueInput, ...] = ()


@dataclass(frozen=True)
class RegistrationService:
    """Register manual assets and their initial metadata atomically."""

    repository: InventoryRepository
    values: ValueService
    fingerprinter: Fingerprinter
    redactor: Redactor
    audit: AuditLog
    clock: Clock

    async def register(self, session: AsyncSession, body: RegistrationBody, actor_ref: str) -> str:
        """Create a registered asset or return a conflict for duplicate identity."""
        if (
            not 1 <= len(body.name) <= 200
            or not 1 <= len(body.locator) <= 1024
            or _SOURCE.fullmatch(body.source) is None
        ):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid registration")
        if body.item_count is not None and body.item_count < 0:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid registration")
        if (
            self.redactor.has_hits(body.name)
            or self.redactor.has_hits(body.source)
            or self.redactor.has_hits(body.locator)
        ):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive value detected")
        resource_key: str = self.fingerprinter.resource_key(
            "registered:" + body.source, body.locator
        )
        existing: DataAssetRow | None = await self.repository.get_by_resource_key(
            session, resource_key
        )
        if existing is not None:
            raise TelescreenError(ErrorCode.CONFLICT, "data asset already exists")
        now: datetime = self.clock.now()
        number: int = await self.repository.next_asset_number(session)
        row: DataAssetRow = DataAssetRow(
            id=new_id(),
            number=number,
            resource_key=resource_key,
            source=body.source,
            origin=DataAssetOrigin.REGISTERED.value,
            kind=body.kind.value,
            state=DataAssetState.PRESENT.value,
            name=body.name,
            display_locator=body.locator,
            collector_config_id=None,
            sweep_covered=False,
            item_count=body.item_count,
            first_seen_at=now,
            last_seen_at=now,
            deleted_at=None,
            overdue_notified_for=None,
            created_by=actor_ref,
            created_at=now,
            updated_at=now,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            raise TelescreenError(ErrorCode.CONFLICT, "data asset already exists") from None
        await self.repository.append_event(
            session,
            DataEventDraft(
                asset_id=row.id,
                kind=DataEventKind.REGISTERED,
                recorded_at=now,
                actor_ref=actor_ref,
                item_count_after=body.item_count,
            ),
        )
        fields: tuple[DataFieldRow, ...] = tuple(
            (await session.scalars(select(DataFieldRow))).all()
        )
        await self.values.put(session, row, fields, body.values, actor_ref)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind_for(actor_ref),
                actor_ref,
                "data_asset.registered",
                "data_asset",
                row.id,
                "Data asset registered",
                json_object((("asset_id", json_str(row.id)), ("number", json_int(number)))),
            ),
        )
        return row.id
