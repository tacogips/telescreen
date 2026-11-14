"""Command-line interface for the Reporting Bureau."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from telescreen.assistant.mcp_server import run_stdio
from telescreen.assistant.tools import AssistantTools
from telescreen.cli_v12 import create_token, member_set_role, notify_test
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope, ScanTrigger, SourceFormat
from telescreen.db.models import PoliceWorkerRow
from telescreen.masking.log_masking import configure_logging, install_excepthook
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import CollectorConfig
from telescreen.search.reindex import ReindexCounts, reindex_all
from telescreen.wiring import Container, build_container, close_container
from telescreen.workflow.dsl.loader import WorkflowValidationError, load_new_workflow
from telescreen.workflow.dsl.raw_scan import source_has_raw_values


class _Environment(EnvSource):
    """Expose process environment using the typed configuration protocol."""

    def get(self, key: str, /) -> str | None:
        """Return one process environment value."""
        return os.environ.get(key)


def _parser() -> argparse.ArgumentParser:
    """Create the documented command tree."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="telescreen")
    commands: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(
        dest="command"
    )
    commands.add_parser("serve").add_argument("--host", default="0.0.0.0")
    commands.choices["serve"].add_argument("--port", type=int, default=8000)
    inventory: argparse.ArgumentParser = commands.add_parser("inventory-serve")
    inventory.add_argument("--host", default="0.0.0.0")
    inventory.add_argument("--port", type=int, default=8001)
    worker: argparse.ArgumentParser = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    migrate: argparse.ArgumentParser = commands.add_parser("migrate")
    migrate.add_argument("--revision", default="head")
    migrate.add_argument("--store", choices=("main", "inventory", "notification"))
    migrate.add_argument("--adopt", action="store_true")
    migrate.add_argument("--writers-stopped", action="store_true")
    from telescreen.cli_inventory_store import add_commands

    add_commands(commands)
    workflow: argparse.ArgumentParser = commands.add_parser("workflow")
    workflow_commands: argparse._SubParsersAction[argparse.ArgumentParser] = (
        workflow.add_subparsers(dest="workflow_command")
    )
    validate: argparse.ArgumentParser = workflow_commands.add_parser("validate")
    validate.add_argument("files", nargs="+")
    collector: argparse.ArgumentParser = commands.add_parser("collector")
    collector_commands: argparse._SubParsersAction[argparse.ArgumentParser] = (
        collector.add_subparsers(dest="collector_command")
    )
    run: argparse.ArgumentParser = collector_commands.add_parser("run")
    run.add_argument("name")
    run.add_argument("--watch", action="store_true")
    sweep: argparse.ArgumentParser = collector_commands.add_parser("sweep")
    sweep.add_argument("name")
    commands.add_parser("police").add_subparsers(dest="police_command").add_parser("list")
    token: argparse.ArgumentParser = commands.add_parser("token")
    token_commands: argparse._SubParsersAction[argparse.ArgumentParser] = token.add_subparsers(
        dest="token_command"
    )
    create_token: argparse.ArgumentParser = token_commands.add_parser("create")
    create_token.add_argument("--label", required=True)
    create_token.add_argument("--scopes", default="read,write")
    create_token.add_argument("--member")
    member: argparse.ArgumentParser = commands.add_parser("member")
    member_commands: argparse._SubParsersAction[argparse.ArgumentParser] = member.add_subparsers(
        dest="member_command"
    )
    set_role: argparse.ArgumentParser = member_commands.add_parser("set-role")
    set_role.add_argument("handle")
    set_role.add_argument("role", choices=("admin", "responder", "viewer", "none"))
    notify: argparse.ArgumentParser = commands.add_parser("notify")
    notify_commands: argparse._SubParsersAction[argparse.ArgumentParser] = notify.add_subparsers(
        dest="notify_command"
    )
    notify_test_parser: argparse.ArgumentParser = notify_commands.add_parser("test")
    notify_test_parser.add_argument("--channel", choices=("email", "slack"), required=True)
    notify_test_parser.add_argument("--to", dest="recipient", required=True)
    notify_test_parser.add_argument("--template")
    projections: argparse.ArgumentParser = commands.add_parser("projections")
    projections.add_subparsers(dest="projections_command").add_parser("resync")
    search: argparse.ArgumentParser = commands.add_parser("search")
    search.add_subparsers(dest="search_command").add_parser("reindex")
    commands.add_parser("mcp")
    return parser


