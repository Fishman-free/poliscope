"""Persistent model-gateway settings (single-row table).

Revision ID: 0011_app_settings
Revises: 0010_knowledge_bases
Create Date: 2026-08-05

The web workbench keeps the researcher's model endpoint (base URL, API key,
model name) as a permanent setting rather than a per-task form field. One
row (``id = 1``) holds it; ``apps/api/routers/tasks.py`` applies it to new
tasks that carry no explicit per-task model config. The API key is stored
exactly like the per-task ``research_tasks.model_config`` already is and is
never returned by any endpoint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0011_app_settings"
down_revision: str | None = "0010_knowledge_bases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS_TABLES = ("app_settings",)


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_base_url", sa.String(length=1024), nullable=True),
        sa.Column("model_api_key", sa.String(length=2048), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_app_settings_single_row"),
    )

    # The web/CLI read and write settings under the application role, so the
    # grants mirror migration 0010's knowledge-base tables exactly.
    revoke_all(APP_ROLE, SETTINGS_TABLES)
    grant(APP_ROLE, FULL_DML, SETTINGS_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, SETTINGS_TABLES)
    op.drop_table("app_settings")
