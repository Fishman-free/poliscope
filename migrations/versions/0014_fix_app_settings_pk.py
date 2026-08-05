"""Fix app_settings primary key for per-account rows.

Revision ID: 0014_fix_app_settings_pk
Revises: 0013_skills
Create Date: 2026-08-05

Migration 0012 made app_settings per-account by adding ``user_id`` with a
``(user_id, id)`` uniqueness -- but kept ``id`` as the primary key, and every
account writes ``id = 1``, so the second account to save settings collided on
the primary key. The account is the row's identity; the primary key moves to
``user_id`` (``id`` stays, forced to 1 by the CHECK, as documentation of the
single-row-per-account intent).

Pre-account rows (``user_id`` NULL) are deleted: they were already invisible
to every account (the README says so), and a NULL primary key is impossible.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_fix_app_settings_pk"
down_revision: str | None = "0013_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM app_settings WHERE user_id IS NULL"
    )
    # user_id becomes the primary key (which is indexed by Postgres), so the
    # standalone index from 0012 is redundant -- and the ORM, which declares
    # user_id as the primary key with no index=True, would drift on it.
    op.drop_index("ix_app_settings_user_id", table_name="app_settings")
    op.drop_constraint(
        "uq_app_settings_user_row", "app_settings", type_="unique"
    )
    op.drop_constraint(
        "ck_app_settings_single_row", "app_settings", type_="check"
    )
    op.drop_constraint("app_settings_pkey", "app_settings", type_="primary")
    op.alter_column("app_settings", "user_id", nullable=False)
    op.create_primary_key("app_settings_pkey", "app_settings", ["user_id"])
    op.create_check_constraint(
        "ck_app_settings_single_row", "app_settings", "id = 1"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_app_settings_single_row", "app_settings", type_="check"
    )
    op.drop_constraint("app_settings_pkey", "app_settings", type_="primary")
    op.alter_column("app_settings", "user_id", nullable=True)
    op.create_primary_key("app_settings_pkey", "app_settings", ["id"])
    op.create_check_constraint(
        "ck_app_settings_single_row", "app_settings", "id = 1"
    )
    op.create_unique_constraint(
        "uq_app_settings_user_row", "app_settings", ["user_id", "id"]
    )
    op.create_index(
        "ix_app_settings_user_id", "app_settings", ["user_id"]
    )
