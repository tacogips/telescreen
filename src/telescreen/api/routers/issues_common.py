"""Shared issue route dependencies, safe serializers, and response helpers."""

from dataclasses import dataclass
from datetime import datetime

from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.context import ApiContext
from telescreen.api.common.pagination import decode_cursor
from telescreen.core.clock import ensure_utc
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_float,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.db.models import (
    FindingRow,
    IssueBookmarkRow,
    IssueExceptionRow,
    IssueHistoryRow,
    IssueRow,
)
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.service import IssueService
from telescreen.ports.directory import (
    ActorDirectory,
    ActorDisplay,
    OwnerDirectory,
    RecipientResolver,
)
from telescreen.ports.issues import AssigneeSuggester
from telescreen.ports.notifications import MemberNotifier
from telescreen.ports.search import SearchIndexer


@dataclass(frozen=True)
class IssuesRouterDeps:
    """Dependencies needed by issue routes."""

    ctx: ApiContext
    service: IssueService
    indexer: SearchIndexer
    remediation: IssueRemediationService | None = None
    exceptions: ExceptionService | None = None
    owners: OwnerDirectory | None = None
    resolver: RecipientResolver | None = None
    actors: ActorDirectory | None = None
    suggester: AssigneeSuggester | None = None
    member_notifier: MemberNotifier | None = None


@dataclass(frozen=True)
class IssueItemExtras:
    """Optional list-item projections supplied by later stream owners."""

    assignee: ActorDisplay | None
    merged_count: int
    bookmarked: bool
    trend: tuple[int, ...] = ()


def _cursor(cursor: str | None) -> tuple[str, ...]:
    """Decode an optional cursor."""
    return decode_cursor(cursor) if cursor is not None else ()


def _response(value: JsonValue, status_code: int = 200) -> Response:
    """Serialize the closed JSON model into an API response."""
    return Response(
        content=dump_json(value), status_code=status_code, media_type="application/json"
    )


def _optional_string(value: str | None) -> JsonValue:
    """Serialize an optional string without native untyped values."""
    return json_str(value) if value is not None else json_null()


def _issue_json(issue: IssueRow, extras: IssueItemExtras | None = None) -> JsonValue:
    """Serialize only safe issue columns."""
    resolved_at: str | None = (
        ensure_utc(issue.resolved_at).isoformat() if issue.resolved_at else None
    )
    assignee_json: JsonValue = json_null()
    merged_count: int = 0
    bookmarked: bool = False
    trend: tuple[int, ...] = ()
    if extras is not None:
        merged_count = extras.merged_count
        bookmarked = extras.bookmarked
        trend = extras.trend
        if extras.assignee is not None:
            assignee_json = json_object(
                (
                    ("kind", json_str(extras.assignee.kind.value)),
                    ("ref", json_str(extras.assignee.ref)),
                    ("display_name", json_str(extras.assignee.display_name)),
                )
            )
    archive_json: JsonValue = json_null()
    if issue.archive_kind is not None:
        archive_json = json_object(
            (
                ("kind", json_str(issue.archive_kind)),
                ("until", _timestamp(issue.archive_until)),
                (
                    "threshold",
                    json_int(issue.archive_threshold)
                    if issue.archive_threshold is not None
                    else json_null(),
                ),
                (
                    "baseline",
                    json_int(issue.archive_baseline)
                    if issue.archive_baseline is not None
                    else json_null(),
                ),
            )
        )
    return json_object(
        (
            ("id", json_str(issue.id)),
            ("number", json_int(issue.number)),
            ("short_id", json_str(f"TELE-{issue.number}")),
            ("grouping_key", json_str(issue.grouping_key)),
            ("source", json_str(issue.source)),
            ("resource_key", json_str(issue.resource_key)),
            ("display_locator", json_str(issue.display_locator)),
            ("resource_ref_json", _optional_string(issue.resource_ref_json)),
            ("collector_config_id", _optional_string(issue.collector_config_id)),
            ("display_title", json_str(issue.display_title)),
            ("rule_class", json_str(issue.rule_class)),
            ("severity", json_str(issue.severity)),
            ("base_severity", json_str(issue.base_severity)),
            ("exposure_visibility", json_str(issue.exposure_visibility)),
            ("risk_score", json_int(issue.risk_score)),
            ("policy_name", _optional_string(issue.policy_name)),
            (
                "policy_version",
                json_int(issue.policy_version) if issue.policy_version is not None else json_null(),
            ),
            ("status", json_str(issue.status)),
            ("assignee", assignee_json),
            ("priority", json_str(issue.priority)),
            ("priority_manual", json_bool(issue.priority_manual)),
            ("needs_review", json_bool(issue.needs_review)),
            ("archive", archive_json),
            ("updated_at", json_str(ensure_utc(issue.updated_at).isoformat())),
            ("merged_count", json_int(merged_count)),
            ("bookmarked", json_bool(bookmarked)),
            ("trend", json_array(tuple(json_int(value) for value in trend))),
            ("first_seen", json_str(ensure_utc(issue.first_seen).isoformat())),
            ("last_seen", json_str(ensure_utc(issue.last_seen).isoformat())),
            ("last_checked_at", json_str(ensure_utc(issue.last_checked_at).isoformat())),
            ("sweep_covered", json_bool(issue.sweep_covered)),
            ("times_seen", json_int(issue.times_seen)),
            ("distinct_fingerprints", json_int(issue.distinct_fingerprints)),
            ("regression_count", json_int(issue.regression_count)),
            ("resolved_at", _optional_string(resolved_at)),
            ("resolution", _optional_string(issue.resolution)),
        )
    )


