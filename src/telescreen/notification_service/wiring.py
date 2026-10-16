"""Public composition entry point for the Notification service."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.notification_service.app import (
    NotificationRuntime,
)
from telescreen.notification_service.app import (
    build_notification_runtime as _build,
)


def build_notification_runtime(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> NotificationRuntime:
    """Build Notification-owned persistence, auth, delivery and API services."""
    return _build(settings, env, http, sessions, clock)
