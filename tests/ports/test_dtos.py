"""Contract tests for frozen port DTOs and their pure helpers."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    ActorKind,
    CollectorKind,
    DetectorKind,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    NotificationChannel,
    RuleClass,
    Severity,
    TriggerMode,
)
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    CustomSelector,
    DriveSelector,
    EventCheck,
    EventCheckKind,
    EventResources,
    GitHubSelector,
    InboundWebhook,
    PoliceSelector,
    RateLimited,
    RescanKind,
    RescanResult,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
    SlackSelector,
    WatchState,
)
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.findings import (
    ExternalFinding,
    ExternalScan,
    MaskedFinding,
    RecordOutcome,
    ResourceScan,
)
from telescreen.ports.issues import (
    IssueEvent,
    IssueHistoryEntry,
    IssueSnapshot,
    OpenResourceRef,
)
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    RuleClassCount,
    ShardEntry,
    ShardEntryKind,
    ShardFailure,
    ShardSpec,
    ShardSummary,
    SubCorpus,
)
from telescreen.ports.workflow import (
    ClaimedTask,
    StepSignal,
    StepSignalKind,
    TaskResult,
    TaskResultKind,
    TaskSpec,
)
from tests.support.samples import slack_token

NOW: datetime = datetime(2026, 10, 8, tzinfo=UTC)

type PortDto = (
    AuditEntry
    | ResolvedRecipients
    | AssigneeIds
    | MaskedFinding
    | ResourceScan
    | ExternalFinding
    | ExternalScan
    | RecordOutcome
    | IssueHistoryEntry
    | IssueSnapshot
    | OpenResourceRef
    | IssueEvent
    | InboundWebhook
    | EventCheck
    | CollectorSecrets
    | ResourceRef
    | Segment
    | ResourceContent
    | CollectorCursor
    | ChangePage
    | EventResources
    | SlackSelector
    | DriveSelector
    | GitHubSelector
    | CustomSelector
    | PoliceSelector
    | CollectorConfig
    | WatchState
    | RescanResult
    | PartitionRequest
    | ShardSpec
    | SubCorpus
    | PartitionPage
    | ShardEntry
    | RuleClassCount
    | ShardSummary
    | ShardFailure
    | NotificationRequest
    | NotificationReceipt
    | PoliceTaskRequest
    | StepSignal
    | TaskSpec
    | ClaimedTask
    | TaskResult
)


def assert_frozen[T](value: T, attribute: str) -> None:
    """Assert that assigning to a DTO field raises the dataclass error."""
    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, None)


def test_every_port_dto_constructs_and_is_frozen() -> None:
    """Construct each public DTO and check its frozen contract."""
    token: str = slack_token().value
    finding: MaskedFinding = MaskedFinding(
        "credential",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        DetectorKind.RULE,
        0.9,
        "masked",
        "line:1",
        "fingerprint",
        "key-1",
    )
    resource_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, "{}", token)
    content: ResourceContent = ResourceContent(
        "slack",
        "config",
        (Segment("message", token),),
        True,
        False,
        token,
        token,
        resource_ref,
    )
    external_finding: ExternalFinding = ExternalFinding(
        "masked-locator",
        "credential",
        RuleClass.CREDENTIAL,
        "masked",
        "line:1",
        0.8,
        "external-fingerprint",
    )
    resource_scan: ResourceScan = ResourceScan(
        "slack",
        "resource",
        "display",
        "title",
        "config",
        "scan",
        "{}",
        (finding,),
        True,
        False,
        NOW,
    )
    external_scan: ExternalScan = ExternalScan(
        "custom",
        None,
        None,
        DetectorKind.POLICE,
        (external_finding,),
        ("masked-locator",),
        False,
        NOW,
    )
    webhook: InboundWebhook = InboundWebhook(
        CollectorKind.GITHUB, "POST", NOW, (("X-Hub-Signature-256", token),), token.encode()
    )
    selector: SlackSelector = SlackSelector(("channel",), True)
    drive_selector: DriveSelector = DriveSelector(("drive",), ("folder",), ("text/plain",))
    github_selector: GitHubSelector = GitHubSelector("owner", ("repo",), ("main",), ("*.md",))
    custom_selector: CustomSelector = CustomSelector(json_null())
    police_selector: PoliceSelector = PoliceSelector(("label",), json_null())
    config: CollectorConfig = CollectorConfig(
        "id",
        "name",
        CollectorKind.SLACK,
        TriggerMode.BOTH,
        None,
        None,
        4,
        selector,
        "env:SLACK_TOKEN",
        True,
    )
    values: tuple[PortDto, ...] = (
        AuditEntry(
            ActorKind.SYSTEM, "system", "issue.opened", "issue", "id", "summary", json_null()
        ),
        ResolvedRecipients(("a@example.invalid",), (), (), ("member",)),
        AssigneeIds("member", None),
        finding,
        resource_scan,
        external_finding,
        external_scan,
        RecordOutcome.empty(),
        IssueHistoryEntry("issue", IssueHistoryKind.COMMENTED, "member", NOW),
        IssueSnapshot(
            "id",
            1,
            "TELE-1",
            "title",
            IssueStatus.UNRESOLVED,
            "slack",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            "masked",
            1,
            NOW,
            NOW,
            None,
            None,
        ),
        OpenResourceRef("issue", "resource", "{}", False),
        IssueEvent(
            IssueEventKind.ISSUE_CREATED,
            "event",
            "issue",
            "slack",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            NOW,
        ),
        webhook,
        EventCheck(EventCheckKind.VALID),
        CollectorSecrets(token, token.encode()),
        resource_ref,
        Segment("message", token),
        content,
        CollectorCursor("cursor"),
        ChangePage((resource_ref,), (), CollectorCursor("next"), False, False),
        EventResources((resource_ref,), (), True),
        selector,
        drive_selector,
        github_selector,
        custom_selector,
        police_selector,
        config,
        WatchState("channel", "resource", NOW),
        RescanResult(RescanKind.CLEAN),
        PartitionRequest("corpus", None, selector),
        ShardSpec("shard", json_null(), 1),
        SubCorpus("child"),
        PartitionPage((), None, (SubCorpus("child"),)),
        ShardEntry(ShardEntryKind.FETCHED, resource_ref, content),
        RuleClassCount(RuleClass.CREDENTIAL, 1),
        ShardSummary.empty(),
        ShardFailure(ErrorCode.RATE_LIMITED, 1.5),
        NotificationRequest(
            NotificationChannel.EMAIL, ("a@example.invalid",), "subject", "body", "actor"
        ),
        NotificationReceipt(("notification",), DlpVerdict.PASS, True),
        PoliceTaskRequest("verify", (), json_null(), NOW),
        StepSignal(StepSignalKind.HUMAN_COMPLETED),
        TaskSpec(EngineTaskKind.ADVANCE_STEP, "ref", json_null(), NOW, "key"),
        ClaimedTask("id", EngineTaskKind.ADVANCE_STEP, "ref", json_null(), 1, 5, "key"),
        TaskResult.done(),
    )
    for value in values:
        assert_frozen(value, "frozen_probe")
    assert webhook.header("x-hub-signature-256") == token
    assert webhook.header("X-Missing") is None
    assert token not in repr(webhook)
    assert token not in repr(CollectorSecrets(token, token.encode()))
    assert token not in repr(resource_ref)
    assert token not in repr(content)
    assert token not in repr(content.segments[0])
    limited: RateLimited = RateLimited(2.0)
    assert limited.retry_after_seconds == 2.0
    assert str(ResourceGoneError("gone")) == "gone"
    result: RescanResult = RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED)
    assert result.error_code is ErrorCode.FETCH_FAILED


def test_record_outcome_plus_sums_counts_and_deduplicates_ids() -> None:
    """Combine scan outcomes without losing issue order or count fields."""
    first: RecordOutcome = RecordOutcome(2, 1, 0, 0, ("i1", "i2"))
    second: RecordOutcome = RecordOutcome(3, 0, 1, 1, ("i2", "i3"), True)
    combined: RecordOutcome = first.plus(second)
    assert combined == RecordOutcome(5, 1, 1, 1, ("i1", "i2", "i3"), True)


def test_shard_summary_plus_sums_counts_by_rule_class() -> None:
    """Combine sweep summaries and merge per-class finding counts."""
    first: ShardSummary = ShardSummary(
        2, 0, 0, 12, 2, 1, 0, (RuleClassCount(RuleClass.CREDENTIAL, 2),)
    )
    second: ShardSummary = ShardSummary(
        1,
        1,
        1,
        5,
        3,
        2,
        1,
        (RuleClassCount(RuleClass.CREDENTIAL, 1), RuleClassCount(RuleClass.EMAIL, 2)),
    )
    expected: ShardSummary = ShardSummary(
        3,
        1,
        1,
        17,
        5,
        3,
        1,
        (RuleClassCount(RuleClass.CREDENTIAL, 3), RuleClassCount(RuleClass.EMAIL, 2)),
    )
    assert first.plus(second) == expected
    assert ShardSummary.empty().plus(first) == first


def test_task_result_retry_sets_disposition_and_fields() -> None:
    """Build a retry result with its schedule and safe error code."""
    result: TaskResult = TaskResult.retry(NOW, ErrorCode.RATE_LIMITED)
    assert result.kind is TaskResultKind.RETRY
    assert result.available_at == NOW
    assert result.error_code is ErrorCode.RATE_LIMITED
