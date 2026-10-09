"""Durable map-reduce job, partition and shard models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class MapJobRow(Base, kw_only=True):
    """A durable map-reduce sweep execution."""

    __tablename__ = "map_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    origin: Mapped[str] = mapped_column(String(32))
    step_run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    collector_config_id: Mapped[str] = mapped_column(String(36), index=True)
    spec_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    partition_done: Mapped[bool] = mapped_column(Boolean, default=False)
    max_concurrency: Mapped[int] = mapped_column(Integer)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MapPartitionRow(Base, kw_only=True):
    """A cursor-bearing corpus partition for a map job."""

    __tablename__ = "map_partitions"
    __table_args__ = (
        UniqueConstraint("map_job_id", "corpus_key", name="uq_map_partition_corpus"),
        Index("ix_map_partitions_job_status", "map_job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    map_job_id: Mapped[str] = mapped_column(String(36), index=True)
    corpus_key: Mapped[str] = mapped_column(String(256))
    cursor: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    police_task_id: Mapped[str | None] = mapped_column(String(36), default=None)


class MapItemRow(Base, kw_only=True):
    """One idempotent map shard and its count-only summary."""

    __tablename__ = "map_items"
    __table_args__ = (
        UniqueConstraint("map_job_id", "shard_key", name="uq_map_item_shard"),
        Index("ix_map_items_job_status", "map_job_id", "status"),
        Index("ix_map_items_police_task", "police_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    map_job_id: Mapped[str] = mapped_column(String(36), index=True)
    shard_key: Mapped[str] = mapped_column(String(256))
    descriptor_json: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    summary_json: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    police_task_id: Mapped[str | None] = mapped_column(String(36), default=None)
    scan_job_id: Mapped[str | None] = mapped_column(String(36), default=None)
