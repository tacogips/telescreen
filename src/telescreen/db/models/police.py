"""Police worker registration and task database models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class PoliceWorkerRow(Base, kw_only=True):
    """A registered Police worker and its supported capabilities."""

    __tablename__ = "police_workers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    contract_version: Mapped[str] = mapped_column(String(32))
    task_types: Mapped[str] = mapped_column(Text)
    labels: Mapped[str] = mapped_column(Text)
    concurrency: Mapped[int] = mapped_column(Integer)
    endpoint_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PoliceTaskRow(Base, kw_only=True):
    """A leased Police task with masked JSON input and result."""

    __tablename__ = "police_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(128))
    labels: Mapped[str] = mapped_column(Text)
    input_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    callback_token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    callback_token_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    outcome: Mapped[str | None] = mapped_column(String(32), default=None)
    step_run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    collector_config_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
