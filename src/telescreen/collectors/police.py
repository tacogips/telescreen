"""Dispatch centrally configured Police collectors."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_object, json_str
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import CollectorConfig, PoliceSelector
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest


@dataclass(frozen=True)
class PoliceCollector:
    """Dispatch detect tasks through the transport-neutral Police gateway."""

    gateway: PoliceGateway
    clock: Clock

    async def dispatch_detect(self, session: AsyncSession, config: CollectorConfig) -> str:
        """Dispatch one detector task using the selector's safe input."""
        if config.kind is not CollectorKind.POLICE or not isinstance(
            config.selector, PoliceSelector
        ):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "collector is not configured for Police"
            )
        selector: PoliceSelector = config.selector
        payload: JsonValue = json_object(
            (("selector", selector.input), ("collector_config_id", json_str(config.id)))
        )
        return await self.gateway.dispatch(
            session,
            PoliceTaskRequest(
                "detect",
                selector.labels,
                payload,
                self.clock.now() + timedelta(hours=1),
                collector_config_id=config.id,
            ),
        )
