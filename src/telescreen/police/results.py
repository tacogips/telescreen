"""Validation, privacy gate, and routing for Police task results."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.contracts.validator import SchemaError, validate
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import (
    ActorKind,
    ChannelKind,
    DataAssetKind,
    DetectorKind,
    ErrorCode,
    PoliceTaskStatus,
    RepoVisibility,
    RuleClass,
    Visibility,
)
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader, Exposure
from telescreen.ports.findings import (
    ExternalFinding,
    ExternalScan,
    LocatorExposure,
    LocatorInventory,
    RecordOutcome,
)
from telescreen.ports.issues import IssueRecorder
from telescreen.ports.sweeps import (
    MapResultSink,
    PartitionPage,
    RuleClassCount,
    ShardFailure,
    ShardSpec,
    ShardSummary,
    SubCorpus,
)
from telescreen.ports.workflow import RunSignaler, StepSignal, StepSignalKind


def _member(value: JsonValue | None, name: str) -> JsonValue | None:
    """Get a named member from a JSON object."""
    return value.get(name) if value is not None and value.kind is JsonKind.OBJECT else None


def _str(value: JsonValue | None, default: str = "") -> str:
    """Return a string member or a safe empty default."""
    return value.str_value if value is not None and value.kind is JsonKind.STRING else default


def _int(value: JsonValue | None) -> int:
    """Return an integer member or zero."""
    return value.int_value if value is not None and value.kind is JsonKind.INT else 0


def _contains_hit(value: JsonValue, redactor: Redactor) -> bool:
    """Check every string leaf recursively without retaining its raw text."""
    if value.kind is JsonKind.STRING:
        return redactor.has_hits(value.str_value)
    if value.kind is JsonKind.ARRAY:
        return any(_contains_hit(item, redactor) for item in value.items)
    return value.kind is JsonKind.OBJECT and any(
        _contains_hit(member.value, redactor) for member in value.members
    )


def _safe_result_json(value: JsonValue) -> str:
    """Serialize accepted result content without the one-time callback token."""
    members: tuple[tuple[str, JsonValue], ...] = tuple(
        (member.name, member.value) for member in value.members if member.name != "result_token"
    )
    return dump_json(json_object(members))


def _external_findings(value: JsonValue | None) -> tuple[ExternalFinding, ...]:
    """Translate schema-validated masked finding members to the issue port DTO."""
    if value is None or value.kind is not JsonKind.ARRAY:
        return ()
    findings: list[ExternalFinding] = []
    for item in value.items:
        rule_class: RuleClass = RuleClass(_str(_member(item, "rule_class")))
        confidence_value: JsonValue | None = _member(item, "confidence")
        confidence_result: float | None = (
            confidence_value.as_float() if confidence_value is not None else None
        )
        confidence: float = confidence_result if confidence_result is not None else 0.0
        findings.append(
            ExternalFinding(
                _str(_member(item, "locator")),
                _str(_member(item, "rule_id")),
                rule_class,
                _str(_member(item, "masked_snippet")),
                _str(_member(item, "location")),
                confidence,
                _str(_member(item, "external_fingerprint")),
            )
        )
    return tuple(findings)


def _resource_exposures(
    value: JsonValue | None, valid_locators: frozenset[str]
) -> tuple[tuple[LocatorExposure, ...], int]:
    """Decode and retain exposure entries for resources represented by the result."""
    if value is None or value.kind is not JsonKind.ARRAY:
        return (), 0
    exposures: list[LocatorExposure] = []
    dropped: int = 0
    for item in value.items:
        locator: str = _str(_member(item, "locator"))
        if locator not in valid_locators:
            dropped += 1
            continue
        visibility: Visibility = Visibility(_str(_member(item, "visibility")))
        count: JsonValue | None = _member(item, "external_principal_count")
        shared_drive: JsonValue | None = _member(item, "shared_drive")
        is_fork: JsonValue | None = _member(item, "is_fork")
        channel: JsonValue | None = _member(item, "channel_kind")
        repository: JsonValue | None = _member(item, "repo_visibility")
        exposures.append(
            LocatorExposure(
                locator,
                Exposure(
                    visibility,
                    _int(count),
                    shared_drive.bool_value if shared_drive is not None else False,
                    ChannelKind(_str(channel)) if channel is not None else None,
                    RepoVisibility(_str(repository)) if repository is not None else None,
                    is_fork.bool_value if is_fork is not None else False,
                ),
            )
        )
    return tuple(exposures), dropped


def _resource_inventories(
    value: JsonValue | None, allowed_locators: frozenset[str]
) -> tuple[tuple[LocatorInventory, ...], int]:
    """Decode inventory entries only for locators represented by the result."""
    if value is None or value.kind is not JsonKind.ARRAY:
        return (), 0
    inventories: list[LocatorInventory] = []
    dropped: int = 0
    for item in value.items:
        locator: str = _str(_member(item, "locator"))
        if locator not in allowed_locators:
            dropped += 1
            continue
        fingerprints_value: JsonValue | None = _member(item, "item_fingerprints")
        fingerprints: tuple[str, ...] | None = (
            tuple(entry.str_value for entry in fingerprints_value.items)
            if fingerprints_value is not None and fingerprints_value.kind is JsonKind.ARRAY
            else None
        )
        count_value: JsonValue | None = _member(item, "item_count")
        count: int | None = count_value.int_value if count_value is not None else None
        kind_value: JsonValue | None = _member(item, "kind")
        kind: DataAssetKind | None = (
            DataAssetKind(_str(kind_value)) if kind_value is not None else None
        )
        inventories.append(LocatorInventory(locator, fingerprints, count, kind))
    return tuple(inventories), dropped


@dataclass(frozen=True)
class ResultService:
    """Accept only schema-valid, redactor-clean worker results."""

    issues: IssueRecorder
    configs: CollectorConfigReader
    signaler: RunSignaler
    map_sink: MapResultSink
    redactor: Redactor
    audit: AuditLog
    clock: Clock

    async def accept(
        self,
        session: AsyncSession,
        task: PoliceTaskRow,
        raw_result: bytes,
        worker: PoliceWorkerRow | None = None,
    ) -> None:
        """Validate and route one result, leaving rejected tasks untouched."""
        errors: list[SchemaError] = validate("result-envelope.schema.json", raw_result)
        if errors:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police result")
        result: JsonValue = parse_json(raw_result)
        if _str(_member(result, "task_id")) != task.id:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "result task id does not match")
        if task.task_type == "verify_remediation" and _member(result, "outcome") is None:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "verification outcome is required")
        if task.status in {
            PoliceTaskStatus.CANCELLED.value,
            PoliceTaskStatus.COMPLETED.value,
            PoliceTaskStatus.FAILED.value,
            PoliceTaskStatus.EXPIRED.value,
        }:
            raise TelescreenError(ErrorCode.CONFLICT, "police task is no longer open")
        if _contains_hit(result, self.redactor):
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:police-result",
                    "police.result_rejected",
                    "police_task",
                    task.id,
                    "Police result rejected by data loss prevention",
                    json_object((("task_id", json_str(task.id)),)),
                ),
            )
            raise TelescreenError(
                ErrorCode.RAW_VALUE_DETECTED, "police result contains sensitive data"
            )

        status: str = _str(_member(result, "status"))
        outcome: str = _str(_member(result, "outcome"))
        findings: tuple[ExternalFinding, ...] = _external_findings(_member(result, "findings"))
        exposures: tuple[LocatorExposure, ...] = ()
        exposures_dropped: int = 0
        inventories: tuple[LocatorInventory, ...] = ()
        inventories_dropped: int = 0
        record_outcome: RecordOutcome | None = None
        config_name: str | None = None
        if task.collector_config_id is not None:
            config: CollectorConfig | None = await self.configs.get(
                session, task.collector_config_id
            )
            config_name = config.name if config is not None else None
        worker_name: str = worker.name if worker is not None else "worker"
        if status == "failed":
            await self._route_failure(session, task, result)
        elif task.task_type in {"detect", "scan_shard"}:
            stats: JsonValue | None = _member(result, "stats")
            if task.task_type == "scan_shard" and stats is None:
                raise TelescreenError(
                    ErrorCode.VALIDATION_ERROR, "scan shard statistics are required"
                )
            complete_value: JsonValue | None = _member(result, "complete_locators")
            complete_locators: tuple[str, ...] = (
                tuple(item.str_value for item in complete_value.items)
                if complete_value is not None and complete_value.kind is JsonKind.ARRAY
                else ()
            )
            if task.task_type == "scan_shard" and _int(_member(stats, "resources_failed")) > 0:
                complete_locators = ()
            allowed_locators: frozenset[str] = frozenset(
                (*complete_locators, *(finding.locator for finding in findings))
            )
            exposures, exposures_dropped = _resource_exposures(
                _member(result, "resource_exposures"), allowed_locators
            )
            inventories, inventories_dropped = _resource_inventories(
                _member(result, "resource_inventories"), allowed_locators
            )
            record_outcome = await self.issues.record_external(
                session,
                ExternalScan(
                    "police:" + (config_name or worker_name),
                    task.collector_config_id,
                    None,
                    DetectorKind.POLICE,
                    findings,
                    complete_locators,
                    True,
                    self.clock.now(),
                    exposures,
                    inventories,
                ),
            )
            if task.task_type == "scan_shard":
                await self.map_sink.shard_finished(
                    session,
                    task.id,
                    _shard_summary(result, findings, record_outcome),
                )
        elif task.task_type == "partition":
            page_value: JsonValue | None = _member(result, "payload")
            if page_value is None:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid partition result")
            partition_errors: list[SchemaError] = validate(
                "partition-result.schema.json", dump_json(page_value).encode()
            )
            if partition_errors:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid partition result")
            await self.map_sink.partition_page(session, task.id, _partition_page(page_value))

        if task.step_run_id is not None:
            signal_outcome: str = "error" if status == "failed" else outcome or "passed"
            await self.signaler.signal(
                session,
                task.step_run_id,
                StepSignal(
                    StepSignalKind.POLICE_RESULT,
                    signal_outcome,
                    json_object(
                        (
                            ("summary", json_str(_str(_member(result, "summary")))),
                            ("findings_count", json_int(len(findings))),
                        )
                    ),
                ),
            )
        task.status = (
            PoliceTaskStatus.FAILED.value
            if status == "failed"
            else PoliceTaskStatus.COMPLETED.value
        )
        task.result_json = _safe_result_json(result)
        task.outcome = outcome or ("error" if status == "failed" else None)
        task.completed_at = self.clock.now()
        audit_details: JsonValue = json_object(
            (("task_id", json_str(task.id)),)
            + (
                (("exposures_dropped", json_int(exposures_dropped)),)
                if exposures_dropped > 0
                else ()
            )
            + (
                (("inventories_dropped", json_int(inventories_dropped)),)
                if inventories_dropped > 0
                else ()
            )
        )
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:police-result",
                "police.result_accepted",
                "police_task",
                task.id,
                "Police result accepted",
                audit_details,
            ),
        )

    async def _route_failure(
        self, session: AsyncSession, task: PoliceTaskRow, result: JsonValue
    ) -> None:
        """Route failed map tasks through the sweep sink."""
        if task.task_type not in {"partition", "scan_shard"}:
            return
        error_value: JsonValue | None = _member(result, "error")
        code: str = _str(_member(error_value, "code"))
        retry: JsonValue | None = _member(error_value, "retry_after_seconds")
        retry_value: float | None = retry.as_float() if retry is not None else None
        error_code: ErrorCode = (
            ErrorCode.RATE_LIMITED if code == "rate_limited" else ErrorCode.FETCH_FAILED
        )
        await self.map_sink.task_failed(session, task.id, ShardFailure(error_code, retry_value))


def _partition_page(value: JsonValue) -> PartitionPage:
    """Decode the already validated partition result DTO."""
    shards_value: JsonValue | None = _member(value, "shards")
    corpora_value: JsonValue | None = _member(value, "sub_corpora")
    shards: list[ShardSpec] = []
    if shards_value is not None and shards_value.kind is JsonKind.ARRAY:
        for shard in shards_value.items:
            descriptor: JsonValue | None = _member(shard, "descriptor")
            shards.append(
                ShardSpec(
                    _str(_member(shard, "shard_key")),
                    descriptor if descriptor is not None else parse_json("{}"),
                    _int(_member(shard, "weight")),
                )
            )
    corpora: list[SubCorpus] = []
    if corpora_value is not None and corpora_value.kind is JsonKind.ARRAY:
        corpora.extend(SubCorpus(_str(_member(item, "corpus_key"))) for item in corpora_value.items)
    cursor: JsonValue | None = _member(value, "next_cursor")
    return PartitionPage(
        tuple(shards),
        cursor.str_value if cursor is not None and cursor.kind is JsonKind.STRING else None,
        tuple(corpora),
    )


def _shard_summary(
    result: JsonValue, findings: tuple[ExternalFinding, ...], outcome: RecordOutcome
) -> ShardSummary:
    """Combine wire stats and issue recorder counts without returning content."""
    stats: JsonValue | None = _member(result, "stats")
    counts: list[RuleClassCount] = []
    for rule_class in RuleClass:
        count: int = sum(1 for finding in findings if finding.rule_class is rule_class)
        if count:
            counts.append(RuleClassCount(rule_class, count))
    return ShardSummary(
        _int(_member(stats, "resources_scanned")),
        0,
        _int(_member(stats, "resources_failed")),
        _int(_member(stats, "bytes_scanned")),
        outcome.findings_inserted,
        outcome.issues_opened,
        outcome.issues_auto_resolved,
        tuple(counts),
    )
