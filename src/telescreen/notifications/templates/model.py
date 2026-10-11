"""Notification template value types, validation issues and errors."""

from dataclasses import dataclass, field
from enum import StrEnum

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DlpVerdict, ErrorCode, NotificationEvent


@dataclass(frozen=True)
class TemplateTexts:
    """Admin-entered template text fields of one template version."""

    subject: str = field(repr=False)
    body: str = field(repr=False)
    email_subject: str | None = field(default=None, repr=False)
    email_body: str | None = field(default=None, repr=False)
    slack_body: str | None = field(default=None, repr=False)
    description: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BuiltinTemplate:
    """A code-held default template for one event kind (version 0)."""

    name: str
    event_kind: NotificationEvent
    texts: TemplateTexts
    version: int = 0


@dataclass(frozen=True)
class ResolvedTemplate:
    """The template version selected for one notification request."""

    name: str
    version: int
    event_kind: NotificationEvent
    texts: TemplateTexts
    fallback: bool = False


class TemplateIssueCode(StrEnum):
    """Stable template validation codes; issues never carry template text."""

    INVALID_NAME = "invalid_name"
    RESERVED_NAME = "reserved_name"
    TEMPLATE_EVENT_MISMATCH = "template_event_mismatch"
    TOO_LONG = "too_long"
    INVALID_TEMPLATE = "invalid_template"
    UNKNOWN_VARIABLE = "unknown_variable"
    RAW_VALUE_DETECTED = "raw_value_detected"
    INCOMPLETE_VARIANT = "incomplete_variant"


@dataclass(frozen=True)
class TemplateIssue:
    """One validation problem identified by field name and code."""

    field: str
    code: TemplateIssueCode


class TemplateValidationError(TelescreenError):
    """Template diagnostics containing only field names and stable codes."""

    issues: tuple[TemplateIssue, ...]

    def __init__(self, issues: tuple[TemplateIssue, ...]) -> None:
        """Create a static template validation error."""
        self.issues = issues
        super().__init__(ErrorCode.VALIDATION_ERROR, "template validation failed")


@dataclass(frozen=True)
class PreviewVariant:
    """Rendered and DLP-gated preview text for one channel."""

    subject: str = field(repr=False)
    body: str = field(repr=False)
    dlp_verdict: DlpVerdict


@dataclass(frozen=True)
class PreviewResult:
    """Email and Slack previews rendered with a synthetic context."""

    email: PreviewVariant
    slack: PreviewVariant
