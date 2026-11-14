"""Inventory data cutover CLI without constructing runtime services or channel clients."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from sqlalchemy.exc import SQLAlchemyError

from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.db.transfer import TransferReport, transfer_inventory


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Expose explicit copy/verify operations and the authoritative rollback direction."""
    parser: argparse.ArgumentParser = commands.add_parser("inventory-store")
    actions: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(
        dest="inventory_store_command", required=True
    )
    for name in ("copy", "verify"):
        action: argparse.ArgumentParser = actions.add_parser(name)
        action.add_argument("--direction", choices=("split", "merge"), default="split")
        action.add_argument("--writers-stopped", action="store_true")


def run(settings: TelescreenSettings, arguments: argparse.Namespace) -> int:
    """Use configured URLs; never print credentials, values or perform schema upgrades."""
    if settings.database_url is None or settings.inventory_database_url is None:
        raise ConfigError("inventory transfer requires main and inventory database URLs")
    source: str = settings.database_url
    target: str = settings.inventory_database_url
    if arguments.direction == "merge":
        source, target = target, source
    try:
        report: TransferReport = asyncio.run(
            transfer_inventory(
                source,
                target,
                writers_stopped=arguments.writers_stopped,
                replace=arguments.direction == "merge",
                verify_only=arguments.inventory_store_command == "verify",
            )
        )
    except SQLAlchemyError:
        raise RuntimeError("inventory transfer database operation failed") from None
    print(json.dumps(asdict(report), separators=(",", ":")))
    return 0
