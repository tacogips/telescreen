"""Contracts for durable notification commands."""

from datetime import UTC, datetime

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.ports.handoff_limits import MAX_HANDOFF_BYTES
from telescreen.ports.notification_commands import (
    MemberSendOperation,
    NotificationCommand,
    SendOperation,
    decode_notification_command,
    encode_notification_command,
    notification_row_id,
    operation_seed,
)
from telescreen.ports.notification_types import (
    ContextFieldHits,
    NotificationContext,
    ProducerRedactionHits,
)

NOW: datetime = datetime(2026, 10, 15, tzinfo=UTC)


def test_notification_command_round_trip_and_stable_ids() -> None:
    """Commands round trip and recipient row ids remain deterministic."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:m1",),
        "Subject",
        "Body",
        "system:test",
        None,
        None,
        None,
        None,
        NotificationContext(issue_short_id="ISSUE-1"),
        False,
        None,
        None,
        False,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    command: NotificationCommand = NotificationCommand("event-1", NOW, (operation,))
    assert decode_notification_command(encode_notification_command(command)) == command
    seed: str = operation_seed("e", 0)
    assert notification_row_id(seed, 1) == notification_row_id(seed, 1)
    assert notification_row_id(seed, 1) != notification_row_id(seed, 2)


def test_notification_command_rejects_unknown_version_without_echo() -> None:
    """Unknown wire versions fail with a content-free validation error."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:m1",),
        "private",
        "private",
        "system:test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        False,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    encoded: str = encode_notification_command(NotificationCommand("event-1", NOW, (operation,)))
    invalid: str = encoded.replace('"schema_version":1', '"schema_version":2')
    with pytest.raises(TelescreenError) as caught:
        decode_notification_command(invalid)
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert "private" not in caught.value.detail


def test_notification_redaction_fields_are_allowlisted_and_unique() -> None:
    """Redaction metadata accepts only distinct string context field names."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:m1",),
        "s",
        "b",
        "system:test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        False,
        None,
        False,
        ProducerRedactionHits(0, 0, (ContextFieldHits("issue_short_id", 1),)),
    )
    valid: NotificationCommand = NotificationCommand("event-1", NOW, (operation,))
    assert decode_notification_command(encode_notification_command(valid)) == valid
    for fields in (
        (ContextFieldHits("issue_risk_score", 1),),
        (ContextFieldHits("unknown", 1),),
        (ContextFieldHits("issue_short_id", 1), ContextFieldHits("issue_short_id", 2)),
    ):
        invalid_operation: SendOperation = SendOperation(
            NotificationChannel.EMAIL,
            ("member:m1",),
            "s",
            "b",
            "system:test",
            None,
            None,
            None,
            None,
            None,
            False,
            None,
            None,
            False,
            None,
            False,
            ProducerRedactionHits(0, 0, fields),
        )
        with pytest.raises(TelescreenError):
            encode_notification_command(NotificationCommand("event-1", NOW, (invalid_operation,)))


def test_member_redaction_cannot_include_subject_or_body_hits() -> None:
    """Member-directed commands only carry context hit counts."""
    operation: MemberSendOperation = MemberSendOperation(
        "member-1",
        NotificationCategory.ASSIGNED,
        NotificationEvent.ISSUE_ASSIGNED,
        NotificationContext(),
        None,
        ProducerRedactionHits(1, 0),
    )
    with pytest.raises(TelescreenError):
        encode_notification_command(NotificationCommand("event-1", NOW, (operation,)))


def test_member_send_rejects_stray_subject_field() -> None:
    """Strict command parsing rejects fields from another operation kind."""
    payload: str = (
        '{"event_id":"event-1","occurred_at":"2026-10-15T00:00:00Z",'
        '"operations":[{"kind":"member_send","member_id":"member-1",'
        '"category":"assigned","event":"issue_assigned","context":{},'
        '"issue_id":null,"redaction":{"subject":0,"body":0,"context":[]},"subject":1}]}'
    )
    with pytest.raises(TelescreenError):
        decode_notification_command(payload)


def test_command_bounds_reject_65_recipients_and_9_operations() -> None:
    """The command codec rejects oversized recipient and operation collections."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        tuple(f"member:{i}" for i in range(65)),
        "s",
        "b",
        "system:test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        False,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    with pytest.raises(TelescreenError):
        encode_notification_command(NotificationCommand("event-1", NOW, (operation,)))
    small: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:m1",),
        "s",
        "b",
        "system:test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        False,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    with pytest.raises(TelescreenError):
        encode_notification_command(NotificationCommand("event-1", NOW, (small,) * 9))


def test_command_rejects_payload_over_handoff_limit() -> None:
    """The command decoder checks the byte bound before parsing JSON."""
    oversized: str = "x" * (MAX_HANDOFF_BYTES + 1)
    with pytest.raises(TelescreenError) as caught:
        decode_notification_command(oversized)
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
