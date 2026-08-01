from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.kernel.database import Base


class CouncilRoundModel(Base):
    """One execution of one of the seven protocol phases."""

    __tablename__ = "council_rounds"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Timezone aware: these timestamps are part of the audit trail and must stay
    # comparable across hosts.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ScientistRunModel(Base):
    """One seat's participation in one round."""

    __tablename__ = "scientist_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    round_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("council_rounds.id"),
        nullable=False,
        index=True,
    )
    seat: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when this seat fails. The round degrades rather than aborting.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("round_id", "seat", name="uq_scientist_run_seat"),
    )


class RoundOutputModel(Base):
    """A structured scientific action a seat emitted during a round."""

    __tablename__ = "round_outputs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    round_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("council_rounds.id"),
        nullable=False,
        index=True,
    )
    seat: Mapped[str] = mapped_column(String(64), nullable=False)
    output_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
