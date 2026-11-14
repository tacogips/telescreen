"""Transactional scheduler for workflow timers and registered providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.ports.workflow import ScheduleProvider

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class Scheduler:
    """Tick schedule providers independently so one failure cannot block others."""

    sessions: async_sessionmaker[AsyncSession]
    providers: tuple[ScheduleProvider, ...]
    clock: Clock

    async def tick(self) -> int:
        """Run every provider in its own transaction and return created work."""
        created: int = 0
        now: datetime = self.clock.now()
        for provider in self.providers:
            try:
                async with self.sessions.begin() as session:
                    created += await provider.tick(session, now)
            except Exception as error:
                logger.error("schedule provider failed type=%s", type(error).__name__)
        return created
