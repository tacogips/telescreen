"""Built-in collector construction for the Telescreen composition root."""

import hashlib
import hmac
from dataclasses import dataclass, field, replace
from typing import cast
from urllib.parse import urlsplit

import httpx

from telescreen.api.routers.collectors import WebhookSecrets
from telescreen.collectors.github.adapter import GitHubAdapter
from telescreen.collectors.github.client import GitHubApi
from telescreen.collectors.github.sweep import GitHubSweep
from telescreen.collectors.google.adapter import GoogleAdapter, build_google_adapters
from telescreen.collectors.google.admin import AdminApi
from telescreen.collectors.google.auth import (
    GoogleAuth,
    ServiceAccountKey,
    parse_service_account_key,
)
from telescreen.collectors.google.drive import DriveApi
from telescreen.collectors.google.sheets import SheetsApi
from telescreen.collectors.google.sweep import DriveAdapterFactory, GoogleSweep
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.slack.adapter import SlackAdapter
from telescreen.collectors.slack.client import SlackApi
from telescreen.collectors.slack.sweep import SlackSweep
from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.core.vocabulary import CollectorKind
from telescreen.ports.collectors import CollectorAdapter, CollectorConfig, CollectorSecrets


@dataclass(frozen=True)
class SlackClientFactory:
    """Create Slack clients using per-config or configured global credentials."""

    http: httpx.AsyncClient = field(repr=False)
    env: EnvSource = field(repr=False)
    global_token: SecretValue | None = field(repr=False)

    def __call__(self, config: CollectorConfig) -> SlackApi:
        """Resolve the selected credential and construct a Slack client."""
        token: SecretValue | None = (
            resolve_secret(config.credential_ref, self.env)
            if config.credential_ref is not None
            else self.global_token
        )
        if token is None:
            raise ConfigError("slack credential is not configured")
        return SlackApi(self.http, token)


@dataclass(frozen=True)
class GitHubClientFactory:
    """Create GitHub clients from per-collector secret references."""

    http: httpx.AsyncClient = field(repr=False)
    env: EnvSource = field(repr=False)
    api_url: str

    def __call__(self, config: CollectorConfig) -> GitHubApi:
        """Resolve one collector credential and construct a GitHub client."""
        if config.credential_ref is None:
            raise ConfigError("github credential is not configured")
        token: SecretValue = resolve_secret(config.credential_ref, self.env)
        return GitHubApi(self.http, self.api_url, token)


@dataclass(frozen=True)
class GoogleDriveFactory:
    """Create Drive clients when a service-account key is configured."""

    http: httpx.AsyncClient = field(repr=False)
    auth: GoogleAuth | None = field(repr=False)

    def __call__(self) -> DriveApi:
        """Return the shared-auth Drive client or fail with a safe config error."""
        if self.auth is None:
            raise ConfigError("google credential is not configured")
        return DriveApi(self.http, self.auth)


@dataclass(frozen=True)
class GoogleSheetsFactory:
    """Create Sheets clients when a service-account key is configured."""

    http: httpx.AsyncClient = field(repr=False)
    auth: GoogleAuth | None = field(repr=False)

    def __call__(self) -> SheetsApi:
        """Return the shared-auth Sheets client or fail with a safe config error."""
        if self.auth is None:
            raise ConfigError("google credential is not configured")
        return SheetsApi(self.http, self.auth)


@dataclass(frozen=True)
class GoogleSweepFactory:
    """Expose the shared Drive client and registered adapter lookup to sweeps."""

    drive_factory: GoogleDriveFactory
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter]

    def drive(self) -> DriveApi:
        """Create a Drive API client for partitioning and shard work."""
        return self.drive_factory()

    def adapter(self, kind: CollectorKind) -> GoogleAdapter:
        """Return the matching registered Google adapter."""
        for adapter in self.adapters:
            if adapter.kind is kind:
                return adapter
        raise ConfigError("google collector kind is not configured")


def drive_channel_key(fingerprint_key: SecretValue) -> bytes:
    """Derive the stable Drive channel key from the fingerprinting secret."""
    return hmac.new(
        fingerprint_key.value.encode("utf-8"),
        b"telescreen:gdrive-channel-token:v1",
        hashlib.sha256,
    ).digest()


