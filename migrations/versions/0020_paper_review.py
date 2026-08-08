"""Paper-review task type and multi-format upload metadata.

Revision ID: 0020_paper_review
Revises: 0019_session_deletion_grants
Create Date: 2026-08-09

Round-7 feature: a researcher may hand the council a paper to review instead
of a controversy question. The same ``research_tasks`` row drives both modes --
``task_type`` tells the worker which prompt shape to run and the synthesizer
which report shape to emit -- so no new table is needed.

``objects.file_name`` exists because the upload endpoint now accepts more than
PDF: the worker must know which extractor to use for an object, and the
object key's suffix is derivable but the name the researcher gave is the
honest record (CLAUDE.md 6). Old rows have NULL (they were all PDFs -- the
pre-round-7 upload gate accepted nothing else), so the extraction path
defaults NULL to a PDF.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_paper_review"
down_revision: str | None = "0019_session_deletion_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both are columns on existing tables, so the DML grants already given
    # (research_tasks in 0001, objects in 0002) cover them -- no new grant.
    op.add_column(
        "research_tasks",
        sa.Column(
            "task_type",
            sa.String(32),
            server_default="deep_research",
            nullable=False,
        ),
    )
    op.add_column(
        "objects",
        sa.Column("file_name", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("objects", "file_name")
    op.drop_column("research_tasks", "task_type")
