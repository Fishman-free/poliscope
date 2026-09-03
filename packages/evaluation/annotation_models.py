"""ORM models for the ForesightBlindspot human-annotation workflow (C9).

These tables (migration 0026) back the missing pipeline that
``packages.evaluation.agreement`` was always written to consume but had no way
to collect:

* ``AnnotationBatchModel`` freezes one rating exercise for one task -- which
  blindspots/claims are under review, who created it, and an optional note.
* ``AnnotationItemModel`` is one rated statement, copied (not referenced live)
  from the evidence graph so a later projection change cannot silently rewrite
  what a human actually rated.
* ``AnnotationLabelModel`` is one rater's nominal label for one item; the
  unique (item_id, rater_name) constraint lets a rater revise by upsert without
  double-counting.

They hold human judgments about the system's output, never formal evidence:
the Graph Projector never reads or writes them and they carry no edge into the
evidence graph (CLAUDE.md 5.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base

# Nominal label vocabulary. Kept tiny on purpose: a three-way relevance call is
# what Blindspot Recall/Precision grading needs, and "unsure" must be a first
# class option so a rater is never forced to guess.
LABEL_RELEVANT = "relevant"
LABEL_NOT_RELEVANT = "not_relevant"
LABEL_UNSURE = "unsure"
ANNOTATION_LABELS = (LABEL_RELEVANT, LABEL_NOT_RELEVANT, LABEL_UNSURE)

REF_KIND_BLINDSPOT = "blindspot"
REF_KIND_CLAIM = "claim"
ANNOTATION_REF_KINDS = (REF_KIND_BLINDSPOT, REF_KIND_CLAIM)


class AnnotationBatchModel(Base):
    __tablename__ = "annotation_batches"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnnotationItemModel(Base):
    __tablename__ = "annotation_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("annotation_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ref_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnnotationLabelModel(Base):
    __tablename__ = "annotation_labels"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "rater_name", name="uq_annotation_label_one_per_rater"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("annotation_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rater_name: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "ANNOTATION_LABELS",
    "ANNOTATION_REF_KINDS",
    "LABEL_NOT_RELEVANT",
    "LABEL_RELEVANT",
    "LABEL_UNSURE",
    "REF_KIND_BLINDSPOT",
    "REF_KIND_CLAIM",
    "AnnotationBatchModel",
    "AnnotationItemModel",
    "AnnotationLabelModel",
]