async def issue_item_extras(
    session: AsyncSession,
    issues: tuple[IssueRow, ...],
    member_id: str | None,
    actors: ActorDirectory | None,
) -> tuple[IssueItemExtras, ...]:
    """Batch safe assignee display, merged-child counts and bookmark state."""
    refs: list[str] = []
    issue_refs: list[str | None] = []
    for issue in issues:
        ref: str | None = (
            f"member:{issue.assignee_member_id}"
            if issue.assignee_member_id is not None
            else f"team:{issue.assignee_team_id}"
            if issue.assignee_team_id is not None
            else None
        )
        issue_refs.append(ref)
        if ref is not None and ref not in refs:
            refs.append(ref)
    displays: tuple[ActorDisplay, ...] = (
        await actors.display(session, tuple(refs)) if actors is not None and refs else ()
    )
    merged_rows: list[tuple[str, int]] = []
    if issues:
        merged_result: Result[tuple[str | None, int]] = await session.execute(
            select(IssueRow.merged_into_id, func.count(IssueRow.id))
            .where(IssueRow.merged_into_id.in_(tuple(issue.id for issue in issues)))
            .group_by(IssueRow.merged_into_id)
        )
        merged_rows = [(row[0], row[1]) for row in merged_result.all() if row[0] is not None]
    bookmarked_ids: set[str] = set()
    if member_id is not None and issues:
        bookmarked_ids = set(
            (
                await session.scalars(
                    select(IssueBookmarkRow.issue_id).where(
                        IssueBookmarkRow.member_id == member_id,
                        IssueBookmarkRow.issue_id.in_(tuple(issue.id for issue in issues)),
                    )
                )
            ).all()
        )
    result: list[IssueItemExtras] = []
    for issue, ref in zip(issues, issue_refs, strict=True):
        display: ActorDisplay | None = None
        if ref is not None:
            display = next((item for item in displays if item.ref == ref), None)
            if display is None:
                kind: ActorDisplayKind = (
                    ActorDisplayKind.MEMBER if ref.startswith("member:") else ActorDisplayKind.TEAM
                )
                display = ActorDisplay(ref, kind, ref, None, False)
        child_count: int = next(
            (count for parent_id, count in merged_rows if parent_id == issue.id), 0
        )
        result.append(IssueItemExtras(display, child_count, issue.id in bookmarked_ids))
    return tuple(result)


