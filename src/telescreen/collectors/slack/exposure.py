"""Map Slack conversation and file metadata into transient exposure context."""

from __future__ import annotations

from telescreen.collectors.slack.client import SlackConversationInfo, SlackFile
from telescreen.core.vocabulary import ChannelKind, Visibility
from telescreen.ports.collectors import Exposure


def slack_exposure(
    info: SlackConversationInfo | None,
    own_team_id: str,
    file: SlackFile | None = None,
) -> Exposure | None:
    """Return Slack exposure enums and counts without retaining provider principals."""
    if info is None:
        return (
            Exposure(Visibility.ANYONE_WITH_LINK)
            if file is not None and file.public_url_shared
            else None
        )
    if info.is_ext_shared:
        external_team_count: int = len(
            {team_id for team_id in info.connected_team_ids if team_id != own_team_id}
        )
        exposure: Exposure = Exposure(
            visibility=Visibility.EXTERNAL_SHARED,
            external_principal_count=external_team_count,
            channel_kind=ChannelKind.CONNECT,
        )
    elif info.is_im:
        exposure = Exposure(Visibility.PRIVATE, channel_kind=ChannelKind.DM)
    elif info.is_mpim:
        exposure = Exposure(Visibility.INTERNAL, channel_kind=ChannelKind.DM)
    elif info.is_private:
        exposure = Exposure(Visibility.INTERNAL, channel_kind=ChannelKind.PRIVATE)
    else:
        exposure = Exposure(Visibility.DOMAIN, channel_kind=ChannelKind.PUBLIC)
    if file is not None and file.public_url_shared:
        return Exposure(
            Visibility.ANYONE_WITH_LINK,
            external_principal_count=exposure.external_principal_count,
            channel_kind=exposure.channel_kind,
        )
    return exposure
