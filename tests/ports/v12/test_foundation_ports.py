"""v1.2 additive notification, issue, and identity port contract tests."""

from datetime import UTC, datetime

from telescreen.core.vocabulary import (
    DlpVerdict,
    IssueStatus,
    NotificationChannel,
    NotificationEvent,
    RuleClass,
    Severity,
    SignInProvisioning,
)
from telescreen.ports.identity import MemberAuth, SignInResult, VerifiedToken
from telescreen.ports.issues import IssueSnapshot
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
)
from telescreen.ports.templates import TemplateInfo


def test_notification_and_issue_dtos_keep_legacy_construction() -> None:
    """New request, receipt, context and excerpt fields are safely defaulted."""
    request: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL, ("email:person@example.test",), "subject", "body", "actor"
    )
    receipt: NotificationReceipt = NotificationReceipt((), DlpVerdict.PASS, False)
    issue: IssueSnapshot = IssueSnapshot(
        "issue",
        1,
        "TS-1",
        "display title",
        IssueStatus.UNRESOLVED,
        "source",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        "masked locator",
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        None,
    )
    context: NotificationContext = NotificationContext()
    assert request.event is None
    assert request.inline is False
    assert request.record_unreachable is False
    assert receipt.skipped == ()
    assert issue.masked_excerpt is None
    assert context.issue_risk_score is None and context.task_tier is None


def test_identity_subject_and_claims_are_hidden_from_representations() -> None:
    """Sensitive OIDC subject and serialized claims do not appear in DTO reprs."""
    token: VerifiedToken = VerifiedToken("https://idp.example.test", "private-sub", "{}")
    assert "private-sub" not in repr(token)
    assert "{}" not in repr(token)
    member: MemberAuth = MemberAuth("member-id", "handle", "Display Name", None, True)
    result: SignInResult = SignInResult(member, SignInProvisioning.EXISTING, False)
    assert result.provisioning is SignInProvisioning.EXISTING
    template: TemplateInfo = TemplateInfo(
        "system.workflow_notify", NotificationEvent.WORKFLOW_NOTIFY, 0
    )
    assert template.active_version == 0
