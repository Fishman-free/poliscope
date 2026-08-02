"""Add authors and dataset_id to sources for lineage detection.

Revision ID: 0006_source_lineage_fields
Revises: 0005_council_runtime
Create Date: 2026-08-02

CLAUDE.md 7.6 requires SAME_DATASET and SAME_RESEARCH_TEAM lineage edges, but
``sources`` had nowhere to persist the data those edges are detected from.
Independent evidence clusters (packages/evidence/independence.py) could only
ever merge on canonical DOI, which is an upper bound on independence, not the
real count.

``authors`` is populated end to end: every provider adapter
(``packages/tools/adapters/*.py``) already returns it on ``NormalizedSource``,
it was simply discarded in ``packages.papers.acquisition.SourceAcquisition``.

``dataset_id`` is added as a forward-compatible column only. No current
adapter (OpenAlex, Crossref, Semantic Scholar, Unpaywall) resolves a dataset
identifier from a DOI lookup, so in production this column will be NULL for
essentially every row until a future extraction path (e.g. a paper's Data
Availability statement) populates it. This is recorded honestly in README's
known-gaps section rather than implied as fully wired.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_source_lineage_fields"
down_revision: str | None = "0005_council_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "authors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "sources",
        sa.Column("dataset_id", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_sources_dataset_id", "sources", ["dataset_id"])
    # Grants on `sources` were already set in 0002_papers_and_objects; a new
    # column on an existing table needs no new GRANT statement.


def downgrade() -> None:
    op.drop_index("ix_sources_dataset_id", table_name="sources")
    op.drop_column("sources", "dataset_id")
    op.drop_column("sources", "authors")
