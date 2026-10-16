"""Inline channel tester for member notification integrations."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    EngineTaskKind,
    ErrorCode,
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from telescreen.db.models import NotificationRow
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.member_send import MemberReferences
from telescreen.notifications.repository import NotificationRepository
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.notification_commands import ChannelStatus
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
    NotificationTester,
    TestOutcome,
)
from telescreen.ports.workflow import ClaimedTask


@dataclass(frozen=True)
class NotificationServiceTester(NotificationTester):
    """Queue one synthetic notification and run its delivery handler inline."""

    sessions: async_sessionmaker[AsyncSession]
    service: NotificationSender
    delivery: NotificationDeliveryHandler
    members: OwnerDirectory
    repository: NotificationRepository = field(default_factory=NotificationRepository)
    member_references: MemberReferences | None = None
    channel_status: ChannelStatus | None = None

    async def send_test(
        self,
        channel: NotificationChannel,
        member_id: str,
        template_name: str | None = None,
    ) -> TestOutcome:
        """Test a member's selected channel without returning message contents."""
        if self.channel_status is not None and not _channel_configured(
            self.channel_status, channel
        ):
            return TestOutcome(False, ErrorCode.INTEGRATION_UNCONFIGURED, None)
        async with self.sessions.begin() as session:
            recipient_ref: str | None
            if self.member_references is None:
                recipient_ref = await self.members.member_ref(session, member_id)
            else:
                try:
                    recipient_ref = await self.member_references.member_reference(
                        session, member_id
                    )
                except TelescreenError as error:
                    if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                        raise
                    recipient_ref = None
            if recipient_ref is None:
                return TestOutcome(False, ErrorCode.RECIPIENT_UNREACHABLE, None)
            request: NotificationRequest = NotificationRequest(
                channel,
                (recipient_ref,),
                "",
                "",
                "system:notification-test",
                event=NotificationEvent.INTEGRATION_TEST,
                template_name=template_name,
                context=NotificationContext(health_target="telescreen"),
                record_unreachable=True,
            )
            receipt: NotificationReceipt = await self.service.send(session, request)
        identifiers: tuple[str, ...] = receipt.notification_ids + receipt.skipped
        if not identifiers:
            return TestOutcome(False, ErrorCode.RECIPIENT_UNREACHABLE, None)
        notification_id: str = identifiers[0]
        if receipt.notification_ids:
            await self.delivery.handle(
                ClaimedTask(
                    notification_id,
                    EngineTaskKind.NOTIFICATION_DELIVERY,
                    notification_id,
                    json_null(),
                    1,
                    1,
                    f"notify:{notification_id}",
                )
            )
        async with self.sessions() as session:
            row: NotificationRow | None = await self.repository.get(session, notification_id)
        if row is None:
            return TestOutcome(False, ErrorCode.RECIPIENT_UNREACHABLE, notification_id)
        ok: bool = row.status == NotificationStatus.SENT.value
        error_code: ErrorCode | None = (
            ErrorCode(row.error_code) if row.error_code is not None else None
        )
        return TestOutcome(ok, error_code, notification_id)


def _channel_configured(status: ChannelStatus, channel: NotificationChannel) -> bool:
    """Return the static configuration flag for one notification transport."""
    return status.email if channel is NotificationChannel.EMAIL else status.slack
