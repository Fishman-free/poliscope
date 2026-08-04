from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base


class ResearchTaskModel(Base):
    __tablename__ = "research_tasks"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    wall_clock_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    model_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    tool_call_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    source_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    user_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # Plan phase 8: CouncilCheckpoint.model_dump(mode="json"), set only while
    # status is AWAITING_COUNCIL_INPUT. Nullable because every task before and
    # after that one checkpoint has nothing to store here -- this is not a
    # general-purpose snapshot column (see CLAUDE.md 17 scope note in the
    # plan), only the one fixed BLINDSPOT_BOUNTY -> JOINT_MODELING gate.
    council_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )


class ResearchScopeModel(Base):
    __tablename__ = "research_scopes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    populations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    regions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_until: Mapped[date] = mapped_column(Date, nullable=False)
    evidence_priorities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    allow_preprints: Mapped[bool] = mapped_column(nullable=False)


class AtomicClaimModel(Base):
    __tablename__ = "atomic_claims"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    falsification_condition: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