def _issue_detail_json(
    issue: IssueRow,
    assignee_ref: str | None,
    owner_present: bool,
    owner_member_ref: str | None,
    active_exception: IssueExceptionRow | None,
) -> JsonValue:
    """Serialize issue detail with safe exposure and owner projections."""
    members: list[tuple[str, JsonValue]] = [
        (member.name, member.value) for member in _issue_json(issue).members
    ]
    active_exception_json: JsonValue = json_null()
    if active_exception is not None:
        active_exception_json = json_object(
            (
                ("id", json_str(active_exception.id)),
                ("status", json_str(active_exception.status)),
                ("duration", json_str(active_exception.duration)),
                ("expires_at", _timestamp(active_exception.expires_at)),
                ("approver_ref", _optional_string(active_exception.approver_ref)),
            )
        )
    members.extend(
        (
            ("exposure", _safe_exposure(issue.exposure_json)),
            ("exposure_changed_at", _timestamp(issue.exposure_changed_at)),
            ("asset_tier", _optional_string(issue.asset_tier)),
            ("assignee_ref", _optional_string(assignee_ref)),
            (
                "owner",
                json_object(
                    (
                        ("present", json_bool(owner_present)),
                        ("member_ref", _optional_string(owner_member_ref)),
                    )
                ),
            ),
            ("active_exception", active_exception_json),
        )
    )
    return json_object(members)


def _safe_exposure(text: str | None) -> JsonValue:
    """Project persisted exposure JSON to its closed enum/count shape."""
    if text is None:
        return json_null()
    value: JsonValue = parse_json(text)
    names: tuple[str, ...] = (
        "visibility",
        "external_principal_count",
        "shared_drive",
        "channel_kind",
        "repo_visibility",
        "is_fork",
    )
    return json_object(
        tuple((name, child) for name in names if (child := value.get(name)) is not None)
    )


def _timestamp(value: datetime | None) -> JsonValue:
    """Serialize an optional UTC timestamp."""
    return json_str(ensure_utc(value).isoformat()) if value is not None else json_null()


def _unavailable() -> Response:
    """Return the static feature-off dependency response."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str("internal_error")),
                            ("detail", json_str("internal error")),
                        )
                    ),
                ),
            )
        ),
        status_code=503,
    )


def _invalid_transition() -> Response:
    """Return the v1.1 422 response for dedicated-transition-only operations."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str("invalid_transition")),
                            ("detail", json_str("invalid issue transition")),
                        )
                    ),
                ),
            )
        ),
        status_code=422,
    )


def _finding_json(finding: FindingRow) -> JsonValue:
    """Serialize a masked finding observation."""
    return json_object(
        (
            ("id", json_str(finding.id)),
            ("issue_id", json_str(finding.issue_id)),
            ("scan_job_id", _optional_string(finding.scan_job_id)),
            ("source", json_str(finding.source)),
            ("resource_key", json_str(finding.resource_key)),
            ("display_locator", json_str(finding.display_locator)),
            ("location", json_str(finding.location)),
            ("rule_id", json_str(finding.rule_id)),
            ("rule_class", json_str(finding.rule_class)),
            ("detector", json_str(finding.detector)),
            ("confidence", json_float(finding.confidence)),
            ("masked_snippet", json_str(finding.masked_snippet)),
            ("fingerprint", json_str(finding.fingerprint)),
            ("fingerprint_key_id", json_str(finding.fingerprint_key_id)),
            ("observed_at", json_str(ensure_utc(finding.observed_at).isoformat())),
        )
    )


def _history_json(entry: IssueHistoryRow) -> JsonValue:
    """Serialize masked issue history."""
    return json_object(
        (
            ("id", json_str(entry.id)),
            ("issue_id", json_str(entry.issue_id)),
            ("kind", json_str(entry.kind)),
            ("actor_ref", json_str(entry.actor_ref)),
            ("from_value", _optional_string(entry.from_value)),
            ("to_value", _optional_string(entry.to_value)),
            ("note", _optional_string(entry.note)),
            ("at", json_str(ensure_utc(entry.at).isoformat())),
        )
    )


def _bucket_start(value: datetime, bucket: str) -> datetime:
    """Normalize a timestamp to its hourly or daily bucket."""
    if bucket == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)
