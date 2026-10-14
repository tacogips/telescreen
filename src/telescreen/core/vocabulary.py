"""Closed vocabulary shared across Telescreen feature packages."""

from enum import StrEnum


class RuleClass(StrEnum):
    """A supported class of sensitive data finding."""

    CREDENTIAL = "credential"
    TOKEN = "token"
    PRIVATE_KEY = "private_key"
    PASSWORD = "password"
    PAYMENT_CARD = "payment_card"
    NATIONAL_ID = "national_id"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"


class Severity(StrEnum):
    """Finding severity."""

    HIGH = "high"
    MEDIUM = "medium"
    CRITICAL = "critical"


class IssuePriority(StrEnum):
    """Triage priority independent from detection severity."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ArchiveKind(StrEnum):
    """Bounded conditions that can unarchive an issue."""

    UNTIL_ESCALATING = "until_escalating"
    FOR_DURATION = "for_duration"
    UNTIL_FINDINGS = "until_findings"
    UNTIL_VALUES = "until_values"


class AlertTriggerKind(StrEnum):
    """Conditions that can fire an alert rule."""

    ISSUE_CREATED = "issue_created"
    ISSUE_REGRESSED = "issue_regressed"
    ISSUE_ESCALATING = "issue_escalating"
    FREQUENCY = "frequency"
    VALUES = "values"


class AlertFireOutcome(StrEnum):
    """Outcome recorded for an evaluated alert rule."""

    DELIVERED = "delivered"
    BLOCKED = "blocked"
    MUTED = "muted"
    SUPPRESSED_INTERVAL = "suppressed_interval"
    FAILED = "failed"


class NotificationCategory(StrEnum):
    """Member-controlled notification categories."""

    ASSIGNED = "assigned"
    MENTIONS = "mentions"
    SUBSCRIBED = "subscribed"
    ALERTS = "alerts"
    WORKFLOW_TASKS = "workflow_tasks"
    WEEKLY_REPORT = "weekly_report"


class NotificationPreference(StrEnum):
    """Available member notification delivery preferences."""

    EMAIL = "email"
    SLACK = "slack"
    OFF = "off"


class SavedViewVisibility(StrEnum):
    """Visibility of a saved issue view."""

    PERSONAL = "personal"
    SHARED = "shared"


class SubscriptionReason(StrEnum):
    """Reason a member follows an issue."""

    MANUAL = "manual"
    ASSIGNED = "assigned"
    COMMENTED = "commented"
    MENTIONED = "mentioned"


class ExternalIssueKind(StrEnum):
    """Supported external issue link kinds."""

    URL = "url"
    GITHUB = "github"


class IntegrationKind(StrEnum):
    """Integration status categories exposed by the organization API."""

    SLACK = "slack"
    EMAIL = "email"
    GITHUB_ISSUES = "github_issues"
    OIDC = "oidc"


class SuggestionReason(StrEnum):
    """Reason an assignee was suggested."""

    OWNER = "owner"
    OWNERSHIP_RULE = "ownership_rule"
    POLICY_TEAM = "policy_team"


class ActorDisplayKind(StrEnum):
    """Resolved actor reference category for user-facing activity."""

    MEMBER = "member"
    TEAM = "team"
    TOKEN = "token"
    SYSTEM = "system"
    UNKNOWN = "unknown"


def priority_for_severity(s: Severity) -> IssuePriority:
    """Return the default issue priority for a detected severity."""
    if s in (Severity.CRITICAL, Severity.HIGH):
        return IssuePriority.HIGH
    return IssuePriority.MEDIUM


def severity_rank(s: Severity) -> int:
    """Return the total ordering rank for an issue severity."""
    if s is Severity.CRITICAL:
        return 3
    if s is Severity.HIGH:
        return 2
    return 1


class DetectorKind(StrEnum):
    """Detection backend names."""

    RULE = "rule"
    ML = "ml"
    MANAGED_LLM = "managed_llm"
    LOCAL_LLM = "local_llm"
    POLICE = "police"


class DetectorMode(StrEnum):
    """How configured detectors combine their results."""

    UNION = "union"
    CASCADE = "cascade"


class DlpPolicy(StrEnum):
    """Action taken when outbound content contains sensitive data."""

    MASK = "mask"
    BLOCK = "block"


class ManagedLlmProvider(StrEnum):
    """Supported managed language-model providers."""

    CLAUDE = "claude"
    CODEX = "codex"


class IssueStatus(StrEnum):
    """Issue lifecycle state."""

    UNRESOLVED = "unresolved"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    IGNORED = "ignored"
    REGRESSED = "regressed"
    ALLOWED = "allowed"
    PENDING_VERIFICATION = "pending_verification"
    ARCHIVED = "archived"
    ESCALATING = "escalating"


class IssueResolution(StrEnum):
    """How an issue was resolved."""

    MANUAL = "manual"
    AUTO = "auto"
    VERIFIED_REMOVED = "verified_removed"
    RESOURCE_DELETED = "resource_deleted"
    EXPOSURE_REDUCED = "exposure_reduced"
    POLICY_CHANGED = "policy_changed"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_RISK = "accepted_risk"
    MANUAL_UNVERIFIED = "manual_unverified"


class IssueHistoryKind(StrEnum):
    """Kinds of issue history entry."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    COMMENTED = "commented"
    REGRESSED = "regressed"
    AUTO_RESOLVED = "auto_resolved"
    WORKFLOW_STARTED = "workflow_started"
    EVIDENCE_ADDED = "evidence_added"
    SLA_BREACHED = "sla_breached"
    EXPOSURE_CHANGED = "exposure_changed"
    POLICY_REEVALUATED = "policy_reevaluated"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_INCONCLUSIVE = "verification_inconclusive"
    EXCEPTION_REQUESTED = "exception_requested"
    EXCEPTION_APPROVED = "exception_approved"
    EXCEPTION_REJECTED = "exception_rejected"
    EXCEPTION_REVOKED = "exception_revoked"
    EXCEPTION_EXPIRED = "exception_expired"
    ARCHIVED = "archived"
    UNARCHIVED = "unarchived"
    ESCALATED = "escalated"
    PRIORITY_CHANGED = "priority_changed"
    REVIEWED = "reviewed"
    MERGED = "merged"
    UNMERGED = "unmerged"
    COMMENT_EDITED = "comment_edited"
    COMMENT_DELETED = "comment_deleted"
    EXTERNAL_ISSUE_LINKED = "external_issue_linked"
    EXTERNAL_ISSUE_UNLINKED = "external_issue_unlinked"


