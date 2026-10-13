"""Typed environment configuration for Telescreen."""

import re
from dataclasses import dataclass, field
from typing import TypeVar

from croniter import croniter

from telescreen.core.errors import ConfigError
from telescreen.core.oidc_settings import OidcSettings, load_oidc_settings
from telescreen.core.risk_weights import RiskWeights, parse_risk_weights
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    DetectorKind,
    DetectorMode,
    DlpPolicy,
    ManagedLlmProvider,
)

_EnumType = TypeVar("_EnumType", bound=str)


@dataclass(frozen=True)
class FingerprintKeyRef:
    """Configured key version and external secret reference."""

    version: int
    ref: str = field(repr=False)


@dataclass(frozen=True)
class TelescreenSettings:
    """Validated settings loaded from environment variables."""

    database_url: str | None = None
    fingerprint_key_ref: str | None = None
    police_signing_key_ref: str | None = None
    police_enrollment_token_ref: str | None = None
    police_allow_http: bool = False
    meili_url: str | None = None
    meili_api_key_ref: str | None = None
    detectors: tuple[DetectorKind, ...] = (DetectorKind.RULE,)
    detector_mode: DetectorMode = DetectorMode.UNION
    dlp_detectors: tuple[DetectorKind, ...] = (DetectorKind.RULE,)
    dlp_policy: DlpPolicy = DlpPolicy.MASK
    managed_llm_provider: ManagedLlmProvider = ManagedLlmProvider.CLAUDE
    managed_llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key_ref: str | None = None
    openai_api_key_ref: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    local_llm_base_url: str | None = None
    local_llm_model: str | None = None
    local_llm_api_key_ref: str | None = None
    ml_model: str | None = None
    llm_batch_max_chars: int = 12000
    llm_batch_max_items: int = 20
    llm_concurrency: int = 4
    scan_batch_items: int = 200
    scan_max_bytes: int = 5242880
    evidence_dir: str = "./var/evidence"
    evidence_max_bytes: int = 10485760
    slack_signing_secret_ref: str | None = None
    slack_bot_token_ref: str | None = None
    github_webhook_secret_ref: str | None = None
    github_api_url: str = "https://api.github.com"
    google_service_account_ref: str | None = None
    google_delegated_admin: str | None = None
    map_dispatch_seconds: int = 2
    sweep_shard_size: int = 100
    public_base_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password_ref: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    worker_concurrency: int = 8
    worker_poll_seconds: float = 1.0
    web_dist: str | None = None
    log_level: str = "INFO"
    fingerprint_keys: tuple[FingerprintKeyRef, ...] = ()
    fingerprint_active_version: int = 1
    internal_domains: tuple[str, ...] = ()
    owner_routing: bool = False
    default_team: str | None = None
    digest_cron: str | None = "0 9 * * 1"
    health_recipients: tuple[str, ...] = ()
    health_failure_threshold: int = 3
    alert_unscannable_public: bool = True
    risk_weights: RiskWeights = RiskWeights()
    oidc: OidcSettings | None = None
    ui_token_login: bool = True
    escalating_min_findings: int = 10
    escalating_factor: int = 3
    weekly_report_cron: str | None = "0 9 * * 1"
    github_issues_token_ref: str | None = None
    github_issues_repos: tuple[str, ...] = ()
    data_overdue_cron: str | None = "0 9 * * *"


def _read(env: EnvSource, name: str) -> str | None:
    """Return a setting by its TELESCREEN-prefixed environment name."""
    return env.get(f"TELESCREEN_{name}")


def _optional(env: EnvSource, name: str) -> str | None:
    """Return a non-empty optional setting."""
    value: str | None = _read(env, name)
    return value if value else None


