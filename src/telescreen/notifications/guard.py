"""Count-only outbound content inspection adapter."""

from dataclasses import dataclass

from telescreen.core.vocabulary import DlpVerdict
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage
from telescreen.ports.notifications import GuardVerdict, OutboundContentGuard


@dataclass(frozen=True)
class DlpOutboundGuard(OutboundContentGuard):
    """Expose the outbound DLP gate without returning detected content."""

    gate: DlpGate

    async def inspect(self, text: str) -> GuardVerdict:
        """Return only the allow decision and total detection count."""
        message: OutboundMessage = OutboundMessage("", text)
        inspected: CensoredMessage = await self.gate.inspect(message)
        return GuardVerdict(inspected.verdict is not DlpVerdict.BLOCKED, inspected.hit_count)
