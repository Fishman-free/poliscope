from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base


class ScientificEventModel(Base):
    __tablename__ = "scientific_events"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    evidence_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    finding_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_event_idempotency"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_event_sequence"),
    )


class ProcessStreamModel(Base):
    """Process-only, real-time stream of a running task.

    Distinct from ``scientific_events`` on purpose: the ledger is the durable,
    idempotent, replayable scientific record, and token-level noise must never
    pollute it. This table is the live wire -- model token deltas, tool calls,
    seat progress -- written by the worker as it runs and read by the API's
    SSE process endpoint. It is deliberately *not* replay-guaranteed: a
    reconnecting client re-reads from the start and deduplicates by ``seq``,
    and a finished task's stream may be truncated by retention. Nothing here
    is ever admitted to the Evidence Graph (CLAUDE.md 5.1).
    """

    __tablename__ = "process_stream"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # Per-task monotonic sequence, assigned by the writer (a task is executed
    # by exactly one worker at a time, so there is no concurrent allocation).
    #
    # Deliberately NO foreign key on task_id (migration 0016): the worker's
    # claim holds the task row with SELECT ... FOR UPDATE for the whole run,
    # and a Postgres FK check takes a KEY SHARE lock on the parent row -- so
    # every INSERT here would block until the deliberation transaction
    # commits, freezing the live view and deadlocking the run. The stream is
    # ephemeral by design; orphan rows are harmless and bounded by retention.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        sa.UniqueConstraint("task_id", "seq", name="uq_process_stream_task_seq"),
        # Mirrors migration 0015's ix_process_stream_task_seq: the stream's
        # per-task replay (SELECT ... WHERE task_id = ? ORDER BY seq) scans
        # this index. Declared here so autogenerate does not see drift.
        sa.Index("ix_process_stream_task_seq", "task_id", "seq"),
    )


class EventAuditModel(Base):
    __tablename__ = "event_audits"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scientific_events.id"),
        nullable=False,
        index=True,
    )
    gate_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GraphNodeModel(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    node_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GraphEdgeModel(Base):
    __tablename__ = "graph_edges"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    source_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    target_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    edge_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        sa.UniqueConstraint(
            "task_id", "source_node_id", "target_node_id", "edge_type",
            name="uq_graph_edge",
        ),
    )


class ProjectionCheckpointModel(Base):
    __tablename__ = "projection_checkpoints"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        unique=True,
    )
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


