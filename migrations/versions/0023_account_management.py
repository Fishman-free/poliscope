"""Account management: avatar key, and verification-purpose on email codes.

Revision ID: 0023_account_management
Revises: 0022_email_verification
Create Date: 2026-08-10

Round-8 account management: users gain an ``avatar_key`` (the object-store
key of an uploaded avatar image -- never the bytes themselves), and
``email_verifications`` gains a ``purpose`` so the same email can hold a
registration code and a password-reset code at the same time without
colliding. The uniqueness moves from ``email`` to ``(email, purpose)``;
legacy rows (all registrations) are filled with ``'register'`` by the
server default, so the new constraint holds for them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_account_management"
down_revision: str | None = "0022_email_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both columns land on already-granted tables -- no new grant (0022
    # documented the convention: a new column on a granted table needs none).
    op.add_column(
        "users",
        sa.Column("avatar_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_verifications",
        sa.Column(
            "purpose",
            sa.String(length=16),
            server_default=sa.text("'register'"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        "email_verifications_email_key", "email_verifications", type_="unique"
    )
    op.create_unique_constraint(
        "email_verifications_email_purpose_key",
        "email_verifications",
        ["email", "purpose"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "email_verifications_email_purpose_key",
        "email_verifications",
        type_="unique",
    )
    op.create_unique_constraint(
        "email_verifications_email_key", "email_verifications", ["email"]
    )
    op.drop_column("email_verifications", "purpose")
    op.drop_column("users", "avatar_key")
