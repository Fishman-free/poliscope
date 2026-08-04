"""Add object_id to sources for uploaded-PDF provenance.

Revision ID: 0007_source_object_ref
Revises: 0006_source_lineage_fields
Create Date: 2026-08-03

Plan phase 6 gives a researcher-uploaded PDF the same Source/StudyFinding
pipeline a DOI-resolved paper already has. A DOI-resolved Source dedupes on
``canonical_doi``; an uploaded PDF has no DOI at all, so it needs its own
dedup key. This column plays that role for ``SourceAcquisition.
acquire_uploaded``, the same way ``canonical_doi`` already does for
``SourceAcquisition.acquire``.

Nullable because every DOI-resolved Source has no uploaded object behind it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_source_object_ref"
down_revision: str | None = "0006_source_lineage_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sources_object_id_objects",
        "sources",
        "objects",
        ["object_id"],
        ["id"],
    )
    op.create_index("ix_sources_object_id", "sources", ["object_id"])
    # Grants on `sources` were already set in 0002_papers_and_objects; a new
    # column on an existing table needs no new GRANT statement.


def downgrade() -> None:
    op.drop_index("ix_sources_object_id", table_name="sources")
    op.drop_constraint("fk_sources_object_id_objects", "sources", type_="foreignkey")
    op.drop_column("sources", "object_id")
