"""Seat-retry audit: how many attempts a scientist's run took.

Revision ID: 0024_seat_retry_attempts
Revises: 0023_account_management
Create Date: 2026-08-10

Round-9 seat retry: when a scientist times out or fails in a phase, the
orchestrator asks them again (up to ``MAX_SEAT_ATTEMPTS``), so a transient
provider hiccup re-admits the seat to the round instead of recording an
avoidable absence. ``scientist_runs.attempts`` records how many times the seat
was actually asked, so the audit trail can distinguish "answered first time"
from "answered only after a retry" from "gave up after N tries".

The table is populated by apps/worker/jobs.py::_persist_council_runs (round-9);
it was previously an orphan table with no writer. There are no existing rows to
backfill, and the column sits on an already-granted table, so no new grant is
needed (the 0022 convention).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_seat_retry_attempts"
down_revision: str | None = "0023_account_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scientist_runs",
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("scientist_runs", "attempts")