class IssueEventKind(StrEnum):
    """Issue events that trigger workflows."""

    ISSUE_CREATED = "issue_created"
    ISSUE_REGRESSED = "issue_regressed"
    ISSUE_ESCALATING = "issue_escalating"


class CollectorKind(StrEnum):
    """Built-in and custom collector kinds."""

    SLACK = "slack"
    GDRIVE = "gdrive"
    GDOCS = "gdocs"
    GSHEETS = "gsheets"
    GITHUB = "github"
    CUSTOM = "custom"
    POLICE = "police"


class TriggerMode(StrEnum):
    """Configured collector trigger modes."""

    EVENT = "event"
    SCHEDULE = "schedule"
    BOTH = "both"


class ScanTrigger(StrEnum):
    """Origin of a scan job."""

    EVENT = "event"
    SCHEDULE = "schedule"
    PUSH = "push"
    MANUAL = "manual"
    WORKFLOW = "workflow"
    SWEEP = "sweep"


class ScanJobStatus(StrEnum):
    """Scan job lifecycle state."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class ActorKind(StrEnum):
    """Identity category for an audited actor."""

    MEMBER = "member"
    TOKEN = "token"
    SYSTEM = "system"
    POLICE = "police"
    COLLECTOR = "collector"


class ApiScope(StrEnum):
    """Bearer token authorization scope."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    INGEST = "ingest"


class NotificationEvent(StrEnum):
    """System events that may produce notifications."""

    WORKFLOW_NOTIFY = "workflow_notify"
    HUMAN_TASK_ASSIGNED = "human_task_assigned"
    OWNER_REVIEW_ASSIGNED = "owner_review_assigned"
    SLA_ESCALATED = "sla_escalated"
    OWNER_DIGEST = "owner_digest"
    EXCEPTION_REQUESTED = "exception_requested"
    EXCEPTION_EXPIRED = "exception_expired"
    VERIFICATION_INCONCLUSIVE = "verification_inconclusive"
    COLLECTOR_HEALTH_ALERT = "collector_health_alert"
    POLICE_HEALTH_ALERT = "police_health_alert"
    ALERT_FIRED = "alert_fired"
    ISSUE_ASSIGNED = "issue_assigned"
    ISSUE_MENTIONED = "issue_mentioned"
    ISSUE_ACTIVITY = "issue_activity"
    WEEKLY_REPORT = "weekly_report"
    DATA_OVERDUE = "data_overdue"
    INTEGRATION_TEST = "integration_test"


