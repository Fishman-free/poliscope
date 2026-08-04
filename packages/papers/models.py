from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from packages.kernel.database import Base


class ObjectModel(Base):
    __tablename__ = "objects"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    object_key: Mapped[str] = mapped_column(
        String(1024), nullable=False, unique=True
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    encryption: Mapped[str] = mapped_column(
        String(32), nullable=False, default="AES256"
    )
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceModel(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("research_tasks.task_id"),
        nullable=False,
        index=True,
    )
    doi: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    canonical_doi: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    provider_ids: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    authors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    dataset_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    object_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("objects.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceVersionModel(Base):
    __tablename__ = "source_versions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id"),
        nullable=False,
        index=True,
    )
    version_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StudyModel(Base):
    __tablename__ = "studies"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    source_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_versions.id"),
        nullable=False,
        index=True,
    )
    research_question: Mapped[str] = mapped_column(
        String(2048), nullable=False, default=""
    )
    design: Mapped[str] = mapped_column(
        String(64), nullable=False, default="other"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FindingModel(Base):
    __tablename__ = "findings"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    study_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("studies.id"),
        nullable=False,
        index=True,
    )
    statement: Mapped[str] = mapped_column(
        String(4096), nullable=False, default=""
    )
    origin: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_direction: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_reported"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CitationAnchorModel(Base):
    __tablename__ = "citation_anchors"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    finding_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("findings.id"),
        nullable=False,
        index=True,
    )
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locator: Mapped[str] = mapped_column(String(255), nullable=False)
    exact_quote: Mapped[str] = mapped_column(String(4096), nullable=False)
    extraction_agent: Mapped[str] = mapped_column(String(255), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unverified"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
