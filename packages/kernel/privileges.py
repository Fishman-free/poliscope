"""Mechanism for granting table privileges from inside a migration.

This module deliberately contains no policy. Which role may touch which table is
written literally in the migration that creates the table, because a migration
must replay identically forever: if the policy lived here, editing it would
silently change what an already-applied revision did.

The privilege model itself is load bearing rather than defensive hardening.
Two constraints from CLAUDE.md 5.3 are enforced here and nowhere else:

* the Graph Projector is the only writer of the evidence graph, so the
  application role receives SELECT on the graph tables and nothing more;
* refuted, quarantined, and folded nodes must never be physically deleted, so
  DELETE and TRUNCATE are granted to no role at all on those tables.

Code can be bypassed by the next contributor. A missing grant cannot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

READ = "SELECT"
APPEND = "SELECT, INSERT"
"""Insert and read, but never modify or remove. Used for the event ledger."""

MUTATE = "SELECT, INSERT, UPDATE"
"""Everything except removal. Used for the evidence graph and checkpoints."""

FULL_DML = "SELECT, INSERT, UPDATE, DELETE"
"""Ordinary business tables, where deleting a draft row is legitimate."""


def _quote(tables: Sequence[str]) -> str:
    if not tables:
        raise ValueError("at least one table is required")
    return ", ".join(tables)


def grant(role: str, privileges: str, tables: Sequence[str]) -> None:
    """Grant ``privileges`` on every table in ``tables`` to ``role``."""
    op.execute(sa.text(f"GRANT {privileges} ON {_quote(tables)} TO {role}"))


def grant_column(
    role: str,
    privilege: str,
    table: str,
    columns: Sequence[str],
) -> None:
    """Grant a privilege limited to specific columns.

    Used so that the projector can advance an event's ``status`` without gaining
    the ability to rewrite the payload it was asked to project.
    """
    if not columns:
        raise ValueError("at least one column is required")
    column_list = ", ".join(columns)
    op.execute(
        sa.text(f"GRANT {privilege} ({column_list}) ON {table} TO {role}")
    )


def revoke_all(role: str, tables: Sequence[str]) -> None:
    """Remove every privilege ``role`` holds on ``tables``.

    Called before granting so that a re-run cannot leave a wider privilege in
    place than the migration intends, and called on downgrade so that dropping a
    table does not leave a dangling grant behind.
    """
    op.execute(sa.text(f"REVOKE ALL ON {_quote(tables)} FROM {role}"))
