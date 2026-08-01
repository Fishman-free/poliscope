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


