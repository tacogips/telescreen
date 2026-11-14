"""Owner-neutral inputs shared by local and remote inventory composition."""

from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.context import ApiContext
from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.integrations.status import ChannelStatusSource
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import CollectorConfigReader
from telescreen.ports.directory import ActorDirectory, OwnerDirectory, RecipientResolver
from telescreen.ports.issues import ResourceIssueReader
from telescreen.ports.notifications import MemberNotifier, NotificationSender, NotificationTester
from telescreen.ports.templates import TemplateCatalog
from telescreen.ports.workflow import ScheduleProvider, TaskHandler, TaskQueue


@dataclass(frozen=True)
class NotificationParts:
    """Notification ports and lifecycle registrations for either topology."""

    sender: NotificationSender
    members: MemberNotifier
    catalog: TemplateCatalog
    tester: NotificationTester
    channel_status: ChannelStatusSource
    routers: tuple[APIRouter, ...]
    handlers: tuple[TaskHandler, ...]
    providers: tuple[ScheduleProvider, ...]


@dataclass(frozen=True)
class WiringV14Parts:
    """Runtime dependencies needed by v1.4 inventory wiring."""

    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    settings: TelescreenSettings
    redactor: Redactor
    fingerprinter: Fingerprinter
    collector_configs: CollectorConfigReader
    issue_reader: ResourceIssueReader
    actors: ActorDirectory
    recipients: RecipientResolver
    owners: OwnerDirectory
    members: MemberNotifier
    sender: NotificationSender
    queue: TaskQueue
    context: ApiContext
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None