class Role(StrEnum):
    """Human access role assigned to a member."""

    ADMIN = "admin"
    RESPONDER = "responder"
    VIEWER = "viewer"


class RoleChangeSource(StrEnum):
    """Source responsible for a member role change."""

    MANUAL = "manual"
    IDP = "idp"
    CLI = "cli"


class MembershipOrigin(StrEnum):
    """Origin of a member's team membership."""

    MANUAL = "manual"
    IDP = "idp"


class OidcProvisioning(StrEnum):
    """Provisioning policy for an OIDC identity."""

    LINK = "link"
    CREATE = "create"


class SignInProvisioning(StrEnum):
    """Outcome of resolving a member during OIDC sign-in."""

    EXISTING = "existing"
    LINKED = "linked"
    CREATED = "created"


class PrincipalKind(StrEnum):
    """Authentication mechanism for an API principal."""

    TOKEN = "token"
    OIDC = "oidc"


class ExternalAuthKind(StrEnum):
    """Authentication type for external API requests."""

    PUBLIC = "public"
    PROVIDER_SIGNATURE = "provider_signature"
    POLICE_CONTRACT = "police_contract"
    OIDC_SIGN_IN = "oidc_sign_in"


class SourceFormat(StrEnum):
    """Supported workflow definition formats."""

    YAML = "yaml"
    JSON = "json"


class StepType(StrEnum):
    """Workflow definition node types."""

    DETECT = "detect"
    AI_AGENT = "ai_agent"
    CONDITION = "condition"
    HUMAN_TASK = "human_task"
    NOTIFY = "notify"
    EVIDENCE_UPLOAD = "evidence_upload"
    POLICE_VERIFY = "police_verify"
    WAIT = "wait"
    SEND_BACK = "send_back"
    PARALLEL = "parallel"
    JOIN = "join"
    SUB_WORKFLOW = "sub_workflow"
    MAP = "map"
    END = "end"


class RunStatus(StrEnum):
    """Workflow run state."""

    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunTrigger(StrEnum):
    """Workflow run trigger."""

    ISSUE_CREATED = "issue_created"
    ISSUE_REGRESSED = "issue_regressed"
    MANUAL = "manual"
    SUB_WORKFLOW = "sub_workflow"
    ISSUE_ESCALATING = "issue_escalating"
    ALERT_RULE = "alert_rule"


class StepRunStatus(StrEnum):
    """Workflow step run state."""

    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransitionKind(StrEnum):
    """Workflow transition kind."""

    START = "start"
    NEXT = "next"
    BRANCH = "branch"
    SEND_BACK = "send_back"
    FORK = "fork"
    JOIN = "join"
    TIMEOUT = "timeout"
    ERROR = "error"


class EngineTaskKind(StrEnum):
    """Durable engine queue task kind."""

    ADVANCE_STEP = "advance_step"
    TIMER_FIRED = "timer_fired"
    SLA_CHECK = "sla_check"
    POLICE_TIMEOUT = "police_timeout"
    COLLECTOR_SCAN = "collector_scan"
    POLICE_PUSH_DELIVERY = "police_push_delivery"
    NOTIFICATION_DELIVERY = "notification_delivery"
    CHILD_RUN_FINISHED = "child_run_finished"
    COLLECTOR_WATCH_RENEW = "collector_watch_renew"
    MAP_PARTITION = "map_partition"
    MAP_DISPATCH = "map_dispatch"
    MAP_SHARD = "map_shard"
    MAP_REDUCE = "map_reduce"
    VERIFY_RESCAN = "verify_rescan"
    POLICY_REEVALUATE = "policy_reevaluate"
    OWNER_DIGEST = "owner_digest"
    WEEKLY_REPORT = "weekly_report"
    DATA_OVERDUE_CHECK = "data_overdue_check"
    INVENTORY_DELIVERY = "inventory_delivery"
    INVENTORY_AUDIT_DELIVERY = "inventory_audit_delivery"
    RETENTION_DELIVERY = "retention_delivery"


