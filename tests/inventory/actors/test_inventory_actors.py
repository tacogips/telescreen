"""Inventory actor directory behavior."""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import json_object
from telescreen.core.vocabulary import ActorDisplayKind, CollectorKind, TriggerMode
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader, CustomSelector
from telescreen.ports.directory import ActorDirectory, ActorDisplay
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class BaseDirectory(ActorDirectory):
    """Record delegated refs and return safe display placeholders."""

    calls: list[tuple[str, ...]]

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Resolve delegated refs in their original order."""
        del session
        self.calls.append(refs)
        return tuple(ActorDisplay(ref, ActorDisplayKind.UNKNOWN, ref, None, False) for ref in refs)


@dataclass(frozen=True)
class ConfigReader(CollectorConfigReader):
    """Return one configured collector and record unique lookups."""

    names: tuple[tuple[str, str], ...]
    calls: list[str]

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return the collector with the requested id, if configured."""
        del session
        self.calls.append(collector_config_id)
        name: str | None = next(
            (value for key, value in self.names if key == collector_config_id), None
        )
        if name is None:
            return None
        return CollectorConfig(
            id=collector_config_id,
            name=name,
            kind=CollectorKind.CUSTOM,
            trigger_mode=TriggerMode.EVENT,
            cron=None,
            sweep_cron=None,
            sweep_max_concurrency=1,
            selector=CustomSelector(json_object(())),
            credential_ref=None,
            enabled=True,
        )

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """This test reader does not resolve collector names."""
        del session, name
        return None


@pytest.mark.asyncio
async def test_resolves_collector_and_delegates_other_refs_once_in_order() -> None:
    """Collector refs resolve safely while other refs are delegated as one batch."""
    from telescreen.inventory.actors import InventoryActorDirectory

    database: SqliteHarness = await make_sqlite_harness()
    base: BaseDirectory = BaseDirectory([])
    configs: ConfigReader = ConfigReader((("c1", "inventory-live"),), [])
    directory: InventoryActorDirectory = InventoryActorDirectory(base, configs)
    try:
        async with database.sessions() as session:
            result: tuple[ActorDisplay, ...] = await directory.display(
                session,
                ("member:alex", "collector:c1", "system:inventory", "collector:c1"),
            )
            missing: tuple[ActorDisplay, ...] = await directory.display(
                session, ("collector:gone",)
            )
            malformed: tuple[ActorDisplay, ...] = await directory.display(session, ("collector:",))
            empty: tuple[ActorDisplay, ...] = await directory.display(session, ())
        assert tuple(item.ref for item in result) == (
            "member:alex",
            "collector:c1",
            "system:inventory",
            "collector:c1",
        )
        assert (
            result[1].kind is ActorDisplayKind.SYSTEM and result[1].display_name == "inventory-live"
        )
        assert result[3].display_name == "inventory-live"
        assert tuple(item.ref for item in missing) == ("collector:gone",)
        assert (
            missing[0].kind is ActorDisplayKind.SYSTEM and missing[0].display_name == "Telescreen"
        )
        assert malformed[0].display_name == "collector:"
        assert empty == ()
        assert base.calls == [("member:alex", "system:inventory"), ("collector:",)]
        assert configs.calls == ["c1", "gone"]
    finally:
        await database.close()
