"""Research workbench tools: corpus cutoff, read-only shares, model override,
human annotation, and source publication year.

Revision ID: 0026_research_tools
Revises: 0025_task_cancel_requests
Create Date: 2026-09-03

Five small, independent additions that back the A1-D12 workbench features:

* ``sources.publication_year`` (A3, corpus time-travel): providers already
  return a publication year on ``NormalizedSource``; acquisition discarded it.
  A closed-corpus replay needs a real date to filter against, so it is now
  persisted. Nullable because uploads / knowledge documents carry no provider
  year and an unknown date must stay visibly unknown (CLAUDE.md 7).
* ``research_tasks.corpus_cutoff`` + ``research_tasks.replay_of_task_id``
  (A3): an optional inclusive publication-year cutoff applied at acquisition,
  and a pointer to the task a closed-corpus replay was cloned from.
* ``research_tasks.share_token`` + ``share_created_at`` (A2): one opaque
  read-only share token per task; NULL means "not shared". The token is the
  bearer capability for the public, login-free read endpoint, so it uses
  ``secrets.token_urlsafe`` entropy server-side and never encodes ownership.
* ``research_tasks.model_config_override`` (C10): a QUEUED/PAUSED task's
  endpoint may be re-pointed before a worker claims it; the worker reads this
  fresh at claim time instead of being frozen to the creation-time snapshot.
* ``annotation_batches / items / labels`` (C9): the ForesightBlindspot human
  annotation workflow. A batch freezes the items (blindspots/claims) being
  rated; raters submit one nominal label per item; agreement (Cohen's kappa /
  Krippendorff's alpha) is computed from the labels, never stored as truth.

The annotation tables carry no evidence-graph semantics, so -- exactly like
``task_cancel_requests`` (0025) and ``process_stream`` (0015) -- the app role
receives full DML and the projector never touches them. New columns on
existing tables need no GRANT change (see 0006).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0026_research_tools"
down_revision: str | None = "0025_task_cancel_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCHES = "annotation_batches"
ITEMS = "annotation_items"
LABELS = "annotation_labels"


def upgrade() -> None:
    # --- A3: publication year + corpus cutoff / replay linkage -------------
    op.add_column(
        "sources",
        sa.Column("publication_year", sa.Integer(), nullable=True),
    )
    op.add_column(
        "research_tasks",
        sa.Column("corpus_cutoff", sa.Date(), nullable=True),
    )
    op.add_column(
        "research_tasks",
        sa.Column(
            "replay_of_task_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_research_tasks_replay_of",
        "research_tasks",
        "research_tasks",
        ["replay_of_task_id"],
        ["task_id"],
        ondelete="SET NULL",
    )

    # --- A2: one read-only share token per task ----------------------------
    op.add_column(
        "research_tasks",
        sa.Column("share_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_tasks",
        sa.Column(
            "share_created_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_research_tasks_share_token",
        "research_tasks",
        ["share_token"],
        unique=True,
    )

    # --- C10: queued-task model endpoint override --------------------------
    op.add_column(
        "research_tasks",
        sa.Column(
            "model_config_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # --- C9: human annotation workflow -------------------------------------
    op.create_table(
        BATCHES,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False, default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
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
    op.create_table(
        ITEMS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        # Which graph object this item freezes: "blindspot" or "claim".
        sa.Column("ref_kind", sa.String(length=32), nullable=False),
        sa.Column("ref_node_id", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "position",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["batch_id"], [f"{BATCHES}.id"], ondelete="CASCADE"
        ),
    )
    op.create_table(
        LABELS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("rater_name", sa.String(length=255), nullable=False),
        # Nominal label: "relevant" / "not_relevant" / "unsure".
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["item_id"], [f"{ITEMS}.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "item_id", "rater_name", name="uq_annotation_label_one_per_rater"
        ),
    )

    for table in (BATCHES, ITEMS, LABELS):
        revoke_all(APP_ROLE, (table,))
        grant(APP_ROLE, FULL_DML, (table,))


def downgrade() -> None:
    for table in (LABELS, ITEMS, BATCHES):
        revoke_all(APP_ROLE, (table,))
    op.drop_table(LABELS)
    op.drop_table(ITEMS)
    op.drop_table(BATCHES)

    op.drop_column("research_tasks", "model_config_override")
    op.drop_index("ix_research_tasks_share_token", table_name="research_tasks")
    op.drop_column("research_tasks", "share_created_at")
    op.drop_column("research_tasks", "share_token")
    op.drop_constraint(
        "fk_research_tasks_replay_of", "research_tasks", type_="foreignkey"
    )
    op.drop_column("research_tasks", "replay_of_task_id")
    op.drop_column("research_tasks", "corpus_cutoff")
    op.drop_column("sources", "publication_year")
