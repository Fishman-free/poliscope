"""Add research_tasks.output_language: the language the council must answer in.

Revision ID: 0017_output_language
Revises: 0016_process_stream_no_fk
Create Date: 2026-08-07

Round-4 requirement: a researcher who asks in Chinese must get Chinese
reasoning, judgments, and reports back; an English question, English. The API
resolves "auto" at task creation (packages/research/language.py) and stores
one of zh-Hans / zh-Hant / en; the worker injects it into every seat's system
prompt. Existing rows default to "auto", which the worker resolves from the
question at run time -- so no pre-existing task is left without a language.

The column is task-owned (same shape as skill_ids), so no new table grants
are needed; the research_tasks DML grants from earlier migrations already
cover the new column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_output_language"
down_revision: str | None = "0016_process_stream_no_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column(
            "output_language",
            sa.String(length=16),
            server_default="auto",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("research_tasks", "output_language")
