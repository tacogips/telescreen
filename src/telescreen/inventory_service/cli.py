"""Inventory-only executable, independent of bureau composition and CLI imports."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

import httpx
import uvicorn
from fastapi import FastAPI

from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import EnvSource
from telescreen.db.ownership import Store
from telescreen.inventory_service.app import build_inventory_runtime, create_inventory_app
from telescreen.masking.log_masking import configure_logging, install_excepthook
from telescreen.masking.redact import Redactor


class ProcessEnvironment(EnvSource):
    """Read the inventory process environment."""

    def get(self, key: str, /) -> str | None:
        """Return only the requested configuration value."""
        return os.environ.get(key)


def main(argv: Sequence[str] | None = None) -> int:
    """Serve or migrate only the inventory owner with no main database configuration."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="telescreen-inventory")
    commands: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(
        dest="command", required=True
    )
    serve: argparse.ArgumentParser = commands.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8001)
    migrate: argparse.ArgumentParser = commands.add_parser("migrate")
    migrate.add_argument("--revision", default="head")
    arguments: argparse.Namespace = parser.parse_args(argv)
    redactor: Redactor = Redactor.default()
    configure_logging("INFO", redactor)
    install_excepthook(redactor)
    try:
        env: ProcessEnvironment = ProcessEnvironment()
        settings: TelescreenSettings = load_settings(env)
        configure_logging(settings.log_level, redactor)
        if arguments.command == "migrate":
            if settings.inventory_database_url is None:
                raise ConfigError("inventory database URL is required")
            from telescreen.db.migrate import upgrade_store

            upgrade_store(settings.inventory_database_url, Store.INVENTORY, arguments.revision)
            return 0
        http: httpx.AsyncClient = httpx.AsyncClient()
        try:
            app: FastAPI = create_inventory_app(build_inventory_runtime(settings, env, http), http)
        except Exception:
            asyncio.run(http.aclose())
            raise
        uvicorn.run(app, host=arguments.host, port=arguments.port, log_config=None)
        return 0
    except ConfigError as error:
        print(error.detail, file=sys.stderr)
        return 3
    except (TelescreenError, OSError, RuntimeError) as error:
        print(
            error.detail if isinstance(error, TelescreenError) else "inventory command failed",
            file=sys.stderr,
        )
        return 1
