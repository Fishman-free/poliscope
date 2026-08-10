"""Email verification for registration, and the users.email column.

Revision ID: 0022_email_verification
Revises: 0021_free_trial
Create Date: 2026-08-10

Registration becomes two-phase (send a 6-digit code, then confirm it) so a
new account is only created after the caller proves the inbox is theirs. The
code is stored hashed (sha256, never plaintext), expires after 5 minutes, and
can only be consumed once; resend is throttled to one per 60s and five per
day per address.

``users.email`` is nullable on purpose: accounts created before this
migration have no email (NULL), and PostgreSQL's unique constraint treats
multiple NULLs as distinct, so legacy accounts are unaffected while every new
account's email is unique.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0022_email_verification"
down_revision: str | None = "0021_free_trial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERIFICATION_TABLES = ("email_verifications",)


def upgrade() -> None:
    # users.email: nullable keeps legacy accounts (created before this
    # migration) at NULL, and the unique constraint (not a unique index --
    # test_schema_drift compares against ORM metadata) enforces "one email,
    # one account" for new registrations.
    op.add_column(
        "users",
        sa.Column("email", sa.String(length=254), nullable=True),
    )
    op.create_unique_constraint("users_email_key", "users", ["email"])

    # email_verifications: one row per address (the natural key is the email),
    # holding the current pending code and its throttle/attempt counters.
    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_day", sa.Date(), nullable=False),
        sa.Column(
            "sent_today",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="email_verifications_email_key"),
    )

    # The new table is ordinary business data: the app role (web and worker)
    # reads and writes it (0012_accounts precedent -- a new table needs its
    # grants, a new column on a granted table does not).
    revoke_all(APP_ROLE, VERIFICATION_TABLES)
    grant(APP_ROLE, FULL_DML, VERIFICATION_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, VERIFICATION_TABLES)
    op.drop_table("email_verifications")
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.drop_column("users", "email")
