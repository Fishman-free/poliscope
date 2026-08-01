"""Create council runtime, scientist runs, and round outputs.

Revision ID: 0005_council_runtime
Revises: 0003_evidence_ledger_and_graph
Create Date: 2026-08-01

These tables are the durable half of the state machine in CLAUDE.md 10. A round
that exists only inside the orchestrator's memory cannot be resumed after a
restart, so every phase transition and every seat outcome lands here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0005_council_runtime"
down_revision: str | None = "0003_evidence_ledger_and_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNCIL_TABLES = ("council_rounds", "scientist_runs", "round_outputs")


def upgrade() -> None:
    op.create_table(
        "council_rounds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        # Timezone aware because these timestamps are read back as an audit
        # trail. A naive value cannot be compared across a deployment that
        # changes host timezone.
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Recorded when the round completes so that resuming a task does not have
        # to re-derive the protocol order from code that may have changed.
        sa.Column("next_phase", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_council_rounds_task_id", "council_rounds", ["task_id"])
    op.create_index("ix_council_rounds_status", "council_rounds", ["status"])

    op.create_table(
        "scientist_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seat", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Set when one seat fails. The round continues degraded rather than
        # aborting, as required by CLAUDE.md 10.
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["round_id"], ["council_rounds.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One run per seat per round, so a retry updates the row it already owns
        # instead of leaving two contradictory outcomes behind.
        sa.UniqueConstraint("round_id", "seat", name="uq_scientist_run_seat"),
    )
    op.create_index("ix_scientist_runs_round_id", "scientist_runs", ["round_id"])

    op.create_table(
        "round_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seat", sa.String(length=64), nullable=False),
        sa.Column("output_type", sa.String(length=64), nullable=False),
        # JSONB rather than JSON: the workspace queries inside these payloads,
        # and the ORM declares JSONB, so JSON here would be permanent drift.
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["round_id"], ["council_rounds.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_round_outputs_round_id", "round_outputs", ["round_id"])

    revoke_all(APP_ROLE, COUNCIL_TABLES)
    grant(APP_ROLE, FULL_DML, COUNCIL_TABLES)


def downgrade() -> None:
    revoke_all(APP_ROLE, COUNCIL_TABLES)
    op.drop_table("round_outputs")
    op.drop_table("scientist_runs")
    op.drop_table("council_rounds")
