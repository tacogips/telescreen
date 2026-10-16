"""Command line entry point for serving and managing Notification storage."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telescreen.audit.chain import NOTIFICATION_AUDIT_MODELS
from telescreen.audit.export import export_chain
from telescreen.audit.verify import ChainVerification, verify_chain
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import NotificationChannel
from telescreen.db.adopt import adopt_owner
from telescreen.db.migrate import upgrade_store
from telescreen.db.ownership import Store
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.masking.log_masking import configure_logging, install_excepthook
from telescreen.masking.redact import Redactor
from telescreen.notification_service.app import (
    NotificationRuntime,
    build_notification_runtime,
    create_notification_app,
)
from telescreen.notifications.tester import NotificationServiceTester
from telescreen.ports.notifications import TestOutcome


class ProcessEnvironment(EnvSource):
    """Read configuration values from the process environment."""

    def get(self, key: str, /) -> str | None:
        """Return the requested environment value."""
        return os.environ.get(key)


def _parser() -> argparse.ArgumentParser:
    """Build the notification-only command parser."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="telescreen-notification")
    commands: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(
        dest="command", required=True
    )
    serve: argparse.ArgumentParser = commands.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8002)
    migrate: argparse.ArgumentParser = commands.add_parser("migrate")
    migrate.add_argument("--revision", default="head")
    migrate.add_argument("--adopt", action="store_true")
    migrate.add_argument("--writers-stopped", action="store_true")
    test: argparse.ArgumentParser = commands.add_parser("send-test")
    test.add_argument("--template", default="integration_test")
    test.add_argument(
        "--channel",
        choices=tuple(channel.value for channel in NotificationChannel),
        required=True,
    )
    test.add_argument("--to", required=True)
    audit: argparse.ArgumentParser = commands.add_parser("audit")
    audit_commands: argparse._SubParsersAction[argparse.ArgumentParser] = audit.add_subparsers(
        dest="audit_command", required=True
    )
    audit_commands.add_parser("verify")
    audit_commands.add_parser("export")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one isolated Notification process command."""
    redactor: Redactor = Redactor.default()
    configure_logging("INFO", redactor)
    install_excepthook(redactor)
    arguments: argparse.Namespace = _parser().parse_args(argv)
    env: ProcessEnvironment = ProcessEnvironment()
    try:
        settings: TelescreenSettings = load_settings(env)
        configure_logging(settings.log_level, redactor)
        if arguments.command == "migrate":
            if settings.notification_database_url is None:
                raise ConfigError("notification database URL is required")
            if arguments.adopt:
                adopt_owner(
                    settings.notification_database_url,
                    Store.NOTIFICATION,
                    writers_stopped=arguments.writers_stopped,
                )
            upgrade_store(
                settings.notification_database_url, Store.NOTIFICATION, arguments.revision
            )
            return 0
        if arguments.command == "audit":
            return _audit(settings, arguments.audit_command)
        http: httpx.AsyncClient = httpx.AsyncClient()
        runtime: NotificationRuntime = build_notification_runtime(settings, env, http)
        if arguments.command == "serve":
            uvicorn.run(
                create_notification_app(runtime, http),
                host=arguments.host,
                port=arguments.port,
                log_config=None,
            )
            return 0
        if arguments.command == "send-test":
            return asyncio.run(
                _send_test(
                    runtime.tester,
                    NotificationChannel(arguments.channel),
                    arguments.to,
                    arguments.template,
                )
            )
        return 2
    except ConfigError as error:
        print(error.detail, file=sys.stderr)
        return 3
    except (OSError, RuntimeError, TelescreenError) as error:
        print(
            error.detail if isinstance(error, TelescreenError) else "notification command failed",
            file=sys.stderr,
        )
        return 1


async def _send_test(
    tester: NotificationServiceTester,
    channel: NotificationChannel,
    member_id: str,
    template: str,
) -> int:
    """Send a configured channel test and report only its safe outcome."""
    outcome: TestOutcome = await tester.send_test(channel, member_id, template)
    if outcome.ok:
        print("ok")
        return 0
    print(
        outcome.error_code.value if outcome.error_code is not None else "integration_unconfigured"
    )
    return 1


def _audit(settings: TelescreenSettings, command: str) -> int:
    """Verify or export the Notification-owned audit chain."""
    if settings.notification_database_url is None:
        raise ConfigError("notification database URL is required")
    engine: AsyncEngine = create_engine(settings.notification_database_url)
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)

    async def run() -> int:
        try:
            async with sessions() as session:
                if command == "verify":
                    result: ChainVerification = await verify_chain(
                        session, NOTIFICATION_AUDIT_MODELS
                    )
                    print(f"ok={str(result.ok).lower()} checked={result.checked}")
                    return 0 if result.ok else 1
                async for line in export_chain(session, NOTIFICATION_AUDIT_MODELS):
                    print(line)
            return 0
        finally:
            await engine.dispose()

    return asyncio.run(run())
