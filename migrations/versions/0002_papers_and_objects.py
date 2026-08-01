"""Create paper ingestion and object storage tables.

Revision ID: 0002_papers_and_objects
Revises: 0001_research_and_calls
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_papers_and_objects"
down_revision: str | None = "0001_research_and_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("encryption", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
    )
    op.create_index("ix_objects_task_id", "objects", ["task_id"])

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("canonical_doi", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("provider_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
    )
    op.create_index("ix_sources_task_id", "sources", ["task_id"])
    op.create_index("ix_sources_doi", "sources", ["doi"])
    op.create_index("ix_sources_canonical_doi", "sources", ["canonical_doi"])

    op.create_table(
        "source_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_hash", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
    )
    op.create_index("ix_source_versions_source_id", "source_versions", ["source_id"])

    op.create_table(
        "studies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_question", sa.String(length=2048), nullable=False),
        sa.Column("design", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_version_id"], ["source_versions.id"]),
    )
    op.create_index("ix_studies_source_version_id", "studies", ["source_version_id"])

    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement", sa.String(length=4096), nullable=False),
        sa.Column("origin", sa.String(length=64), nullable=False),
        sa.Column("effect_direction", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"]),
    )
    op.create_index("ix_findings_study_id", "findings", ["study_id"])

    op.create_table(
        "citation_anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("locator", sa.String(length=255), nullable=False),
        sa.Column("exact_quote", sa.String(length=4096), nullable=False),
        sa.Column("extraction_agent", sa.String(length=255), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"]),
    )
    op.create_index(
        "ix_citation_anchors_finding_id",
        "citation_anchors",
        ["finding_id"],
    )


def downgrade() -> None:
    op.drop_table("citation_anchors")
    op.drop_table("findings")
    op.drop_table("studies")
    op.drop_table("source_versions")
    op.drop_table("sources")
    op.drop_table("objects")
