"""Add council_checkpoint to research_tasks for the JOINT_MODELING gate.

Revision ID: 0008_council_checkpoint
Revises: 0007_source_object_ref
Create Date: 2026-08-03

Plan phase 8 adds one fixed checkpoint between BLINDSPOT_BOUNTY and
JOINT_MODELING where a human gives an advisory, non-deciding directional
steer (CLAUDE.md 4/8). CouncilOrchestrator.run() holds no state between
calls, so resuming from this checkpoint without re-running five already-
completed phases needs somewhere durable to hold the serialized
CouncilCheckpoint (packages/epistemo/contracts.py) while the task sits in
AWAITING_COUNCIL_INPUT. Nullable because every task outside that one window
has nothing to store here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_council_checkpoint"
down_revision: str | None = "0007_source_object_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("council_checkpoint", postgresql.JSONB(), nullable=True),
    )
    # Grants on `research_tasks` were already set in 0001_research_and_calls; a
    # new column on an existing table needs no new GRANT statement.


def downgrade() -> None:
    op.drop_column("research_tasks", "council_checkpoint")
