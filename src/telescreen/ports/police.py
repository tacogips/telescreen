"""Police task dispatch port and DTO."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue


@dataclass(frozen=True)
class PoliceTaskRequest:
    """Safe task input and dispatch constraints for a Police worker."""

    task_type: str
    labels: tuple[str, ...]
    input: JsonValue
    deadline: datetime
    step_run_id: str | None = None
    collector_config_id: str | None = None
    max_attempts: int = 3


class PoliceGateway(Protocol):
    """Dispatch and cancel Police tasks independently of transport mode."""

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        """Dispatch a task and return its id."""
        ...

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        """Cancel a task if it has not completed."""
        ...
