"""Create the Scientific Event Ledger and the Evidence Graph.

Revision ID: 0003_evidence_ledger_and_graph
Revises: 0002_papers_and_objects
Create Date: 2026-08-01

These five tables carry the constraints that separate Poliscope from a report
generator, so the grants at the end of this revision are part of the design and
not an operational detail:

* the application role may append to ``scientific_events`` but never rewrite or
  delete a row, because CLAUDE.md 5.3 requires events to stay replayable;
* the application role holds SELECT and nothing else on ``graph_nodes`` and
  ``graph_edges``, which makes the Graph Projector the only possible writer;
* no role at all receives DELETE on the graph tables, so a refuted, quarantined,
  or folded node cannot be physically removed even by a bug in the projector.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE, PROJECTOR_ROLE
from packages.kernel.privileges import (
    APPEND,
    MUTATE,
    READ,
    grant,
    grant_column,
    revoke_all,
)

revision: str = "0003_evidence_ledger_and_graph"
down_revision: str | None = "0002_papers_and_objects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEDGER_TABLES = ("scientific_events", "event_audits", "projection_checkpoints")
GRAPH_TABLES = ("graph_nodes", "graph_edges")


def upgrade() -> None:
    op.create_table(
        "scientific_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # Replaying a task must not duplicate its events, so the writer supplies
        # a deterministic key and the database rejects the second attempt.
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        # Evidence level A to D. Nullable because process events carry no level.
        sa.Column("evidence_level", sa.String(length=8), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_event_idempotency"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_event_sequence"),
    )
    op.create_index("ix_scientific_events_task_id", "scientific_events", ["task_id"])
    op.create_index(
        "ix_scientific_events_event_type",
        "scientific_events",
        ["event_type"],
    )

    op.create_table(
        "event_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Which of the three audits in CLAUDE.md 7.3 produced this decision.
        sa.Column("gate_stage", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["scientific_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_audits_event_id", "event_audits", ["event_id"])

    op.create_table(
        "graph_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # active, refuted, quarantined, or folded. Never absent: a node that
        # loses an argument changes status and keeps its row.
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_nodes_task_id", "graph_nodes", ["task_id"])
    op.create_index("ix_graph_nodes_node_type", "graph_nodes", ["node_type"])

    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
        # Projecting the same event twice must not add a parallel edge, which is
        # what makes the projector idempotent rather than merely ordered.
        sa.UniqueConstraint(
            "task_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_graph_edge",
        ),
    )
    op.create_index("ix_graph_edges_task_id", "graph_edges", ["task_id"])
    op.create_index("ix_graph_edges_source_node_id", "graph_edges", ["source_node_id"])
    op.create_index("ix_graph_edges_target_node_id", "graph_edges", ["target_node_id"])
    op.create_index("ix_graph_edges_edge_type", "graph_edges", ["edge_type"])

    op.create_table(
        "projection_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The highest event sequence already projected. Ordering the projector by
        # this column is what keeps it a single logical writer per task.
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )

    _grant_runtime_permissions()


def _grant_runtime_permissions() -> None:
    all_tables = LEDGER_TABLES + GRAPH_TABLES
    revoke_all(APP_ROLE, all_tables)
    revoke_all(PROJECTOR_ROLE, all_tables)

    # The council writes events and reads everything, but the ledger is
    # append-only and the graph is read-only from the application's side.
    grant(APP_ROLE, APPEND, ("scientific_events",))
    grant(APP_ROLE, READ, ("event_audits", "projection_checkpoints"))
    grant(APP_ROLE, READ, GRAPH_TABLES)

    # The projector advances an event's status but must not be able to rewrite
    # the payload it was handed, so UPDATE is granted one column at a time.
    grant(PROJECTOR_ROLE, READ, ("scientific_events",))
    grant_column(PROJECTOR_ROLE, "UPDATE", "scientific_events", ("status",))
    grant(PROJECTOR_ROLE, APPEND, ("event_audits",))
    grant(PROJECTOR_ROLE, MUTATE, ("projection_checkpoints",))
    grant(PROJECTOR_ROLE, MUTATE, GRAPH_TABLES)

    # The projector validates that a StudyFinding traces back to a real Source
    # before it creates the node, which requires reading the papers tables.
    grant(
        PROJECTOR_ROLE,
        READ,
        ("sources", "source_versions", "studies", "findings", "citation_anchors"),
    )


def downgrade() -> None:
    papers_tables = (
        "sources",
        "source_versions",
        "studies",
        "findings",
        "citation_anchors",
    )
    revoke_all(PROJECTOR_ROLE, papers_tables)
    for table_name in (
        "projection_checkpoints",
        "graph_edges",
        "graph_nodes",
        "event_audits",
        "scientific_events",
    ):
        revoke_all(APP_ROLE, (table_name,))
        revoke_all(PROJECTOR_ROLE, (table_name,))
        op.drop_table(table_name)
