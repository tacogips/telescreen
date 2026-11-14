"""Workflow source scanning preserves email recipient references safely."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from telescreen.core.vocabulary import RuleClass, SourceFormat
from telescreen.detection.types import RawFinding
from telescreen.masking.redact import Redactor
from telescreen.workflow.dsl.raw_scan import source_has_raw_values
from telescreen.workflow.dsl.validate import ValidationIssue
from telescreen.workflow.engine.definitions import WorkflowService
from tests.support.samples import Sample, aws_access_key_id
from tests.workflow.v12.test_fan_out import AuditFake, _service

_V1_EMAIL_WORKFLOW: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: email-target
start: notify
steps:
  - id: notify
    type: notify
    channel: email
    to: [email:ops@example.invalid]
    subject: Alert
    body: Review
    next: done
  - {id: done, type: end, result: succeeded}
"""


@pytest.mark.asyncio
async def test_validate_accepts_notification_sets_example() -> None:
    """The documented v2 example passes workflow source scanning and DSL validation."""
    service: WorkflowService = _service(AuditFake(), None)
    source: str = Path("examples/workflows/notification-sets.yaml").read_text()

    assert await service.validate(SourceFormat.YAML, source) == []


@pytest.mark.asyncio
async def test_validate_accepts_v1_email_recipient() -> None:
    """An explicit v1 email recipient remains valid workflow source."""
    service: WorkflowService = _service(AuditFake(), None)

    assert await service.validate(SourceFormat.YAML, _V1_EMAIL_WORKFLOW) == []


@pytest.mark.asyncio
async def test_validate_rejects_email_outside_recipient_reference() -> None:
    """Email addresses in free text remain blocked by the raw-value gate."""
    service: WorkflowService = _service(AuditFake(), None)
    source: str = _V1_EMAIL_WORKFLOW.replace("body: Review", "body: ops@example.invalid")

    issues: Sequence[ValidationIssue] = await service.validate(SourceFormat.YAML, source)

    assert [(issue.path, issue.code) for issue in issues] == [("", "raw_value_detected")]


@pytest.mark.asyncio
async def test_validate_rejects_sample_secret_in_workflow_source() -> None:
    """Runtime-assembled sample secrets remain blocked by the raw-value gate."""
    service: WorkflowService = _service(AuditFake(), None)
    sample: Sample = aws_access_key_id()
    source: str = _V1_EMAIL_WORKFLOW.replace("body: Review", f"body: {sample.value}")

    issues: Sequence[ValidationIssue] = await service.validate(SourceFormat.YAML, source)

    assert [(issue.path, issue.code) for issue in issues] == [("", "raw_value_detected")]


@pytest.mark.asyncio
async def test_validate_rejects_secret_embedded_in_email_recipient() -> None:
    """A non-email secret detection inside an email target is never exempted."""
    service: WorkflowService = _service(AuditFake(), None)
    sample: Sample = aws_access_key_id()
    source: str = _V1_EMAIL_WORKFLOW.replace(
        "email:ops@example.invalid", f"email:{sample.value}@example.invalid"
    )

    issues: Sequence[ValidationIssue] = await service.validate(SourceFormat.YAML, source)

    assert [(issue.path, issue.code) for issue in issues] == [("", "raw_value_detected")]


@pytest.mark.parametrize("terminator", (" ", "\t", "\n", "\r", "\f", "\v", '"', "'", ",", "]", "}"))
def test_email_recipient_followed_by_allowed_terminator_is_exempt(terminator: str) -> None:
    """Email findings ending at a supported syntax boundary are permitted."""
    text: str = f"email:ops@example.invalid{terminator}"

    assert source_has_raw_values(Redactor.default(), text) is False


def test_email_recipient_at_end_of_text_is_exempt() -> None:
    """An email finding ending at EOF is permitted."""
    assert source_has_raw_values(Redactor.default(), "email:ops@example.invalid") is False


def test_non_email_rule_class_is_not_exempt() -> None:
    """Only the EMAIL rule class can use the explicit recipient exemption."""
    sample: Sample = aws_access_key_id()
    text: str = f"email:{sample.value}@example.invalid"
    findings: list[RawFinding] = Redactor.default().detector.scan_text(text)

    assert any(finding.rule_class is RuleClass.CREDENTIAL for finding in findings)
    assert source_has_raw_values(Redactor.default(), text) is True