def build_webhook_secrets(
    settings: TelescreenSettings, env: EnvSource, channel_key: bytes
) -> WebhookSecrets:
    """Resolve webhook signing secrets while leaving unset refs fail-closed."""
    slack: str | None = (
        resolve_secret(settings.slack_signing_secret_ref, env).value
        if settings.slack_signing_secret_ref is not None
        else None
    )
    github: str | None = (
        resolve_secret(settings.github_webhook_secret_ref, env).value
        if settings.github_webhook_secret_ref is not None
        else None
    )
    return WebhookSecrets(
        CollectorSecrets(slack, None),
        CollectorSecrets(github, None),
        CollectorSecrets(None, channel_key),
    )


def build_collector_registry(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    clock: Clock,
    channel_key: bytes,
) -> CollectorRegistry:
    """Build and register all built-in collector adapters and sweep capabilities."""
    slack_token: SecretValue | None = (
        resolve_secret(settings.slack_bot_token_ref, env)
        if settings.slack_bot_token_ref is not None
        else None
    )
    slack_factory: SlackClientFactory = SlackClientFactory(http, env, slack_token)
    slack: SlackAdapter = SlackAdapter(slack_factory, settings.scan_max_bytes)
    slack_sweep: SlackSweep = SlackSweep(slack_factory, settings.scan_max_bytes)

    github_factory: GitHubClientFactory = GitHubClientFactory(http, env, settings.github_api_url)
    github_host: str = github_web_host(settings.github_api_url)
    github: GitHubAdapter = GitHubAdapter(github_factory, github_host, settings.scan_max_bytes)
    github_sweep: GitHubSweep = GitHubSweep(github_factory, github_host, settings.scan_max_bytes)

    auth: GoogleAuth | None = None
    if settings.google_service_account_ref is not None:
        try:
            secret: SecretValue = resolve_secret(settings.google_service_account_ref, env)
            key: ServiceAccountKey = parse_service_account_key(secret.value)
        except ConfigError:
            raise ConfigError("google service account is invalid") from None
        auth = GoogleAuth(http, key, clock)
    elif settings.google_delegated_admin is not None:
        raise ConfigError("google delegated admin requires a service account")
    drive_factory: GoogleDriveFactory = GoogleDriveFactory(http, auth)
    sheets_factory: GoogleSheetsFactory = GoogleSheetsFactory(http, auth)
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = build_google_adapters(
        drive_factory,
        sheets_factory,
        clock,
        channel_key,
        settings.public_base_url,
        settings.scan_max_bytes,
    )
    adapters = (
        replace(adapters[0], internal_domains=settings.internal_domains),
        replace(adapters[1], internal_domains=settings.internal_domains),
        replace(adapters[2], internal_domains=settings.internal_domains),
    )
    admin: AdminApi | None = (
        AdminApi(http, auth, settings.google_delegated_admin)
        if auth is not None and settings.google_delegated_admin is not None
        else None
    )
    google_factory: DriveAdapterFactory = GoogleSweepFactory(drive_factory, adapters)
    drive_sweep: GoogleSweep = GoogleSweep(
        adapters[0].kind, google_factory, admin, settings.sweep_shard_size
    )
    docs_sweep: GoogleSweep = GoogleSweep(
        adapters[1].kind, google_factory, admin, settings.sweep_shard_size
    )
    sheets_sweep: GoogleSweep = GoogleSweep(
        adapters[2].kind, google_factory, admin, settings.sweep_shard_size
    )

    registry: CollectorRegistry = CollectorRegistry()
    registry.register(slack, slack_sweep)
    registry.register(adapters[0], drive_sweep)
    registry.register(adapters[1], docs_sweep)
    registry.register(adapters[2], sheets_sweep)
    registry.register(cast(CollectorAdapter, github), github_sweep)
    return registry


def github_web_host(api_url: str) -> str:
    """Return the GitHub host used in stable resource locators."""
    hostname: str = urlsplit(api_url).hostname or ""
    return "github.com" if hostname.casefold() == "api.github.com" else hostname
