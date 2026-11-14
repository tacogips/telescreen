"""Programmatic Alembic upgrade entry point."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(database_url: str, revision: str = "head") -> None:
    """Upgrade the configured database to the requested Alembic revision."""
    migration_directory: Path = Path(__file__).parent / "migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)
