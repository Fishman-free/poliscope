"""User accounts, auth tokens, and per-user isolation columns.

Revision ID: 0012_accounts
Revises: 0011_app_settings
Create Date: 2026-08-05

The web workbench gets real accounts (register / login / remember-me on this
machine) replacing the Caddy shared-password gate. ``users`` + ``auth_tokens``
are the account system; ``research_tasks.user_id``, ``knowledge_bases.user_id``
and ``app_settings.user_id`` (UNIQUE with the single-row id) make every
researcher's data belong to exactly one account. Rows created before this
migration carry ``user_id NULL`` and stay invisible to every account -- an
honest read of "no one owns this yet" rather than a silent takeover.

Tokens are stored hashed (sha256 of the bearer token) so a leaked database
does not leak sessions; see packages/accounts/security.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0012_accounts"
down_revision: str | None = "0011_app_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_TABLES = ("users", "auth_tokens")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    # research_tasks: ownership. created_by stays as the audit string; user_id
    # is the isolation key every query filters on.
    op.add_column(
        "research_tasks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_research_tasks_user", "research_tasks", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_research_tasks_user_id", "research_tasks", ["user_id"])

    # knowledge_bases: same ownership story as tasks.
    op.add_column(
        "knowledge_bases",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_knowledge_bases_user", "knowledge_bases", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_knowledge_bases_user_id", "knowledge_bases", ["user_id"])

    # app_settings: the single-row-per-user settings store. Drop the old
    # id=1-only uniqueness and replace it with (user_id, id) so each account
    # owns its own single row; the CHECK on id = 1 stays.
    op.drop_constraint(
        "ck_app_settings_single_row", "app_settings", type_="check"
    )
    op.add_column(
        "app_settings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_app_settings_user", "app_settings", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_app_settings_user_id", "app_settings", ["user_id"])
    op.create_unique_constraint(
        "uq_app_settings_user_row", "app_settings", ["user_id", "id"]
    )
    op.create_check_constraint(
        "ck_app_settings_single_row", "app_settings", "id = 1"
    )

    # The account tables are ordinary business tables: the app role (web and
    # worker both) reads and writes them. The isolation columns need no new
    # grants -- they live on tables whose grants already exist (0002/0009/0010
    # precedent), but the FK targets' tables do need their grants above.
    revoke_all(APP_ROLE, ACCOUNT_TABLES)
    grant(APP_ROLE, FULL_DML, ACCOUNT_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, ACCOUNT_TABLES)
    op.drop_constraint("ck_app_settings_single_row", "app_settings", type_="check")
    op.drop_constraint("uq_app_settings_user_row", "app_settings", type_="unique")
    op.drop_index("ix_app_settings_user_id", table_name="app_settings")
    op.drop_constraint("fk_app_settings_user", "app_settings", type_="foreignkey")
    op.drop_column("app_settings", "user_id")
    op.create_check_constraint(
        "ck_app_settings_single_row", "app_settings", "id = 1"
    )

    op.drop_index("ix_knowledge_bases_user_id", table_name="knowledge_bases")
    op.drop_constraint("fk_knowledge_bases_user", "knowledge_bases", type_="foreignkey")
    op.drop_column("knowledge_bases", "user_id")

    op.drop_index("ix_research_tasks_user_id", table_name="research_tasks")
    op.drop_constraint("fk_research_tasks_user", "research_tasks", type_="foreignkey")
    op.drop_column("research_tasks", "user_id")

    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_table("users")
