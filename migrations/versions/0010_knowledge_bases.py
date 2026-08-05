"""Persistent knowledge bases: user-curated document collections.

Revision ID: 0010_knowledge_bases
Revises: 0009_task_model_config
Create Date: 2026-08-05

The web workbench lets a researcher build a knowledge base (uploaded PDFs)
that is stored across tasks -- the "long-term memory" the process-level
in-memory MemoBrain adapter does not survive. Documents are parsed to text
at ingest time; ``search_vector`` is a generated column so retrieval is a
Postgres keyword query (FTS + ILIKE) with no vector infrastructure.

``research_tasks.knowledge_base_id`` links a task to the collection its
council should treat as Level A user-provided sources, and
``sources.knowledge_document_id`` traces a Source back to the document it
came from (the parallel of ``object_id`` for task uploads).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0010_knowledge_bases"
down_revision: str | None = "0009_task_model_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KNOWLEDGE_TABLES = ("knowledge_bases", "knowledge_documents")


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        # Parsed full text, stored at ingest so retrieval needs no object-store
        # read per hit. May be empty for a scan-only PDF -- that is recorded
        # honestly at ingest rather than fabricated.
        sa.Column("text_content", sa.Text(), nullable=False),
        # 'simple' config: tokens split on whitespace/punctuation. Fine for
        # English; a Chinese sentence becomes one token, so Chinese retrieval
        # relies on the ILIKE branch of the search query (documented in
        # packages/knowledge/search.py) rather than on this vector.
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', text_content)", persisted=True
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"]
        ),
    )
    # Name must match SQLAlchemy's default for `index=True` on the ORM's
    # knowledge_base_id column -- test_schema_drift compares the migration
    # head against the ORM metadata and fails on any naming divergence.
    op.create_index(
        "ix_knowledge_documents_knowledge_base_id",
        "knowledge_documents",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_documents_search_vector",
        "knowledge_documents",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.add_column(
        "research_tasks",
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_research_tasks_knowledge_base",
        "research_tasks",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
    )
    op.create_index(
        "ix_research_tasks_knowledge_base_id",
        "research_tasks",
        ["knowledge_base_id"],
    )

    op.add_column(
        "sources",
        sa.Column(
            "knowledge_document_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_sources_knowledge_document",
        "sources",
        "knowledge_documents",
        ["knowledge_document_id"],
        ["id"],
    )
    op.create_index(
        "ix_sources_knowledge_document_id",
        "sources",
        ["knowledge_document_id"],
    )

    # The ingestion pipeline runs under the application role, so without
    # these grants every knowledge-base write fails at runtime, mirroring
    # migration 0002's handling of the paper tables. `sources` and
    # `research_tasks` already carry the grants the projector and app role
    # need; a new column on an existing table needs no new GRANT statement
    # (0009's precedent).
    revoke_all(APP_ROLE, KNOWLEDGE_TABLES)
    grant(APP_ROLE, FULL_DML, KNOWLEDGE_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, KNOWLEDGE_TABLES)
    op.drop_index("ix_sources_knowledge_document_id", table_name="sources")
    op.drop_constraint(
        "fk_sources_knowledge_document", "sources", type_="foreignkey"
    )
    op.drop_column("sources", "knowledge_document_id")
    op.drop_index(
        "ix_research_tasks_knowledge_base_id", table_name="research_tasks"
    )
    op.drop_constraint(
        "fk_research_tasks_knowledge_base", "research_tasks", type_="foreignkey"
    )
    op.drop_column("research_tasks", "knowledge_base_id")
    op.drop_index(
        "ix_knowledge_documents_search_vector", table_name="knowledge_documents"
    )
    op.drop_index(
        "ix_knowledge_documents_knowledge_base_id", table_name="knowledge_documents"
    )
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
