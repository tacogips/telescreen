"""Member-facing asset mutations with audit and sensitive-text checks."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    DataAssetKind,
    DataAssetOrigin,
    DataAssetState,
    DataEventKind,
    ErrorCode,
)
from telescreen.db.models import DataAssetRow
from telescreen.inventory.lifecycle import delete_asset, restore_asset
from telescreen.inventory.repository import DataEventDraft, InventoryRepository
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.identity_context import actor_kind_for


@dataclass(frozen=True)
class AssetService:
    """Validate and audit user-triggered data asset mutations."""

    repository: InventoryRepository
    audit: AuditLog
    redactor: Redactor
    clock: Clock

    async def patch(
        self,
        session: AsyncSession,
        asset_id: str,
        name: str | None,
        kind: str | None,
        actor_ref: str,
    ) -> DataAssetRow:
        """Update a registered asset's safe name or its kind."""
        asset: DataAssetRow = await self._require(session, asset_id)
        changed: list[str] = []
        if name is not None:
            if asset.origin != DataAssetOrigin.REGISTERED.value:
                raise TelescreenError(
                    ErrorCode.VALIDATION_ERROR, "discovered asset name is read-only"
                )
            self._safe_text(name, 1, 200)
            asset.name = self.redactor.redact(name).text
            changed.append("name")
        if kind is not None:
            try:
                asset.kind = DataAssetKind(kind).value
            except ValueError:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid asset kind") from None
            changed.append("kind")
        if changed:
            asset.updated_at = self.clock.now()
            await self._audit(
                session,
                actor_ref,
                "data_asset.updated",
                asset.id,
                "Data asset updated",
                tuple(changed),
            )
        return asset

    async def record_event(
        self,
        session: AsyncSession,
        asset_id: str,
        occurred_at: datetime,
        actor_ref: str,
        items_added: int | None,
        items_removed: int | None,
        item_count_after: int | None,
        note: str | None,
    ) -> DataAssetRow:
        """Record a safe manual partial-change event."""
        asset: DataAssetRow = await self._require(session, asset_id)
        now: datetime = self.clock.now()
        occurred: datetime = ensure_utc(occurred_at)
        if occurred > now + timedelta(minutes=5):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "occurred_at is too far in the future"
            )
        if note is not None:
            self._safe_text(note, 1, 1000)
        if any(
            value is not None and value < 0
            for value in (items_added, items_removed, item_count_after)
        ):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "counts must be non-negative")
        event_count_after: int | None = item_count_after
        if asset.origin == DataAssetOrigin.REGISTERED.value:
            if item_count_after is not None:
                asset.item_count = item_count_after
            elif items_added is not None or items_removed is not None:
                asset.item_count = max(
                    0, (asset.item_count or 0) + (items_added or 0) - (items_removed or 0)
                )
            event_count_after = asset.item_count
        await self.repository.append_event(
            session,
            DataEventDraft(
                asset_id=asset.id,
                kind=DataEventKind.MANUAL_CHANGE,
                recorded_at=now,
                actor_ref=actor_ref,
                occurred_at=occurred,
                items_added=items_added,
                items_removed=items_removed,
                item_count_after=event_count_after,
                note=self.redactor.redact(note).text if note is not None else None,
            ),
        )
        await self._audit(
            session,
            actor_ref,
            "data_asset.event_recorded",
            asset.id,
            "Data asset event recorded",
            (),
        )
        return asset

    async def mark_deleted(
        self,
        session: AsyncSession,
        asset_id: str,
        actor_ref: str,
        occurred_at: datetime | None,
        note: str | None,
    ) -> DataAssetRow:
        """Manually mark a present asset deleted and audit the change."""
        asset: DataAssetRow = await self._require(session, asset_id)
        if asset.state == DataAssetState.DELETED.value:
            raise TelescreenError(ErrorCode.CONFLICT, "asset is already deleted")
        now: datetime = self.clock.now()
        occurred: datetime | None = ensure_utc(occurred_at) if occurred_at is not None else None
        if occurred is not None and occurred > now + timedelta(minutes=5):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "occurred_at is too far in the future"
            )
        if note is not None:
            self._safe_text(note, 1, 1000)
        await delete_asset(
            session,
            asset,
            self.repository,
            actor_ref,
            now,
            None,
            occurred,
            self.redactor.redact(note).text if note is not None else None,
        )
        await self._audit(
            session,
            actor_ref,
            "data_asset.marked_deleted",
            asset.id,
            "Data asset marked deleted",
            (),
        )
        return asset

    async def restore(self, session: AsyncSession, asset_id: str, actor_ref: str) -> DataAssetRow:
        """Restore an asset only when it is currently deleted."""
        asset: DataAssetRow = await self._require(session, asset_id)
        if asset.state != DataAssetState.DELETED.value:
            raise TelescreenError(ErrorCode.CONFLICT, "asset is not deleted")
        await restore_asset(session, asset, self.repository, actor_ref, self.clock.now())
        await self._audit(
            session, actor_ref, "data_asset.restored", asset.id, "Data asset restored", ()
        )
        return asset

    async def _require(self, session: AsyncSession, asset_id: str) -> DataAssetRow:
        """Load an asset or raise a static not-found error."""
        asset: DataAssetRow | None = await self.repository.get(session, asset_id, lock=True)
        if asset is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
        return asset

    def _safe_text(self, value: str, minimum: int, maximum: int) -> None:
        """Require a bounded string with no detected sensitive value."""
        if not minimum <= len(value) <= maximum:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid text length")
        if self.redactor.has_hits(value):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive value detected")

    async def _audit(
        self,
        session: AsyncSession,
        actor_ref: str,
        action: str,
        asset_id: str,
        summary: str,
        changed: tuple[str, ...],
    ) -> None:
        """Append a safe audit entry containing only identifiers and field names."""
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind_for(actor_ref),
                actor_ref,
                action,
                "data_asset",
                asset_id,
                summary,
                json_object(
                    (
                        ("fields", json_str(",".join(changed))),
                        ("changed_count", json_int(len(changed))),
                    ),
                ),
            ),
        )
