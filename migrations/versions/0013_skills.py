"""Researcher skills: GitHub skill repositories the account enables.

Revision ID: 0013_skills
Revises: 0012_accounts
Create Date: 2026-08-05

The web workbench lets a researcher add a GitHub skill URL; the server
downloads its SKILL.md into a per-account directory and records the skill
here. ``research_tasks.skill_ids`` (added alongside in the same migration,
declared on the ORM since 0012's accounts work) carries which skills a task
enabled; the worker reads them back and injects the downloaded texts into the
council's prompts as explicitly non-evidence process context.

Deletion is plain: a task stores skill ids, not foreign keys, so removing a
skill simply severs the link for future tasks -- past tasks keep whatever
they were given, and nothing about evidence is ever deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0013_skills"
down_revision: str | None = "0012_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILL_TABLES = ("skills",)


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("github_url", sa.String(length=1024), nullable=False),
        sa.Column("downloaded_path", sa.String(length=1024), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "github_url", name="uq_skills_user_url"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_skills_user_id", "skills", ["user_id"])

    # research_tasks.skill_ids: the ORM declared it with 0012's accounts work
    # (the column is task-owned, not account-owned), so it is added here.
    op.add_column(
        "research_tasks",
        sa.Column(
            "skill_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )

    revoke_all(APP_ROLE, SKILL_TABLES)
    grant(APP_ROLE, FULL_DML, SKILL_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, SKILL_TABLES)
    op.drop_column("research_tasks", "skill_ids")
    op.drop_index("ix_skills_user_id", table_name="skills")
    op.drop_table("skills")
