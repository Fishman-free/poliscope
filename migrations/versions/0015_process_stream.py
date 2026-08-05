"""Process-only real-time stream for thinking-path visualisation.

Revision ID: 0015_process_stream
Revises: 0014_fix_app_settings_pk
Create Date: 2026-08-06

The web workbench's "live view" needs the running council's chain of thought,
tool calls and stage progress in near real time. The Scientific Event Ledger
cannot carry that: it is the durable, idempotent, replayable scientific record,
and token-level noise would pollute it. ``process_stream`` is the live wire
instead -- written by the worker as it runs, read by the API's SSE process
endpoint, never admitted to the Evidence Graph, and explicitly not
replay-guaranteed (a reconnecting client re-reads from the start and
deduplicates by ``seq``).

The table is task-owned, so it is created under the app role's full DML, same
as ``skills`` (0013) -- the worker writes and the API reads under one identity,
which is fine here because the table carries no evidence semantics.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0015_process_stream"
down_revision: str | None = "0014_fix_app_settings_pk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "process_stream"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "seq", name="uq_process_stream_task_seq"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_process_stream_task_seq", TABLE, ["task_id", "seq"])

    revoke_all(APP_ROLE, (TABLE,))
    grant(APP_ROLE, FULL_DML, (TABLE,))


def downgrade() -> None:
    revoke_all(APP_ROLE, (TABLE,))
    op.drop_index("ix_process_stream_task_seq", table_name=TABLE)
    op.drop_table(TABLE)
