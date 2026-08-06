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
    # Per-task model configuration: TaskModelConfig.model_dump(mode="json"),
    # set at creation when the researcher supplies their own endpoint. None
    # means "use the deployment's configured model gateway". The api_key here
    # is never returned by any read endpoint (CLAUDE.md 16).
    model_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Knowledge base whose documents the council treats as Level A
    # user-provided sources (migration 0010). None is the ordinary case --
    # most tasks retrieve from the open web, not from the researcher's own
    # collection.
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id"),
        nullable=True,
        index=True,
    )
    # Owning account (migration 0012). None only for tasks created before
    # accounts existed; those are visible to no one (honest "no owner yet").
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    # Skill ids the researcher enabled for this task (migration 0013). The
    # worker reads them to inject the downloaded SKILL.md texts into the
    # council's prompts as explicitly non-evidence process context.
    skill_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )
    # Output language the council must write in (migration 0017), resolved
    # from the researcher's question language at task creation ("auto" is
    # replaced by the detection result before the row is stored, so the
    # worker never has to guess). One of zh-Hans / zh-Hant / en.
    output_language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto"
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
