"""Shared primitive builders used by the application composition root."""

import logging
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import Clock, SystemClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.detection import RuleDetector
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.index_guard import IndexGuard
from telescreen.masking.log_masking import configure_logging
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditLog
from telescreen.search.backend import SearchBackend
from telescreen.search.indexer import DocumentIndexer
from telescreen.search.meili import MeiliSearchBackend
from telescreen.search.memory import InMemorySearchBackend

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimePrimitives:
    """Validated shared resources needed by API and worker processes."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    redactor: Redactor
    fingerprinter: Fingerprinter
    fingerprint_key: SecretValue = field(repr=False)
    audit: AuditLog
    search: SearchBackend
    indexer: DocumentIndexer
    owns_engine: bool
    inventory_sessions: async_sessionmaker[AsyncSession]
    inventory_engine: AsyncEngine | None


def build_primitives(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None,
) -> RuntimePrimitives:
    """Build database, masking, audit and guarded search primitives."""
    if settings.inventory_service_url is not None:
        from telescreen.inventory_service.transport import ServiceHttp
        from telescreen.inventory_service.wiring import service_secret

        ServiceHttp(
            http,
            settings.inventory_service_url,
            service_secret(settings.inventory_service_token_ref, env),
        )
        if inventory_sessions is not None:
            raise ConfigError("remote inventory cannot use an injected inventory session")
    if settings.database_url is None:
        raise ConfigError("TELESCREEN_DATABASE_URL is required")
    configured_keys: tuple[tuple[int, str], ...]
    if settings.fingerprint_keys:
        configured_keys = tuple((item.version, item.ref) for item in settings.fingerprint_keys)
    elif settings.fingerprint_key_ref is not None:
        configured_keys = ((1, settings.fingerprint_key_ref),)
    else:
        raise ConfigError("TELESCREEN_FINGERPRINT_KEY is required")
    selected_clock: Clock = clock or SystemClock()
    redactor: Redactor = Redactor.default()
    resolved_keys: list[tuple[int, SecretValue]] = [
        (version, resolve_secret(ref, env)) for version, ref in configured_keys
    ]
    fingerprint_key: SecretValue = next(secret for version, secret in resolved_keys if version == 1)
    fingerprinter: Fingerprinter = Fingerprinter.from_versions(
        tuple(resolved_keys), settings.fingerprint_active_version
    )
    engine: AsyncEngine = create_engine(settings.database_url)
    selected_sessions: async_sessionmaker[AsyncSession] = sessions or create_sessionmaker(engine)
    inventory_engine: AsyncEngine | None = None
    selected_inventory: async_sessionmaker[AsyncSession] = selected_sessions
    if inventory_sessions is not None:
        selected_inventory = inventory_sessions
    elif (
        settings.inventory_service_url is None
        and settings.inventory_database_url is not None
        and settings.inventory_database_url != settings.database_url
    ):
        inventory_engine = create_engine(settings.inventory_database_url)
        selected_inventory = create_sessionmaker(inventory_engine)
    audit: AuditLogService = AuditLogService(selected_clock, redactor)
    backend: SearchBackend
    if settings.meili_url is None:
        logger.warning("meilisearch is not configured; using in-memory search")
        backend = InMemorySearchBackend()
    else:
        key: SecretValue | None = (
            resolve_secret(settings.meili_api_key_ref, env) if settings.meili_api_key_ref else None
        )
        backend = MeiliSearchBackend(http, settings.meili_url, key)
    indexer: DocumentIndexer = DocumentIndexer(
        selected_sessions,
        backend,
        IndexGuard(RuleDetector()),
        audit,
        selected_clock,
    )
    configure_logging(settings.log_level, redactor)
    return RuntimePrimitives(
        engine,
        selected_sessions,
        selected_clock,
        redactor,
        fingerprinter,
        fingerprint_key,
        audit,
        backend,
        indexer,
        True,
        selected_inventory,
        inventory_engine,
    )
