"""Escalation policy source scanning preserves explicit email recipients safely."""

from pathlib import Path

import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, parse_json
from telescreen.core.vocabulary import ErrorCode, SourceFormat
from telescreen.db.models import EscalationPolicyRow
from telescreen.masking.redact import Redactor
from telescreen.workflow.dsl.escalation import EscalationPolicySpec, load_escalation_policy
from telescreen.workflow.escalation.service import EscalationPolicyService
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, aws_access_key_id
from tests.workflow.v12.test_fan_out import NOW, AuditFake

_INLINE_EMAIL_POLICY: str = """name: inline-email
tiers:
  - after: PT1H
    assign_to: team:secops
    targets:
      - recipients: [email:ops@example.invalid]
        channels: [email]
"""


def _service() -> EscalationPolicyService:
    """Build the production service with an in-memory audit fake."""
    return EscalationPolicyService(AuditFake(), FakeClock(NOW), Redactor.default())


@pytest.mark.asyncio
async def test_create_accepts_each_shipped_escalation_policy() -> None:
    """Both example policies pass the raw scan and create through the real service."""
    source: str = Path("examples/workflows/escalation-policies.json").read_text()
    parsed: JsonValue = parse_json(source)
    policies: tuple[JsonValue, ...] | None = parsed.as_list()
    assert policies is not None
    harness: SqliteHarness = await make_sqlite_harness()
    service: EscalationPolicyService = _service()
    created_names: list[str] = []
    try:
        for policy in policies:
            policy_text: str = dump_json(policy)
            async with harness.sessions.begin() as session:
                row: EscalationPolicyRow = await service.create(
                    session, policy_text, SourceFormat.JSON, "member:admin"
                )
                created_names.append(row.name)
    finally:
        await harness.close()

    assert created_names == ["secops-standard", "dev-remediation"]


def test_inline_email_tier_target_is_accepted() -> None:
    """An inline email target passes source scanning and escalation parsing."""
    service: EscalationPolicyService = _service()

    service._reject_sensitive_text(_INLINE_EMAIL_POLICY)
    policy: EscalationPolicySpec = load_escalation_policy(_INLINE_EMAIL_POLICY, SourceFormat.YAML)

    assert policy.tiers[0].targets[0].recipients == ("email:ops@example.invalid",)


def test_free_text_email_is_rejected() -> None:
    """An email outside an email: recipient reference remains rejected."""
    service: EscalationPolicyService = _service()
    source: str = _INLINE_EMAIL_POLICY.replace("name: inline-email", "name: ops@example.invalid")

    with pytest.raises(TelescreenError) as caught:
        service._reject_sensitive_text(source)

    assert caught.value.code is ErrorCode.RAW_VALUE_DETECTED


def test_body_secret_is_rejected() -> None:
    """Runtime-assembled secret values remain blocked in policy source."""
    service: EscalationPolicyService = _service()
    sample: Sample = aws_access_key_id()
    source: str = _INLINE_EMAIL_POLICY.replace(
        "name: inline-email", f"name: inline-email\nbody: {sample.value}"
    )

    with pytest.raises(TelescreenError) as caught:
        service._reject_sensitive_text(source)

    assert caught.value.code is ErrorCode.RAW_VALUE_DETECTED


def test_secret_inside_email_recipient_is_rejected() -> None:
    """A credential finding inside an email reference is not exempted."""
    service: EscalationPolicyService = _service()
    sample: Sample = aws_access_key_id()
    source: str = _INLINE_EMAIL_POLICY.replace(
        "email:ops@example.invalid", f"email:{sample.value}@example.invalid"
    )

    with pytest.raises(TelescreenError) as caught:
        service._reject_sensitive_text(source)

    assert caught.value.code is ErrorCode.RAW_VALUE_DETECTED


@pytest.mark.asyncio
async def test_create_accepts_v2_inline_email_targets() -> None:
    """The shipped service create path stores a valid inline email tier target."""
    service: EscalationPolicyService = _service()
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions.begin() as session:
            row: EscalationPolicyRow = await service.create(
                session, _INLINE_EMAIL_POLICY, SourceFormat.YAML, "member:admin"
            )
        assert row.name == "inline-email"
    finally:
        await harness.close()
