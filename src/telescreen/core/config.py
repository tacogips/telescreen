"""Typed environment configuration for Telescreen."""

from dataclasses import dataclass
from typing import TypeVar

from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    DetectorKind,
    DetectorMode,
    DlpPolicy,
    ManagedLlmProvider,
)

_EnumType = TypeVar("_EnumType", bound=str)


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
    return TelescreenSettings(
        database_url=_optional(env, "DATABASE_URL"),
        fingerprint_key_ref=_optional(env, "FINGERPRINT_KEY"),
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
    )
