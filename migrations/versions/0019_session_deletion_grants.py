"""Grant DELETE on every task-child table so the API can delete a session.

Revision ID: 0019_session_deletion_grants
Revises: 0018_skills_multi_name_unique
Create Date: 2026-08-08

Round-6 requirement: a researcher may delete their own sessions (queued
clutter, a mistake, an obsolete run) from the session-history panel. Deleting
a task physically removes its child records -- ledger events, process stream,
evidence graph, audit rows -- so ``apps/api/routers/tasks.py``'s DELETE
handler needs DELETE on every table a task owns.

This is the one deliberate exception to the privileges module's documented
policy that graph tables get no DELETE (see packages/kernel/privileges.py).
The policy guards the *evidence pipeline*: refuted, quarantined, and folded
nodes must never be physically deleted by the projection machinery. Destroying
a whole session at the researcher's explicit request is a task-lifecycle
operation at a different layer -- the same request also destroys the ledger
and the task row itself, so leaving the graph behind would orphan it. The
grant is DELETE-only: the app role gains no new INSERT/UPDATE on graph tables,
and the projector role is untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import grant

revision: str = "0019_session_deletion_grants"
down_revision: str | None = "0018_skills_multi_name_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_CHILD_TABLES = (
    "scientific_events",
    "event_audits",
    "process_stream",
    "graph_nodes",
    "graph_edges",
    "projection_checkpoints",
    "objects",
    "sources",
    "source_versions",
    "council_rounds",
    "scientist_runs",
    "round_outputs",
)


def upgrade() -> None:
    grant(APP_ROLE, "DELETE", TASK_CHILD_TABLES)


def downgrade() -> None:
    # REVOKE DELETE only -- revoke_all would strip the SELECT/INSERT the
    # ledger and graph already legitimately hold.
    op.execute(
        sa.text(
            f"REVOKE DELETE ON {', '.join(TASK_CHILD_TABLES)} FROM {APP_ROLE}"
        )
    )
