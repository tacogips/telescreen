"""Contract tests for v1.3 foundation values and settings."""

from dataclasses import dataclass

import pytest

from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.permissions import (
    ALL_PERMISSIONS,
    READ_PERMISSIONS,
    Permission,
    role_permissions,
    scope_permissions,
)
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    UNRESOLVED_STATUSES,
    AlertFireOutcome,
    AlertTriggerKind,
    ApiScope,
    ArchiveKind,
    ErrorCode,
    ExternalIssueKind,
    IntegrationKind,
    IssuePriority,
    IssueStatus,
    NotificationCategory,
    NotificationPreference,
    Role,
    SavedViewVisibility,
    Severity,
    SubscriptionReason,
    SuggestionReason,
    priority_for_severity,
)
from telescreen.issues.transitions import ensure_user_transition


@dataclass(frozen=True)
class FakeEnv(EnvSource):
    """Read a small immutable environment in settings tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return one configured value."""
        return next((value for name, value in self.values if name == key), None)


def env(*values: tuple[str, str]) -> FakeEnv:
    """Prefix setting names with the application environment namespace."""
    return FakeEnv(tuple((f"TELESCREEN_{name}", value) for name, value in values))


def test_v13_vocabulary_and_status_sets_match_design() -> None:
    """New enums and status sets use the accepted persisted strings and ordering."""
    assert tuple(status.value for status in UNRESOLVED_STATUSES) == (
        "unresolved",
        "acknowledged",
        "in_progress",
        "regressed",
        "escalating",
    )
    assert OPEN_STATUSES == (
        IssueStatus.UNRESOLVED,
        IssueStatus.ACKNOWLEDGED,
        IssueStatus.IN_PROGRESS,
        IssueStatus.REGRESSED,
        IssueStatus.PENDING_VERIFICATION,
        IssueStatus.ESCALATING,
        IssueStatus.ARCHIVED,
    )
    assert tuple(item.value for item in IssuePriority) == ("high", "medium", "low")
    assert tuple(item.value for item in ArchiveKind) == (
        "until_escalating",
        "for_duration",
        "until_findings",
        "until_values",
    )
    assert tuple(item.value for item in AlertTriggerKind) == (
        "issue_created",
        "issue_regressed",
        "issue_escalating",
        "frequency",
        "values",
    )
    assert tuple(item.value for item in AlertFireOutcome) == (
        "delivered",
        "blocked",
        "muted",
        "suppressed_interval",
        "failed",
    )
    assert tuple(item.value for item in NotificationCategory) == (
        "assigned",
        "mentions",
        "subscribed",
        "alerts",
        "workflow_tasks",
        "weekly_report",
    )
    assert tuple(item.value for item in NotificationPreference) == ("email", "slack", "off")
    assert tuple(item.value for item in SavedViewVisibility) == ("personal", "shared")
    assert tuple(item.value for item in SubscriptionReason) == (
        "manual",
        "assigned",
        "commented",
        "mentioned",
    )
    assert tuple(item.value for item in ExternalIssueKind) == ("url", "github")
    assert tuple(item.value for item in IntegrationKind) == (
        "slack",
        "email",
        "github_issues",
        "oidc",
    )
    assert tuple(item.value for item in SuggestionReason) == (
        "owner",
        "ownership_rule",
        "policy_team",
    )
    assert tuple(
        code.value
        for code in (
            ErrorCode.INVALID_QUERY,
            ErrorCode.RECIPIENT_OPTED_OUT,
            ErrorCode.MERGED_GROUP,
            ErrorCode.DLP_BLOCKED,
            ErrorCode.INTEGRATION_UNCONFIGURED,
            ErrorCode.DUPLICATE_TRIGGER,
            ErrorCode.LIMIT_EXCEEDED,
        )
    ) == (
        "invalid_query",
        "recipient_opted_out",
        "merged_group",
        "dlp_blocked",
        "integration_unconfigured",
        "duplicate_trigger",
        "limit_exceeded",
    )


