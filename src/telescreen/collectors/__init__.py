"""Source-agnostic collector services and durable task handlers."""

from telescreen.collectors.codec import (
    CursorState,
    cursor_from_json,
    cursor_to_json,
    selector_from_json,
    selector_to_json,
)
from telescreen.collectors.police import PoliceCollector
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.rescanner import CollectorRescanner
from telescreen.collectors.scheduler import CollectorScheduleProvider
from telescreen.collectors.service import CollectorService, WebhookOutcome
from telescreen.collectors.sweep import CollectorSweeperImpl
from telescreen.collectors.tasks import CollectorScanHandler, WatchRenewHandler

__all__: list[str] = [
    "CollectorRegistry",
    "CollectorRepository",
    "CollectorRescanner",
    "CollectorScanHandler",
    "CollectorScheduleProvider",
    "CollectorService",
    "CollectorSweeperImpl",
    "CursorState",
    "PoliceCollector",
    "ScanJobStoreImpl",
    "WatchRenewHandler",
    "WebhookOutcome",
    "cursor_from_json",
    "cursor_to_json",
    "selector_from_json",
    "selector_to_json",
]
