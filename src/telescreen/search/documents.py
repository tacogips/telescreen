"""Build search documents only from persisted masked columns."""

from datetime import datetime

from telescreen.core.clock import ensure_utc
from telescreen.db.models import (
    AuditRecordRow,
    FindingRow,
    IssueRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.search.backend import SearchDocument


def _epoch(value: datetime) -> int:
    """Convert an aware timestamp to epoch seconds."""
    return int(ensure_utc(value).timestamp())


def issue_document(row: IssueRow, assignee: str | None) -> SearchDocument:
    """Build an issue document from display and lifecycle fields."""
    return SearchDocument(
        row.id,
        (
            ("short_id", f"TELE-{row.number}"),
            ("number", row.number),
            ("display_title", row.display_title),
            ("status", row.status),
            ("source", row.source),
            ("rule_class", row.rule_class),
            ("severity", row.severity),
            ("display_locator", row.display_locator),
            ("assignee", assignee),
            ("times_seen", row.times_seen),
            ("first_seen_ts", _epoch(row.first_seen)),
            ("last_seen_ts", _epoch(row.last_seen)),
        ),
    )


def finding_document(row: FindingRow) -> SearchDocument:
    """Build a finding document from masked observation columns."""
    return SearchDocument(
        row.id,
        (
            ("issue_id", row.issue_id),
            ("rule_id", row.rule_id),
            ("rule_class", row.rule_class),
            ("masked_snippet", row.masked_snippet),
            ("location", row.location),
            ("observed_at_ts", _epoch(row.observed_at)),
        ),
    )


def run_document(row: WorkflowRunRow, definition: WorkflowDefinitionRow | None) -> SearchDocument:
    """Build a run document with its workflow name when available."""
    return SearchDocument(
        row.id,
        (
            ("workflow", definition.name if definition is not None else ""),
            ("status", row.status),
            ("issue_id", row.issue_id),
            ("started_at_ts", _epoch(row.started_at)),
        ),
    )


def audit_document(row: AuditRecordRow) -> SearchDocument:
    """Build an audit document from its redacted persisted representation."""
    at: datetime = ensure_utc(row.at)
    return SearchDocument(
        row.id,
        (
            ("seq", row.seq),
            ("at", at.isoformat()),
            ("at_ts", _epoch(at)),
            ("actor_kind", row.actor_kind),
            ("actor_ref", row.actor_ref),
            ("action", row.action),
            ("target_kind", row.target_kind),
            ("target_id", row.target_id),
            ("summary", row.summary),
        ),
    )