def _validate_files(paths: Sequence[str], redactor: Redactor) -> int:
    """Validate workflow files offline and print only paths and error codes."""
    failed: bool = False
    for name in paths:
        path: Path = Path(name)
        try:
            source: str = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            print(f"{name} file_unavailable")
            failed = True
            continue
        if source_has_raw_values(redactor, source):
            print(f"{name} raw_value_detected")
            failed = True
            continue
        source_format: SourceFormat = (
            SourceFormat.JSON if path.suffix.lower() == ".json" else SourceFormat.YAML
        )
        try:
            load_new_workflow(source, source_format)
        except WorkflowValidationError as error:
            for issue in error.issues:
                print(f"{name}{issue.path} {issue.code}")
            failed = True
    return 2 if failed else 0


def _build_cli_container(settings: TelescreenSettings, env: EnvSource) -> Container:
    """Build a container and mark the CLI-created HTTP client as owned."""
    from dataclasses import replace

    client: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, env, client)
    return replace(container, owns_http=True)


def _serve(settings: TelescreenSettings, env: EnvSource, host: str, port: int) -> int:
    """Run the HTTP API and optional UI with masked uvicorn logging."""
    container: Container
    container = _build_cli_container(settings, env)
    from telescreen.api.app import create_app

    try:
        app: FastAPI = create_app(container)
    except ConfigError:
        asyncio.run(close_container(container))
        raise
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


async def _run_and_close(container: Container, arguments: argparse.Namespace) -> int:
    """Run one command and close its resources on the same event loop.

    Pooled database connections belong to the loop that opened them, so closing the
    container from a second ``asyncio.run`` fails on PostgreSQL (asyncpg).
    """
    try:
        return await _run_async_command(container, arguments)
    finally:
        await close_container(container)


