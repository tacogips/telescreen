"""Resolve inventory lifecycle actors without changing stored actor references."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader
from telescreen.ports.directory import ActorDirectory, ActorDisplay


@dataclass(frozen=True)
class InventoryActorDirectory(ActorDirectory):
    """Resolve collector actors and delegate other actor references in one batch."""

    base: ActorDirectory
    configs: CollectorConfigReader

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Resolve collector names while preserving input length and order."""
        resolved: list[ActorDisplay | None] = [None] * len(refs)
        other_positions: list[int] = []
        other_refs: list[str] = []
        collector_ids: set[str] = set()
        parsed: list[tuple[str, str, str]] = []
        for position, ref in enumerate(refs):
            kind: str
            separator: str
            key: str
            kind, separator, key = ref.partition(":")
            parsed.append((kind, separator, key))
            if kind == "collector" and separator and key:
                collector_ids.add(key)
            else:
                other_positions.append(position)
                other_refs.append(ref)
        collector_names: list[tuple[str, str]] = []
        for collector_id in collector_ids:
            config: CollectorConfig | None = await self.configs.get(session, collector_id)
            collector_names.append(
                (collector_id, config.name if config is not None else "Telescreen")
            )
        for position, (kind, separator, key) in enumerate(parsed):
            if kind == "collector" and separator and key:
                name: str = next(
                    value for identifier, value in collector_names if identifier == key
                )
                resolved[position] = ActorDisplay(
                    refs[position], ActorDisplayKind.SYSTEM, name, None, True
                )
        if other_refs:
            delegated: tuple[ActorDisplay, ...] = await self.base.display(
                session, tuple(other_refs)
            )
            for position, display in zip(other_positions, delegated, strict=True):
                resolved[position] = display
        return tuple(display for display in resolved if display is not None)