def _integer(env: EnvSource, name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer setting without exposing invalid values."""
    raw: str | None = _read(env, name)
    if raw is None:
        return default
    try:
        value: int = int(raw)
    except ValueError:
        raise ConfigError(f"TELESCREEN_{name} must be an integer") from None
    if value < minimum:
        raise ConfigError(f"TELESCREEN_{name} is outside the allowed range")
    return value


def _number(env: EnvSource, name: str, default: float, minimum: float = 0.0) -> float:
    """Read a positive floating point setting without exposing invalid values."""
    raw: str | None = _read(env, name)
    if raw is None:
        return default
    try:
        value: float = float(raw)
    except ValueError:
        raise ConfigError(f"TELESCREEN_{name} must be a number") from None
    if value <= minimum:
        raise ConfigError(f"TELESCREEN_{name} is outside the allowed range")
    return value


def _fingerprint_key_settings(
    env: EnvSource,
) -> tuple[tuple[FingerprintKeyRef, ...], int, str | None]:
    """Read versioned keys and retain the version-one compatibility reference."""
    legacy_ref: str | None = _read(env, "FINGERPRINT_KEY")
    keys_raw: str | None = _read(env, "FINGERPRINT_KEYS")
    if legacy_ref is not None and keys_raw is not None:
        raise ConfigError("TELESCREEN_FINGERPRINT_KEY and TELESCREEN_FINGERPRINT_KEYS conflict")
    if keys_raw is None:
        active_raw: str | None = _read(env, "FINGERPRINT_ACTIVE_VERSION")
        if not legacy_ref:
            if active_raw is not None:
                raise ConfigError("TELESCREEN_FINGERPRINT_ACTIVE_VERSION is not configured")
            return (), 1, None
        if active_raw is not None and active_raw != "1":
            raise ConfigError("TELESCREEN_FINGERPRINT_ACTIVE_VERSION is not configured")
        return (FingerprintKeyRef(1, legacy_ref),), 1, legacy_ref
    configured: list[FingerprintKeyRef] = []
    versions: set[int] = set()
    for entry in keys_raw.split(","):
        if "=" not in entry:
            raise ConfigError("TELESCREEN_FINGERPRINT_KEYS is invalid")
        version_text: str
        ref: str
        version_text, ref = entry.split("=", 1)
        try:
            version: int = int(version_text.strip())
        except ValueError:
            raise ConfigError("TELESCREEN_FINGERPRINT_KEYS is invalid") from None
        if version <= 0 or version in versions or not ref.strip():
            raise ConfigError("TELESCREEN_FINGERPRINT_KEYS is invalid")
        versions.add(version)
        configured.append(FingerprintKeyRef(version, ref.strip()))
    if 1 not in versions:
        raise ConfigError("TELESCREEN_FINGERPRINT_KEYS must include version 1")
    active_version: int = max(versions)
    active_raw: str | None = _read(env, "FINGERPRINT_ACTIVE_VERSION")
    if active_raw is not None:
        try:
            active_version = int(active_raw)
        except ValueError:
            raise ConfigError("TELESCREEN_FINGERPRINT_ACTIVE_VERSION must be an integer") from None
    if active_version <= 0 or active_version not in versions:
        raise ConfigError("TELESCREEN_FINGERPRINT_ACTIVE_VERSION is not configured")
    version_one_ref: str = next(item.ref for item in configured if item.version == 1)
    return tuple(configured), active_version, version_one_ref


def _comma_values(env: EnvSource, name: str, *, lowercase: bool = False) -> tuple[str, ...]:
    """Read a normalized comma-separated setting as a tuple."""
    raw: str | None = _read(env, name)
    if raw is None or not raw.strip():
        return ()
    values: tuple[str, ...] = tuple(
        (part.strip().lower() if lowercase else part.strip()) for part in raw.split(",")
    )
    if any(not value for value in values):
        raise ConfigError(f"TELESCREEN_{name} is invalid")
    return values


def _digest_cron(env: EnvSource) -> str | None:
    """Read an optional cron schedule, with an empty value disabling it."""
    raw: str | None = _read(env, "DIGEST_CRON")
    if raw is None:
        return "0 9 * * 1"
    if not raw.strip():
        return None
    if not croniter.is_valid(raw):
        raise ConfigError("TELESCREEN_DIGEST_CRON is invalid")
    return raw


def _weekly_report_cron(env: EnvSource) -> str | None:
    """Read the optional weekly report schedule, with an empty value disabling it."""
    raw: str | None = _read(env, "WEEKLY_REPORT_CRON")
    if raw is None:
        return "0 9 * * 1"
    if not raw.strip():
        return None
    if not croniter.is_valid(raw):
        raise ConfigError("TELESCREEN_WEEKLY_REPORT_CRON is invalid")
    return raw


def _data_overdue_cron(env: EnvSource) -> str | None:
    """Read the optional data overdue schedule, with an empty value disabling it."""
    raw: str | None = _read(env, "DATA_OVERDUE_CRON")
    if raw is None:
        return "0 9 * * *"
    if not raw.strip():
        return None
    if not croniter.is_valid(raw):
        raise ConfigError("TELESCREEN_DATA_OVERDUE_CRON is invalid")
    return raw


def _github_issues_repos(env: EnvSource) -> tuple[str, ...]:
    """Read the bounded GitHub issue repository allow-list."""
    values: tuple[str, ...] = _comma_values(env, "GITHUB_ISSUES_REPOS")
    if len(values) > 20 or any(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None for value in values
    ):
        raise ConfigError("TELESCREEN_GITHUB_ISSUES_REPOS is invalid")
    return values


def _boolean(env: EnvSource, name: str, default: bool) -> bool:
    """Read a case-insensitive boolean setting."""
    raw: str | None = _read(env, name)
    if raw is None:
        return default
    normalized: str = raw.lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ConfigError(f"TELESCREEN_{name} must be true, false, 1, or 0")


def _enum_value[T: str](env: EnvSource, name: str, enum_type: type[T], default: T) -> T:
    """Read one string enum value."""
    raw: str | None = _read(env, name)
    if raw is None:
        return default
    try:
        return enum_type(raw)
    except ValueError:
        raise ConfigError(f"TELESCREEN_{name} has an unsupported value") from None


def _enum_list[T: str](env: EnvSource, name: str, enum_type: type[T]) -> tuple[T, ...]:
    """Read a comma-separated non-empty enum list."""
    raw: str | None = _read(env, name)
    if raw is None:
        return (enum_type("rule"),)
    values: tuple[str, ...] = tuple(part.strip() for part in raw.split(","))
    if not values or any(not value for value in values):
        raise ConfigError(f"TELESCREEN_{name} must be a comma-separated list")
    try:
        return tuple(enum_type(value) for value in values)
    except ValueError:
        raise ConfigError(f"TELESCREEN_{name} contains an unsupported value") from None


def load_settings(env: EnvSource) -> TelescreenSettings:
    """Load settings without enforcing service-specific required variables."""
    provider: ManagedLlmProvider = _enum_value(
        env, "MANAGED_LLM_PROVIDER", ManagedLlmProvider, ManagedLlmProvider.CLAUDE
    )
    model: str | None = _optional(env, "MANAGED_LLM_MODEL")
    if provider is ManagedLlmProvider.CODEX and model is None:
        raise ConfigError("TELESCREEN_MANAGED_LLM_MODEL is required for codex")
    fingerprint_keys: tuple[FingerprintKeyRef, ...]
    fingerprint_active_version: int
    fingerprint_key_ref: str | None
    fingerprint_keys, fingerprint_active_version, fingerprint_key_ref = _fingerprint_key_settings(
        env
    )
    return TelescreenSettings(
        database_url=_optional(env, "DATABASE_URL"),
        fingerprint_key_ref=fingerprint_key_ref,
        police_signing_key_ref=_optional(env, "POLICE_SIGNING_KEY"),
        police_enrollment_token_ref=_optional(env, "POLICE_ENROLLMENT_TOKEN"),
        police_allow_http=_boolean(env, "POLICE_ALLOW_HTTP", False),
        meili_url=_optional(env, "MEILI_URL"),
        meili_api_key_ref=_optional(env, "MEILI_API_KEY"),
        detectors=_enum_list(env, "DETECTORS", DetectorKind),
        detector_mode=_enum_value(env, "DETECTOR_MODE", DetectorMode, DetectorMode.UNION),
        dlp_detectors=_enum_list(env, "DLP_DETECTORS", DetectorKind),
        dlp_policy=_enum_value(env, "DLP_POLICY", DlpPolicy, DlpPolicy.MASK),
        managed_llm_provider=provider,
        managed_llm_model=model or "claude-sonnet-5-5",
        anthropic_api_key_ref=_optional(env, "ANTHROPIC_API_KEY"),
        openai_api_key_ref=_optional(env, "OPENAI_API_KEY"),
        openai_base_url=_optional(env, "OPENAI_BASE_URL") or "https://api.openai.com/v1",
        local_llm_base_url=_optional(env, "LOCAL_LLM_BASE_URL"),
        local_llm_model=_optional(env, "LOCAL_LLM_MODEL"),
        local_llm_api_key_ref=_optional(env, "LOCAL_LLM_API_KEY"),
        ml_model=_optional(env, "ML_MODEL"),
        llm_batch_max_chars=_integer(env, "LLM_BATCH_MAX_CHARS", 12000),
        llm_batch_max_items=_integer(env, "LLM_BATCH_MAX_ITEMS", 20),
        llm_concurrency=_integer(env, "LLM_CONCURRENCY", 4),
        scan_batch_items=_integer(env, "SCAN_BATCH_ITEMS", 200),
        scan_max_bytes=_integer(env, "SCAN_MAX_BYTES", 5242880),
        evidence_dir=_optional(env, "EVIDENCE_DIR") or "./var/evidence",
        evidence_max_bytes=_integer(env, "EVIDENCE_MAX_BYTES", 10485760),
        slack_signing_secret_ref=_optional(env, "SLACK_SIGNING_SECRET"),
        slack_bot_token_ref=_optional(env, "SLACK_BOT_TOKEN"),
        github_webhook_secret_ref=_optional(env, "GITHUB_WEBHOOK_SECRET"),
        github_api_url=_optional(env, "GITHUB_API_URL") or "https://api.github.com",
        google_service_account_ref=_optional(env, "GOOGLE_SERVICE_ACCOUNT"),
        google_delegated_admin=_optional(env, "GOOGLE_DELEGATED_ADMIN"),
        map_dispatch_seconds=_integer(env, "MAP_DISPATCH_SECONDS", 2),
        sweep_shard_size=_integer(env, "SWEEP_SHARD_SIZE", 100),
        public_base_url=_optional(env, "PUBLIC_BASE_URL"),
        smtp_host=_optional(env, "SMTP_HOST"),
        smtp_port=_integer(env, "SMTP_PORT", 587),
        smtp_username=_optional(env, "SMTP_USERNAME"),
        smtp_password_ref=_optional(env, "SMTP_PASSWORD"),
        smtp_from=_optional(env, "SMTP_FROM"),
        smtp_starttls=_boolean(env, "SMTP_STARTTLS", True),
        worker_concurrency=_integer(env, "WORKER_CONCURRENCY", 8),
        worker_poll_seconds=_number(env, "WORKER_POLL_SECONDS", 1.0),
        web_dist=_optional(env, "WEB_DIST"),
        log_level=_optional(env, "LOG_LEVEL") or "INFO",
        fingerprint_keys=fingerprint_keys,
        fingerprint_active_version=fingerprint_active_version,
        internal_domains=_comma_values(env, "INTERNAL_DOMAINS", lowercase=True),
        owner_routing=_boolean(env, "OWNER_ROUTING", False),
        default_team=_optional(env, "DEFAULT_TEAM"),
        digest_cron=_digest_cron(env),
        health_recipients=_comma_values(env, "HEALTH_RECIPIENTS"),
        health_failure_threshold=_integer(env, "HEALTH_FAILURE_THRESHOLD", 3),
        alert_unscannable_public=_boolean(env, "ALERT_UNSCANNABLE_PUBLIC", True),
        risk_weights=parse_risk_weights(_read(env, "RISK_WEIGHTS")),
        oidc=load_oidc_settings(env),
        ui_token_login=_boolean(env, "UI_TOKEN_LOGIN", True),
        escalating_min_findings=_bounded_integer(env, "ESCALATING_MIN_FINDINGS", 10, 100000),
        escalating_factor=_bounded_integer(env, "ESCALATING_FACTOR", 3, 100),
        weekly_report_cron=_weekly_report_cron(env),
        data_overdue_cron=_data_overdue_cron(env),
        github_issues_token_ref=_optional(env, "GITHUB_ISSUES_TOKEN"),
        github_issues_repos=_github_issues_repos(env),
    )


def _bounded_integer(env: EnvSource, name: str, default: int, maximum: int) -> int:
    """Read an integer constrained to the inclusive range from one to maximum."""
    value: int = _integer(env, name, default)
    if value > maximum:
        raise ConfigError(f"TELESCREEN_{name} is outside the allowed range")
    return value
