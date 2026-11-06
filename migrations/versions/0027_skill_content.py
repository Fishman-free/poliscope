"""Store the skill markdown on the row, not only in a container's /tmp.

Revision ID: 0027_skill_content
Revises: 0026_research_tools
Create Date: 2026-11-06

Why this exists (production incident, 2026-11-06): the API downloaded a
skill's SKILL.md into its own container's ``/tmp/poliscope-skills`` and
recorded that path. The worker runs in a *different* container with its own
``/tmp``, so every task that enabled a skill found the file missing and
re-downloaded it from GitHub at the start of the run. The server's egress to
GitHub is intermittent; when the fetch failed, the exception escaped the
pre-deliberation setup and the whole task was marked FAILED nine seconds
after the researcher asked a question -- no council, no answer.

The markdown is small text (2 MB cap) and is the one thing the worker needs
to honour the researcher's instruction, so it belongs in the row it already
belongs to. With it here, the worker never needs GitHub or a shared disk to
start a run. Nullable for pre-existing rows: ``ensure_downloaded`` backfills
it from the disk cache (or the next successful download) and past tasks keep
the contents they were given.

New columns on an existing table need no GRANT change (see 0006); the app
role already owns full DML on ``skills``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_skill_content"
down_revision: str | None = "0026_research_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("skills", "content")
