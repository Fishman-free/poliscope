"""Researcher-initiated stop (round-10 「停止研究」).

Revision ID: 0025_task_cancel_requests
Revises: 0024_seat_retry_attempts
Create Date: 2026-08-12

The researcher may stop a running task. Stopping a QUEUED or PAUSED task is a
plain status flip to CANCELLED (no worker holds its row). Stopping a RUNNING
task is different: the worker's deliberation runs one long transaction that
holds ``research_tasks`` row-locked for the whole run, so the API cannot just
``UPDATE status`` -- the write would block until the run finishes, which is
exactly what "stop it now" must not wait for.

``task_cancel_requests`` is the side channel that gets around that. It is a
separate table (no row-lock conflict), written by the API the moment the
researcher clicks stop and polled by the worker between phases. On seeing a
request the worker halts the run early and records CANCELLED instead of a
terminal status. A request row for a QUEUED/PAUSED task is never needed -- the
API flips those directly -- but the table allows an UNCONDITIONAL write for a
RUNNING task without the API having to reason about which state it is in under
race. A request row for a task that never gets picked up again is harmless and
is deleted when the task is deleted (the FK cascades).

The table carries no evidence semantics, so both the API and the worker use it
under the single app role's full DML, same as ``process_stream`` (0015).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0025_task_cancel_requests"
down_revision: str | None = "0024_seat_retry_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "task_cancel_requests"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # unique=True + index=True matches the ORM's TaskCancelRequestModel
        # (packages/research/models.py), so autogenerate's drift check sees no
        # difference: SQLAlchemy names the unique constraint
        # ``task_cancel_requests_task_id_key`` and the index
        # ``ix_task_cancel_requests_task_id``.
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
            unique=True,
        ),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"
        ),
    )

    revoke_all(APP_ROLE, (TABLE,))
    grant(APP_ROLE, FULL_DML, (TABLE,))


def downgrade() -> None:
    revoke_all(APP_ROLE, (TABLE,))
    op.drop_table(TABLE)