class InventoryMode(StrEnum):
    """How a collector contributes resources to the data inventory."""

    SENSITIVE_ONLY = "sensitive_only"
    ALL = "all"
    OFF = "off"


class DataAssetOrigin(StrEnum):
    """How a data asset entered the inventory."""

    DISCOVERED = "discovered"
    REGISTERED = "registered"


class DataAssetState(StrEnum):
    """Known lifecycle state of an inventoried data asset."""

    PRESENT = "present"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class DataAssetKind(StrEnum):
    """Broad shape of a data asset."""

    FILE = "file"
    TABLE = "table"
    LIST = "list"
    CHANNEL = "channel"
    OBJECT = "object"
    FOLDER = "folder"
    OTHER = "other"


class DataEventKind(StrEnum):
    """Auditable event kinds in the data asset lifecycle."""

    DISCOVERED = "discovered"
    REGISTERED = "registered"
    ITEMS_CHANGED = "items_changed"
    VALUES_CHANGED = "values_changed"
    DELETED = "deleted"
    RESTORED = "restored"
    METADATA_CHANGED = "metadata_changed"
    MANUAL_CHANGE = "manual_change"
    TRACKING_STOPPED = "tracking_stopped"
    TRACKING_RESUMED = "tracking_resumed"


class DataItemKind(StrEnum):
    """Tracked list-item or finding-value category."""

    ITEM = "item"
    VALUE = "value"


class DataFieldType(StrEnum):
    """Supported data asset metadata field types."""

    TEXT = "text"
    LONG_TEXT = "long_text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    REFERENCE = "reference"
    URL = "url"


class DataFieldRole(StrEnum):
    """Semantic role for commonly used metadata fields."""

    PURPOSE = "purpose"
    OWNER = "owner"
    DELETION_DUE = "deletion_due"


class DataAssetTab(StrEnum):
    """Data inventory list tabs."""

    ALL = "all"
    PRESENT = "present"
    DELETED = "deleted"
    INCOMPLETE = "incomplete"
    OVERDUE = "overdue"


class EngineTaskStatus(StrEnum):
    """Durable engine task state."""

    READY = "ready"
    LEASED = "leased"
    DONE = "done"
    CANCELLED = "cancelled"
    DEAD = "dead"


class HumanTaskKind(StrEnum):
    """Human task type."""

    APPROVAL = "approval"
    EVIDENCE = "evidence"
    OWNER_REVIEW = "owner_review"


class HumanTaskStatus(StrEnum):
    """Human task state."""

    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NotificationChannel(StrEnum):
    """Supported notification transport."""

    SLACK = "slack"
    EMAIL = "email"


class DlpVerdict(StrEnum):
    """Outbound DLP decision."""

    PASS = "pass"
    MASKED = "masked"
    BLOCKED = "blocked"


class NotificationStatus(StrEnum):
    """Notification delivery state."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class EvidenceVerdict(StrEnum):
    """Evidence scanner verdict."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PoliceMode(StrEnum):
    """Police worker transport mode."""

    PULL = "pull"
    PUSH = "push"


class PoliceWorkerStatus(StrEnum):
    """Police worker state."""

    ACTIVE = "active"
    DISABLED = "disabled"


class PoliceTaskStatus(StrEnum):
    """Police task state."""

    QUEUED = "queued"
    LEASED = "leased"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PoliceOutcome(StrEnum):
    """Police verification outcome."""

    PASSED = "passed"
    FAILED = "failed"


class MapOrigin(StrEnum):
    """Origin of a map-reduce job."""

    WORKFLOW = "workflow"
    COLLECTOR = "collector"


class MapJobStatus(StrEnum):
    """Map-reduce job state."""

    PARTITIONING = "partitioning"
    RUNNING = "running"
    REDUCING = "reducing"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MapPartitionStatus(StrEnum):
    """Map partition state."""

    READY = "ready"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"


