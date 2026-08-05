"""Add model_config to research_tasks for per-task model endpoints.

Revision ID: 0009_task_model_config
Revises: 0008_council_checkpoint
Create Date: 2026-08-05

Researchers may run a task against their own OpenAI-compatible endpoint
(base URL + API key + optional model name) instead of the deployment's
configured model gateway (TaskModelConfig in packages/research/contracts.py).
The worker reads this column at claim time and builds a per-task gateway
when it is present. Nullable because the ordinary path -- deployment
configuration -- has nothing stored here. The API key lives in this JSONB
column and is never returned by any read endpoint; it is also not part of
the Research Brief, the ledger, or the Evidence Graph.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_task_model_config"
down_revision: str | None = "0008_council_checkpoint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("model_config", postgresql.JSONB(), nullable=True),
    )
    # Grants on `research_tasks` were already set in 0001_research_and_calls; a
    # new column on an existing table needs no new GRANT statement.


def downgrade() -> None:
    op.drop_column("research_tasks", "model_config")
