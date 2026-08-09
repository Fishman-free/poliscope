"""Free-trial model quota columns on app_settings.

Revision ID: 0021_free_trial
Revises: 0020_paper_review
Create Date: 2026-08-09

Round-7 feature: every account may use the deployment's free-trial model
(DashScope qwen3.8-max) twice. The quota lives on the account's single
``app_settings`` row -- the same row that stores its model endpoint -- as an
explicit marker plus a count: ``is_free_trial`` says the current saved
endpoint IS the free-trial vendor (so a later manual save can clear it), and
``free_trial_used`` is how many confirm-claims have consumed a slot. The
count is incremented atomically by the API (``UPDATE ... WHERE
free_trial_used < 2 RETURNING``), never by a read-modify-write, so two
concurrent confirmations cannot both take the last slot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_free_trial"
down_revision: str | None = "0020_paper_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Columns on an existing table whose FULL_DML grant (migration 0011)
    # already covers them -- no new grant.
    op.add_column(
        "app_settings",
        sa.Column(
            "is_free_trial",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "free_trial_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "free_trial_used")
    op.drop_column("app_settings", "is_free_trial")