class MapItemStatus(StrEnum):
    """Map item state."""

    READY = "ready"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    """Stable, non-sensitive error identifiers."""

    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RAW_VALUE_DETECTED = "raw_value_detected"
    EVIDENCE_REJECTED = "evidence_rejected"
    EVIDENCE_REQUIRED = "evidence_required"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    EVIDENCE_SCANNER_UNAVAILABLE = "evidence_scanner_unavailable"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_TRANSITION = "invalid_transition"
    INTERNAL_ERROR = "internal_error"
    CONFIG_ERROR = "config_error"
    UNKNOWN_SOURCE = "unknown_source"
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    SCHEMA_INVALID = "schema_invalid"
    SIGNATURE_INVALID = "signature_invalid"
    REPLAY_DETECTED = "replay_detected"
    TOKEN_INVALID = "token_invalid"
    TASK_NOT_LEASED = "task_not_leased"
    DEADLINE_PASSED = "deadline_passed"
    SWEEP_UNSUPPORTED = "sweep_unsupported"
    SWEEP_TIMEOUT = "sweep_timeout"
    RATE_LIMITED = "rate_limited"
    FETCH_FAILED = "fetch_failed"
    TOO_LARGE = "too_large"
    MALFORMED_IMAGE = "malformed_image"
    TASK_DEAD = "task_dead"
    LOOP_LIMIT_EXCEEDED = "loop_limit_exceeded"
    EXPRESSION_ERROR = "expression_error"
    WORKFLOW_INVALID = "workflow_invalid"
    NOT_RESCANNABLE = "not_rescannable"
    LLM_OUTPUT_INVALID = "llm_output_invalid"
    LLM_UNAVAILABLE = "llm_unavailable"
    DETECTOR_UNAVAILABLE = "detector_unavailable"
    PROVIDER_ERROR = "provider_error"
    DELIVERY_FAILED = "delivery_failed"
    RECIPIENT_UNKNOWN = "recipient_unknown"
    RECIPIENT_UNREACHABLE = "recipient_unreachable"
    UNKNOWN_TEMPLATE = "unknown_template"
    UNKNOWN_VARIABLE = "unknown_variable"
    TEMPLATE_EVENT_MISMATCH = "template_event_mismatch"
    LAST_ADMIN = "last_admin"
    NO_ROLE = "no_role"
    INVALID_QUERY = "invalid_query"
    RECIPIENT_OPTED_OUT = "recipient_opted_out"
    MERGED_GROUP = "merged_group"
    DLP_BLOCKED = "dlp_blocked"
    INTEGRATION_UNCONFIGURED = "integration_unconfigured"
    DUPLICATE_TRIGGER = "duplicate_trigger"
    LIMIT_EXCEEDED = "limit_exceeded"
    NOT_PROVISIONED = "not_provisioned"
    IDENTITY_CONFLICT = "identity_conflict"
    IDP_UNAVAILABLE = "idp_unavailable"
    INVALID_TOKEN = "invalid_token"
    CHAIN_BROKEN = "chain_broken"
    INDEX_GUARD_REJECTED = "index_guard_rejected"
    SCAN_INCOMPLETE = "scan_incomplete"
    FIELD_IN_USE = "field_in_use"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    INVALID_FIELD_VALUE = "invalid_field_value"


class Visibility(StrEnum):
    """Exposure visibility of a scanned resource."""

    PUBLIC = "public"
    ANYONE_WITH_LINK = "anyone_with_link"
    EXTERNAL_SHARED = "external_shared"
    DOMAIN = "domain"
    INTERNAL = "internal"
    PRIVATE = "private"
    UNKNOWN = "unknown"


def visibility_rank(v: Visibility) -> int | None:
    """Return the exposure rank, or None when visibility is unknown."""
    if v is Visibility.PUBLIC:
        return 6
    if v is Visibility.ANYONE_WITH_LINK:
        return 5
    if v is Visibility.EXTERNAL_SHARED:
        return 4
    if v is Visibility.DOMAIN:
        return 3
    if v is Visibility.INTERNAL:
        return 2
    if v is Visibility.PRIVATE:
        return 1
    return None


class ChannelKind(StrEnum):
    """Slack channel exposure category."""

    PUBLIC = "public"
    PRIVATE = "private"
    DM = "dm"
    CONNECT = "connect"


