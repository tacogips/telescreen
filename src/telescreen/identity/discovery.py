"""OIDC discovery metadata and JWKS cache with rotation and stale fallback."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import SplitResult, urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.vocabulary import ErrorCode

logger: logging.Logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS: float = 5.0
REFETCH_INTERVAL: timedelta = timedelta(seconds=30)
STALE_LIMIT: timedelta = timedelta(seconds=3600)

type KeyEntry = tuple[str, str | None, str | None, str | None]


@dataclass(frozen=True)
class DiscoveryDocument:
    """Discovery fields used for token verification and the UI login flow."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None


@dataclass(frozen=True)
class CachedKey:
    """One JWKS entry kept as canonical JSON text with its selection metadata."""

    kid: str
    jwk_json: str
    kty: str | None
    use: str | None
    alg: str | None


class _FetchFailedError(Exception):
    """Internal marker for a failed or rejected IdP fetch, carrying no details."""


def _origin(url: str) -> tuple[str, str, int | None]:
    """Return the scheme, lowercase host and effective port of a URL."""
    parsed: SplitResult = urlsplit(url)
    default_port: int | None = None
    if parsed.scheme == "http":
        default_port = 80
    elif parsed.scheme == "https":
        default_port = 443
    try:
        port: int | None = parsed.port
    except ValueError:
        raise _FetchFailedError from None
    return parsed.scheme, (parsed.hostname or "").lower(), port or default_port


def _optional_member(value: JsonValue, name: str) -> str | None:
    """Return an optional string member of a JWK object."""
    member: JsonValue | None = value.get(name)
    return member.as_str() if member is not None else None


def _parse_jwks(content: bytes) -> tuple[CachedKey, ...]:
    """Parse a JWKS document, keeping each keyed JWK as canonical JSON text."""
    try:
        document: JsonValue = parse_json(content)
    except TelescreenError:
        raise _FetchFailedError from None
    keys_value: JsonValue | None = document.get("keys")
    items: tuple[JsonValue, ...] | None = keys_value.as_list() if keys_value is not None else None
    if items is None:
        raise _FetchFailedError
    keys: list[CachedKey] = []
    for item in items:
        if item.kind is not JsonKind.OBJECT:
            continue
        kid: str | None = _optional_member(item, "kid")
        if not kid:
            continue
        keys.append(
            CachedKey(
                kid=kid,
                jwk_json=dump_json(item),
                kty=_optional_member(item, "kty"),
                use=_optional_member(item, "use"),
                alg=_optional_member(item, "alg"),
            )
        )
    return tuple(keys)


@dataclass
class OidcProviderClient:
    """Fetch and cache discovery metadata and signing keys for one issuer."""

    http: httpx.AsyncClient
    settings: OidcSettings
    clock: Clock
    _document: DiscoveryDocument | None = field(default=None, init=False, repr=False)
    _document_at: datetime | None = field(default=None, init=False, repr=False)
    _keys: tuple[CachedKey, ...] | None = field(default=None, init=False, repr=False)
    _keys_at: datetime | None = field(default=None, init=False, repr=False)
    _refetch_at: datetime | None = field(default=None, init=False, repr=False)
    _attempt_at: datetime | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def _now(self) -> datetime:
        """Return the current UTC time from the injected clock."""
        return ensure_utc(self.clock.now())

    def _fresh(self, fetched_at: datetime | None, now: datetime) -> bool:
        """Return whether a cached value is within the configured lifetime."""
        return fetched_at is not None and now < fetched_at + self._lifetime()

    def _usable_stale(self, fetched_at: datetime | None, now: datetime) -> bool:
        """Return whether an expired cached value may still serve during an outage."""
        return fetched_at is not None and now < fetched_at + self._lifetime() + STALE_LIMIT

    def _lifetime(self) -> timedelta:
        """Return the metadata and JWKS cache lifetime."""
        return timedelta(seconds=self.settings.jwks_cache_seconds)

    async def _get(self, url: str) -> bytes:
        """GET one IdP document with a fixed timeout and no detail on failure."""
        try:
            response: httpx.Response = await self.http.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            logger.warning("oidc provider request failed")
            raise _FetchFailedError from None
        if response.is_error:
            logger.warning("oidc provider returned an error status")
            raise _FetchFailedError
        return response.content

    async def _fetch_document(self) -> DiscoveryDocument:
        """Fetch discovery metadata and enforce issuer and JWKS origin rules."""
        issuer: str = self.settings.issuer
        content: bytes = await self._get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
        try:
            document: DiscoveryDocument = TypeAdapter(DiscoveryDocument).validate_json(content)
        except ValidationError:
            logger.warning("oidc discovery document is invalid")
            raise _FetchFailedError from None
        if document.issuer != issuer:
            logger.warning("oidc discovery issuer mismatch")
            raise _FetchFailedError
        if _origin(document.jwks_uri) != _origin(issuer):
            logger.warning("oidc discovery jwks_uri is not same-origin")
            raise _FetchFailedError
        return document

    async def _document_locked(self, now: datetime) -> DiscoveryDocument:
        """Return cached or refreshed metadata; the caller holds the lock."""
        if self._document is not None and self._fresh(self._document_at, now):
            return self._document
        try:
            document: DiscoveryDocument = await self._fetch_document()
        except _FetchFailedError:
            if self._document is not None and self._usable_stale(self._document_at, now):
                return self._document
            raise TelescreenError(
                ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable"
            ) from None
        self._document = document
        self._document_at = now
        return document

    async def metadata(self) -> DiscoveryDocument:
        """Return discovery metadata, fetching it when absent or expired."""
        async with self._lock:
            return await self._document_locked(self._now())

    async def _refresh_keys(self, now: datetime) -> None:
        """Refetch the JWKS, keeping a usable stale set when the fetch fails."""
        self._attempt_at = now
        document: DiscoveryDocument = await self._document_locked(now)
        try:
            keys: tuple[CachedKey, ...] = _parse_jwks(await self._get(document.jwks_uri))
        except _FetchFailedError:
            if self._keys is not None and self._usable_stale(self._keys_at, now):
                return
            raise TelescreenError(
                ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable"
            ) from None
        self._keys = keys
        self._keys_at = now

    def _attempted_recently(self, now: datetime) -> bool:
        """Return whether any JWKS fetch was attempted within the refetch interval."""
        return self._attempt_at is not None and now < self._attempt_at + REFETCH_INTERVAL

    def _find(self, kid: str) -> KeyEntry | None:
        """Return a cached key entry by key id."""
        if self._keys is None:
            return None
        found: CachedKey | None = next((key for key in self._keys if key.kid == kid), None)
        if found is None:
            return None
        return found.jwk_json, found.kty, found.use, found.alg

    async def key_for(self, kid: str) -> KeyEntry | None:
        """Return the JWK JSON, kty, use and alg for a key id, refetching on rotation."""
        async with self._lock:
            now: datetime = self._now()
            fetched: bool = False
            if not self._fresh(self._keys_at, now) and not (
                self._attempted_recently(now) and self._usable_stale(self._keys_at, now)
            ):
                await self._refresh_keys(now)
                fetched = True
            found: KeyEntry | None = self._find(kid)
            if found is not None or fetched:
                return found
            if self._refetch_at is not None and now < self._refetch_at + REFETCH_INTERVAL:
                return None
            self._refetch_at = now
            await self._refresh_keys(now)
            return self._find(kid)
