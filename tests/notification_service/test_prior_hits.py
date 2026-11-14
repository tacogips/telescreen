"""Notification-side folding of producer redaction counts."""

import pytest

from telescreen.core.vocabulary import (
    DlpPolicy,
    DlpVerdict,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.detection.rule_detector import RuleDetector
from telescreen.masking.redact import Redactor
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage
from telescreen.notifications.prior_hits import prior_hits_for
from telescreen.notifications.templates.model import TemplateTexts
from telescreen.ports.notification_types import (
    ContextFieldHits,
    NotificationContext,
    ProducerRedactionHits,
)
from telescreen.ports.notifications import NotificationRequest


@pytest.mark.parametrize("policy", (DlpPolicy.MASK, DlpPolicy.BLOCK))
async def test_prior_hits_are_folded_for_inline_messages(policy: DlpPolicy) -> None:
    """Producer hits remain observable after their sensitive values were masked."""
    gate: DlpGate = DlpGate(RuleDetector(), Redactor.default(), policy)
    result: CensoredMessage = await gate.inspect_redacted(
        OutboundMessage("Notice", "Body"), prior_hits=2
    )
    assert result.hit_count == 2
    assert result.verdict is (DlpVerdict.MASKED if policy is DlpPolicy.MASK else DlpVerdict.BLOCKED)
    if policy is DlpPolicy.BLOCK:
        assert result.subject == result.body == "[BLOCKED BY DLP]"


@pytest.mark.parametrize("policy", (DlpPolicy.MASK, DlpPolicy.BLOCK))
async def test_prior_hits_are_folded_for_unredacted_messages(policy: DlpPolicy) -> None:
    """Prior content hits produce the same verdict on the direct inspection path."""
    gate: DlpGate = DlpGate(RuleDetector(), Redactor.default(), policy)
    result: CensoredMessage = await gate.inspect(OutboundMessage("Notice", "Body"), prior_hits=3)
    assert result.hit_count == 3
    assert result.verdict is (DlpVerdict.MASKED if policy is DlpPolicy.MASK else DlpVerdict.BLOCKED)


async def test_zero_prior_hits_preserve_clean_message_bytes() -> None:
    """The legacy path returns unchanged clean text when there are no prior hits."""
    gate: DlpGate = DlpGate(RuleDetector(), Redactor.default(), DlpPolicy.MASK)
    original: OutboundMessage = OutboundMessage("Notice", "Body")
    result: CensoredMessage = await gate.inspect_redacted(original, prior_hits=0)
    assert (result.subject, result.body, result.verdict, result.hit_count) == (
        "Notice",
        "Body",
        DlpVerdict.PASS,
        0,
    )


def test_prior_hits_select_subject_body_and_rendered_context_fields() -> None:
    """Count only text fields used by each v1, inline, or template body path."""
    hits: ProducerRedactionHits = ProducerRedactionHits(1, 2, (ContextFieldHits("issue_title", 1),))
    v1: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("email:member@example.test",),
        "",
        "",
        "system:test",
        prior_hits=hits,
    )
    inline: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("email:member@example.test",),
        "",
        "",
        "system:test",
        event=NotificationEvent.ISSUE_ASSIGNED,
        inline=True,
        prior_hits=hits,
    )
    templated: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("email:member@example.test",),
        "",
        "",
        "system:test",
        event=NotificationEvent.ISSUE_ASSIGNED,
        context=NotificationContext(issue_title="masked"),
        prior_hits=hits,
    )
    texts: TemplateTexts = TemplateTexts("{{ issue.title }} {{ issue.title }}", "")
    unused: TemplateTexts = TemplateTexts("Notice", "")
    assert prior_hits_for(v1, None) == 3
    assert prior_hits_for(inline, None) == 3
    assert prior_hits_for(templated, texts) == 2
    assert prior_hits_for(templated, unused) == 0