class RepoVisibility(StrEnum):
    """Repository visibility reported by a source provider."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"


class CoverageState(StrEnum):
    """Coverage result for a resource scan."""

    SCANNED = "scanned"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    TOO_LARGE = "too_large"
    PERMISSION_DENIED = "permission_denied"
    FETCH_FAILED = "fetch_failed"


class UnsupportedReason(StrEnum):
    """Reason that a resource could not be fully inspected."""

    ENCRYPTED = "encrypted"
    PASSWORD_PROTECTED = "password_protected"
    IMAGE = "image"
    ARCHIVE_TOO_DEEP = "archive_too_deep"
    BINARY = "binary"
    UNSUPPORTED_TYPE = "unsupported_type"


class PlacementKind(StrEnum):
    """Provider container kind used for keyed placement references."""

    GDRIVE_DRIVE = "gdrive_drive"
    GDRIVE_FOLDER = "gdrive_folder"
    SLACK_CHANNEL = "slack_channel"
    GITHUB_OWNER = "github_owner"
    GITHUB_REPO = "github_repo"


class IdentityKind(StrEnum):
    """Transient provider identity category."""

    EMAIL = "email"
    SLACK_USER = "slack_user"
    GITHUB_LOGIN = "github_login"
    OIDC_SUBJECT = "oidc_subject"


class IdentityOrigin(StrEnum):
    """Origin of a configured or observed identity."""

    MEMBER_EMAIL = "member_email"
    MEMBER_SLACK = "member_slack"
    MANUAL = "manual"
    OIDC = "oidc"


class PolicyEffect(StrEnum):
    """Policy decision effect."""

    ALLOW = "allow"
    VIOLATE = "violate"


class RemediationAction(StrEnum):
    """Read-only remediation requested from a resource owner."""

    REMOVE = "remove"
    RESTRICT = "restrict"


class ExceptionDuration(StrEnum):
    """Supported exception expiration periods."""

    DAYS_7 = "7d"
    DAYS_30 = "30d"
    DAYS_90 = "90d"
    UNTIL_REGRESSED = "until_regressed"
    FOREVER = "forever"


class ExceptionStatus(StrEnum):
    """Lifecycle state of an approved exception."""

    REQUESTED = "requested"
    ACTIVE = "active"
    REJECTED = "rejected"
    REVOKED = "revoked"
    EXPIRED = "expired"


class HealthCondition(StrEnum):
    """Collector or Police health condition."""

    STALE_SUCCESS = "stale_success"
    WATCH_EXPIRING = "watch_expiring"
    CONSECUTIVE_FAILURES = "consecutive_failures"
    UNSCANNABLE_PUBLIC = "unscannable_public"
    POLICE_SILENT = "police_silent"


class HealthStatus(StrEnum):
    """Health state of a monitored integration."""

    OK = "ok"
    WARNING = "warning"
    FAILING = "failing"
    UNKNOWN = "unknown"


class AssetTier(StrEnum):
    """Risk weighting category for a protected asset."""

    CLOUD_ROOT = "cloud_root"
    DEPLOY_TOKEN = "deploy_token"
    PERSONAL_TOKEN = "personal_token"
    SECRET = "secret"
    PII_HIGH = "pii_high"
    PII = "pii"


UNRESOLVED_STATUSES: tuple[IssueStatus, ...] = (
    IssueStatus.UNRESOLVED,
    IssueStatus.ACKNOWLEDGED,
    IssueStatus.IN_PROGRESS,
    IssueStatus.REGRESSED,
    IssueStatus.ESCALATING,
)
OPEN_STATUSES: tuple[IssueStatus, ...] = (
    IssueStatus.UNRESOLVED,
    IssueStatus.ACKNOWLEDGED,
    IssueStatus.IN_PROGRESS,
    IssueStatus.REGRESSED,
    IssueStatus.PENDING_VERIFICATION,
    IssueStatus.ESCALATING,
    IssueStatus.ARCHIVED,
)
RECONCILED_STATUSES: tuple[IssueStatus, ...] = OPEN_STATUSES + (IssueStatus.ALLOWED,)
CREDENTIAL_CLASSES: tuple[RuleClass, ...] = (
    RuleClass.CREDENTIAL,
    RuleClass.TOKEN,
    RuleClass.PRIVATE_KEY,
    RuleClass.PASSWORD,
)
PII_CLASSES: tuple[RuleClass, ...] = (
    RuleClass.PAYMENT_CARD,
    RuleClass.NATIONAL_ID,
    RuleClass.EMAIL,
    RuleClass.PHONE,
    RuleClass.ADDRESS,
)


def severity_for(rule_class: RuleClass) -> Severity:
    """Return the default severity for a rule class."""
    if rule_class in (
        RuleClass.CREDENTIAL,
        RuleClass.TOKEN,
        RuleClass.PRIVATE_KEY,
        RuleClass.PASSWORD,
        RuleClass.PAYMENT_CARD,
        RuleClass.NATIONAL_ID,
    ):
        return Severity.HIGH
    return Severity.MEDIUM