def test_v13_transitions_and_priority_defaults() -> None:
    """Escalating and archived have only the accepted user transitions."""
    ensure_user_transition(IssueStatus.ESCALATING, IssueStatus.ACKNOWLEDGED)
    with pytest.raises(TelescreenError) as error:
        ensure_user_transition(IssueStatus.ARCHIVED, IssueStatus.IN_PROGRESS)
    assert error.value.code is ErrorCode.INVALID_TRANSITION
    assert priority_for_severity(Severity.CRITICAL) is IssuePriority.HIGH
    assert priority_for_severity(Severity.HIGH) is IssuePriority.HIGH
    assert priority_for_severity(Severity.MEDIUM) is IssuePriority.MEDIUM


def test_v13_permissions_follow_viewer_responder_admin_matrix() -> None:
    """The new permission grants align with role and token scope sets."""
    viewer: frozenset[Permission] = role_permissions(Role.VIEWER)
    responder: frozenset[Permission] = role_permissions(Role.RESPONDER)
    admin: frozenset[Permission] = role_permissions(Role.ADMIN)
    assert READ_PERMISSIONS == viewer == scope_permissions(frozenset((ApiScope.READ,)))
    assert {
        Permission.VIEWS_READ,
        Permission.VIEWS_WRITE,
        Permission.ISSUES_FOLLOW,
        Permission.PROFILE_SELF,
        Permission.ALERTS_READ,
        Permission.OWNERSHIP_READ,
    } <= viewer
    assert {
        Permission.INTEGRATIONS_READ,
        Permission.ALERTS_WRITE,
        Permission.VIEWS_MANAGE_SHARED,
        Permission.OWNERSHIP_WRITE,
    }.isdisjoint(viewer)
    assert {
        Permission.ALERTS_MUTE,
        Permission.EXTERNAL_ISSUES_WRITE,
        Permission.VIEWS_MANAGE_SHARED,
    } <= responder
    assert ALL_PERMISSIONS - {Permission.INGEST_PUSH} <= admin


def test_v13_settings_defaults_and_validation() -> None:
    """New environment settings default safely and reject invalid values."""
    defaults: TelescreenSettings = load_settings(FakeEnv())
    assert defaults.escalating_min_findings == 10
    assert defaults.escalating_factor == 3
    assert defaults.weekly_report_cron == "0 9 * * 1"
    assert defaults.github_issues_token_ref is None
    assert defaults.github_issues_repos == ()
    assert load_settings(env(("WEEKLY_REPORT_CRON", ""))).weekly_report_cron is None
    configured: TelescreenSettings = load_settings(
        env(("GITHUB_ISSUES_REPOS", "a/b, c/d"), ("GITHUB_ISSUES_TOKEN", "env:GITHUB_TOKEN"))
    )
    assert configured.github_issues_repos == ("a/b", "c/d")
    assert configured.github_issues_token_ref == "env:GITHUB_TOKEN"
    with pytest.raises(ConfigError, match="TELESCREEN_WEEKLY_REPORT_CRON is invalid"):
        load_settings(env(("WEEKLY_REPORT_CRON", "bad")))
    with pytest.raises(ConfigError, match="TELESCREEN_GITHUB_ISSUES_REPOS is invalid"):
        load_settings(env(("GITHUB_ISSUES_REPOS", "bad")))
    with pytest.raises(ConfigError, match="TELESCREEN_GITHUB_ISSUES_REPOS is invalid"):
        load_settings(env(("GITHUB_ISSUES_REPOS", ",".join(f"a/r{index}" for index in range(21)))))
    with pytest.raises(ConfigError, match="TELESCREEN_ESCALATING_MIN_FINDINGS is outside"):
        load_settings(env(("ESCALATING_MIN_FINDINGS", "100001")))
    with pytest.raises(ConfigError, match="TELESCREEN_ESCALATING_FACTOR is outside"):
        load_settings(env(("ESCALATING_FACTOR", "101")))
