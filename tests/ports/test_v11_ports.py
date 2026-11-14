"""Tests for additive v1.1 port DTO fields and protocol availability."""

from datetime import UTC, datetime

from telescreen.core.vocabulary import (
    CollectorKind,
    DetectorKind,
    IdentityKind,
    IssueStatus,
    PlacementKind,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.ports.collectors import (
    OwnerIdentity,
    PlacementRef,
    ResourceContent,
    ResourceRef,
    Segment,
)
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.findings import MaskedFinding, ResourceScan
from telescreen.ports.issues import (
    IssueRemediation,
    IssueSnapshot,
    ResourceIssueReader,
)
from telescreen.ports.policy import PolicyDecider, RiskScorer


def test_v1_positional_construction_retains_appended_defaults() -> None:
    """Old positional DTO constructors remain valid with v1.1 defaults."""
    now: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    content: ResourceContent = ResourceContent(
        "slack",
        None,
        (Segment("line:1", "masked text"),),
        True,
        False,
        "raw-locator",
        "display-title",
        ResourceRef(CollectorKind.SLACK, "{}", "raw-locator"),
    )
    finding: MaskedFinding = MaskedFinding(
        "rule",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        DetectorKind.RULE,
        0.9,
        "masked",
        "line:1",
        "fingerprint",
        "key-id",
    )
    scan: ResourceScan = ResourceScan(
        "slack",
        "resource-key",
        "locator",
        "title",
        None,
        None,
        None,
        (finding,),
        True,
        False,
        now,
    )
    snapshot: IssueSnapshot = IssueSnapshot(
        "issue",
        1,
        "TELE-1",
        "title",
        IssueStatus.UNRESOLVED,
        "slack",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        "locator",
        1,
        now,
        now,
        None,
        None,
    )
    assert content.exposure is None and content.owner is None and content.placement == ()
    assert content.modified_at is None and content.coverage is None and not content.removed
    assert finding.fingerprint_key_version == 1
    assert scan.exposure is None and scan.owner_ref is None and scan.placement_keys == ()
    assert scan.modified_at is None and not scan.removed
    assert snapshot.assignee_ref is None and snapshot.risk_score == 0
    assert snapshot.visibility is Visibility.UNKNOWN


def test_new_ports_are_importable_and_raw_identity_values_are_hidden() -> None:
    """New port contracts are independently importable and identity reprs are safe."""
    owner: OwnerIdentity = OwnerIdentity(IdentityKind.GITHUB_LOGIN, "private-login")
    placement: PlacementRef = PlacementRef(PlacementKind.GITHUB_REPO, "private/repo")
    assert "private-login" not in repr(owner)
    assert "private/repo" not in repr(placement)
    assert PolicyDecider is not None
    assert RiskScorer is not None
    assert OwnerDirectory is not None
    assert ResourceIssueReader is not None
    assert IssueRemediation is not None
