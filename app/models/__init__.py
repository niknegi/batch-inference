from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BatchStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ChunkStatus(enum.StrEnum):
    pending = "pending"
    leased = "leased"
    succeeded = "succeeded"
    failed = "failed"


class WebhookStatus(enum.StrEnum):
    pending = "pending"
    delivered = "delivered"
    dead = "dead"
    none = "none"


def _enum_column(enum_cls: type[enum.Enum], name: str):
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda obj: [e.value for e in obj],
    )


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    status: Mapped[BatchStatus] = mapped_column(
        _enum_column(BatchStatus, "batch_status"),
        default=BatchStatus.pending,
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Sum of per-chunk retries: max(0, attempts - 1) across chunks
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    prompts_key: Mapped[str] = mapped_column(String(512), nullable=False)
    result_prefix: Mapped[str] = mapped_column(String(512), nullable=False)
    results_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    webhook_status: Mapped[WebhookStatus] = mapped_column(
        _enum_column(WebhookStatus, "webhook_status"),
        default=WebhookStatus.none,
        nullable=False,
    )
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rate_limit_rps: Mapped[float] = mapped_column(Float, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)

    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[list[BatchChunk]] = relationship(
        "BatchChunk", back_populates="batch", cascade="all, delete-orphan"
    )


class BatchChunk(Base):
    __tablename__ = "batch_chunks"
    __table_args__ = (UniqueConstraint("batch_id", "chunk_index", name="uq_batch_chunk_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ChunkStatus] = mapped_column(
        _enum_column(ChunkStatus, "chunk_status"),
        default=ChunkStatus.pending,
        nullable=False,
        index=True,
    )
    result_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ok_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    batch: Mapped[Batch] = relationship("Batch", back_populates="chunks")
