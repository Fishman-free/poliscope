"""Drop process_stream's FK: the claim lock would deadlock the live view.

Revision ID: 0016_process_stream_no_fk
Revises: 0015_process_stream
Create Date: 2026-08-06

0015 gave ``process_stream.task_id`` a foreign key to ``research_tasks``.
That is a live-view killer: the worker's claim takes ``SELECT ... FOR UPDATE``
on the task row and holds it for the entire deliberation transaction, while a
Postgres FK check takes a KEY SHARE lock on the parent row -- so the writer's
INSERT (and every token batch behind it) blocks until the run commits. The
deliberation transaction in turn waits for the model stream, which waits for
the writer's flush: a deadlock that froze the worker and left every task
QUEUED.

The stream is ephemeral by design (process-only, never evidence), so the
integrity loss is bounded: orphan rows are harmless, and retention can trim
them later. The ``(task_id, seq)`` uniqueness stays, and task_id keeps its
index for the API's per-task replay.
"""

from collections.abc import Sequence

from alembic import op

from packages.kernel.config import APP_ROLE
from packages.kernel.privileges import FULL_DML, grant, revoke_all

revision: str = "0016_process_stream_no_fk"
down_revision: str | None = "0015_process_stream"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "process_stream"


def upgrade() -> None:
    op.drop_constraint(
        "process_stream_task_id_fkey", TABLE, type_="foreignkey"
    )


def downgrade() -> None:
    op.create_foreign_key(
        "process_stream_task_id_fkey",
        TABLE,
        "research_tasks",
        ["task_id"],
        ["task_id"],
        ondelete="CASCADE",
    )
