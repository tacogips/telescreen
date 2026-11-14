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


class IssueResolution(StrEnum):
    """How an issue was resolved."""

    MANUAL = "manual"
    AUTO = "auto"


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


class IssueEventKind(StrEnum):
    """Issue events that trigger workflows."""

    ISSUE_CREATED = "issue_created"
    ISSUE_REGRESSED = "issue_regressed"


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
    CHAIN_BROKEN = "chain_broken"
    INDEX_GUARD_REJECTED = "index_guard_rejected"


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
