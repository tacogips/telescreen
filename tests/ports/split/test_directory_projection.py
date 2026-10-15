"""Contracts for directory projections shared with Inventory and Notification."""

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
    TeamEntity,
    decode_directory_entity,
    encode_directory_entity,
    for_inventory,
)


def test_member_projection_round_trip_and_inventory_redaction() -> None:
    """Inventory receives identity fields without delivery addresses."""
    payload: MemberEntity = MemberEntity(
        "member-1", "m", "Masked Member", True, None, "m@example.test", "U1"
    )
    entity: DirectoryEntity = DirectoryEntity(
        DirectoryEntityKind.MEMBER, "member-1", 1, True, payload
    )
    assert decode_directory_entity(encode_directory_entity(entity)) == entity
    assert for_inventory(entity).payload == MemberEntity(
        "member-1", "m", "Masked Member", True, None, None, None
    )


def test_team_projection_redaction_and_api_token_label() -> None:
    """Inventory loses team delivery data while API-token labels remain projected."""
    team: TeamEntity = TeamEntity("team-1", "ops", "Operations", None, (), "ops@example.test", "C1")
    entity: DirectoryEntity = DirectoryEntity(DirectoryEntityKind.TEAM, "team-1", 1, True, team)
    assert for_inventory(entity).payload == TeamEntity(
        "team-1", "ops", "Operations", None, (), None, None
    )
    token: ApiTokenEntity = ApiTokenEntity("token-1", "deploy", "hash", (), None, False)
    token_entity: DirectoryEntity = DirectoryEntity(
        DirectoryEntityKind.API_TOKEN, "token-1", 1, True, token
    )
    assert decode_directory_entity(encode_directory_entity(token_entity)) == token_entity


def test_invalid_presence_and_kind_are_safe_errors() -> None:
    """Reject malformed projections with a static error detail."""
    bad_payload: str = (
        '{"kind":"member","id":"member-1","revision":1,"present":true,"payload":null}'
    )
    with pytest.raises(TelescreenError) as caught:
        decode_directory_entity(bad_payload)
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert "member-1" not in caught.value.detail
    mismatch: str = (
        '{"kind":"team","id":"member-1","revision":1,"present":true,'
        '"payload":{"kind":"member","id":"member-1","handle":"m",'
        '"display_name":"M","active":true,"role":null,"email":null,"slack_user_id":null}}'
    )
    with pytest.raises(TelescreenError):
        decode_directory_entity(mismatch)