async def _run_async_command(container: Container, arguments: argparse.Namespace) -> int:
    """Run supported asynchronous utility commands."""
    if arguments.command == "worker":
        if arguments.once:
            await container.worker.run_until_idle()
        else:
            await container.worker.run(asyncio.Event())
        return 0
    if arguments.command == "collector":
        async with container.sessions() as session:
            config: CollectorConfig | None = await container.collector.get_by_name(
                session, arguments.name
            )
        if config is None:
            raise ConfigError("collector config not found")
        if arguments.collector_command == "run":
            if arguments.watch:
                async with container.sessions.begin() as session:
                    await container.collector.start_watch(session, config.id)
            async with container.sessions.begin() as session:
                await container.collector.enqueue_run(session, config.id, ScanTrigger.MANUAL)
            await container.worker.run_until_idle()
            print(f"collector={config.name} scan=queued")
            return 0
        if arguments.collector_command == "sweep":
            job_id: str = await container.sweep_launcher.start_collector_sweep(
                config.id, ScanTrigger.MANUAL
            )
            print(f"collector={config.name} sweep={job_id}")
            return 0
        raise ConfigError("collector command is required")
    if arguments.command == "search":
        result: ReindexCounts = await reindex_all(
            container.sessions,
            container.search,
            container.indexer.guard,
            container.audit,
        )
        print(
            f"issues={result.issues} findings={result.findings} "
            f"runs={result.runs} audit={result.audit}"
        )
        return 0
    if arguments.command == "mcp":
        run_stdio(AssistantTools(container.sessions, container.redactor, container.clock))
        return 0
    if arguments.command == "projections" and arguments.projections_command == "resync":
        from telescreen.core.topology import Topology, topology_of

        if topology_of(container.settings) is not Topology.SPLIT:
            print("projections require split mode", file=sys.stderr)
            return 2
        from telescreen.projections.resync import resync

        created: int = await resync(container.sessions, container.clock)
        print(created)
        return 0
    if arguments.command == "notify" and arguments.notify_command == "test":
        from telescreen.core.topology import Topology, topology_of

        if topology_of(container.settings) is Topology.SPLIT:
            print("use telescreen-notification send-test", file=sys.stderr)
            return 2
        return await notify_test(
            container, arguments.channel, arguments.recipient, arguments.template
        )
    if arguments.command == "member" and arguments.member_command == "set-role":
        return await member_set_role(container, arguments.handle, arguments.role)
    if arguments.command == "token":
        scope_names: tuple[str, ...] = tuple(
            part.strip() for part in arguments.scopes.split(",") if part.strip()
        )
        try:
            scopes: frozenset[ApiScope] = frozenset(ApiScope(name) for name in scope_names)
        except ValueError:
            print("invalid token scope", file=sys.stderr)
            return 2
        if not scopes:
            print("at least one token scope is required", file=sys.stderr)
            return 2
        return await create_token(container, arguments.label, scopes, arguments.member)
    if arguments.command == "police":
        async with container.sessions() as session:
            workers: tuple[PoliceWorkerRow, ...] = await container.police_repository.workers(
                session
            )
        for worker in workers:
            heartbeat: str = (
                worker.last_heartbeat_at.isoformat()
                if worker.last_heartbeat_at is not None
                else "never"
            )
            print(f"{worker.id} {worker.name} {worker.mode} {worker.labels} heartbeat={heartbeat}")
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Parse and run a Telescreen command using documented exit codes."""
    redactor: Redactor = Redactor.default()
    configure_logging("INFO", redactor)
    install_excepthook(redactor)
    parser: argparse.ArgumentParser = _parser()
    arguments: argparse.Namespace = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 2
    env: EnvSource = _Environment()
    if arguments.command == "workflow":
        return _validate_files(arguments.files, redactor)
    try:
        settings: TelescreenSettings = load_settings(env)
        configure_logging(settings.log_level, redactor)
        install_excepthook(redactor)
        if arguments.command == "migrate":
            from telescreen.db.migrate import upgrade_configured, upgrade_store
            from telescreen.db.ownership import Store

            selected_store: Store | None = (
                Store(arguments.store)
                if arguments.store is not None
                else Store.MAIN
                if settings.inventory_service_url
                else None
            )
            selected_url: str | None
            if selected_store is Store.NOTIFICATION:
                selected_url = settings.notification_database_url
            elif selected_store is Store.INVENTORY:
                selected_url = settings.inventory_database_url or settings.database_url
            else:
                selected_url = settings.database_url
            if selected_url is None:
                raise ConfigError("selected store database URL is required")
            if arguments.adopt:
                if arguments.store is None or arguments.revision != "head":
                    raise ConfigError("owner adoption requires --store and the baseline head")
                from telescreen.db.adopt import adopt_owner

                adopt_owner(
                    selected_url, Store(arguments.store), writers_stopped=arguments.writers_stopped
                )
            elif selected_store is not None:
                upgrade_store(selected_url, selected_store, arguments.revision)
            else:
                upgrade_configured(
                    selected_url, settings.inventory_database_url, arguments.revision
                )
            return 0
        if arguments.command == "inventory-serve":
            from telescreen.inventory_service.app import (
                build_inventory_runtime,
                create_inventory_app,
            )

            inventory_http: httpx.AsyncClient = httpx.AsyncClient()
            try:
                inventory_app: FastAPI = create_inventory_app(
                    build_inventory_runtime(settings, env, inventory_http), inventory_http
                )
            except Exception:
                asyncio.run(inventory_http.aclose())
                raise
            uvicorn.run(inventory_app, host=arguments.host, port=arguments.port, log_config=None)
            return 0
        if arguments.command == "serve":
            return _serve(settings, env, arguments.host, arguments.port)
        if arguments.command == "inventory-store":
            from telescreen.cli_inventory_store import run

            return run(settings, arguments)
        if arguments.command == "notification-store":
            from telescreen.cli_inventory_store import run_notification

            return run_notification(settings, arguments)
        container: Container
        container = _build_cli_container(settings, env)
        return asyncio.run(_run_and_close(container, arguments))
    except ConfigError as error:
        print(error.detail, file=sys.stderr)
        return 3
    except (TelescreenError, OSError, RuntimeError) as error:
        safe_detail: str = (
            error.detail if isinstance(error, TelescreenError) else "runtime command failed"
        )
        print(redactor.redact(safe_detail).text, file=sys.stderr)
        return 1
