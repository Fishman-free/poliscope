"""Assert that the dual-graph write boundary is enforced by PostgreSQL.

CLAUDE.md 5.3 makes two claims that code alone cannot guarantee, because the
next contributor can always add another call site:

* the Graph Projector is the only writer of the Evidence Graph;
* refuted, quarantined, and folded nodes are never physically deleted.

Revision 0003 turns both into privilege facts. These tests check the privileges
rather than the Python call graph, so they keep holding even if the projector is
rewritten.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

GRAPH_TABLES = ("graph_nodes", "graph_edges")
INSUFFICIENT_PRIVILEGE = "42501"


async def _has_privilege(
    engine: AsyncEngine,
    role: str,
    table: str,
    privilege: str,
) -> bool:
    async with engine.connect() as connection:
        granted = await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": role, "table": table, "privilege": privilege},
        )
    return bool(granted)


@pytest.mark.parametrize("table", GRAPH_TABLES)
@pytest.mark.parametrize("privilege", ("INSERT", "UPDATE", "TRUNCATE"))
async def test_app_role_cannot_write_the_evidence_graph(
    admin_engine: AsyncEngine,
    table: str,
    privilege: str,
) -> None:
    """The council reaches the graph only through the projector."""
    assert not await _has_privilege(admin_engine, "poliscope_app", table, privilege)


@pytest.mark.parametrize("table", GRAPH_TABLES)
async def test_app_role_delete_on_graph_tables_is_the_session_lifecycle_exception(
    admin_engine: AsyncEngine,
    table: str,
) -> None:
    """Migration 0019 grants the app role DELETE on the graph tables so the
    API can destroy a whole session at the researcher's explicit request --
    the one documented exception to "no role deletes graph rows". DELETE
    without INSERT/UPDATE means the exception cannot grow into write access."""
    assert await _has_privilege(admin_engine, "poliscope_app", table, "DELETE")


@pytest.mark.parametrize("table", GRAPH_TABLES)
async def test_app_role_can_still_read_the_evidence_graph(
    admin_engine: AsyncEngine,
    table: str,
) -> None:
    """The workspace API serves the controversy map, so reads must work."""
    assert await _has_privilege(admin_engine, "poliscope_app", table, "SELECT")


@pytest.mark.parametrize("table", GRAPH_TABLES)
@pytest.mark.parametrize("privilege", ("DELETE", "TRUNCATE"))
async def test_projector_role_cannot_physically_delete_graph_rows(
    admin_engine: AsyncEngine,
    table: str,
    privilege: str,
) -> None:
    """A node that loses an argument changes status; it never disappears.

    The projector -- the evidence pipeline's own writer -- holds no DELETE,
    so even a bug in the projection machinery cannot destroy the dissent
    record that CLAUDE.md 4 requires to stay traceable. (The app role's
    session-lifecycle DELETE from migration 0019 is the deliberate exception
    covered by test_app_role_delete_on_graph_tables_*.)
    """
    assert not await _has_privilege(
        admin_engine, "poliscope_projector", table, privilege
    )


@pytest.mark.parametrize("table", GRAPH_TABLES)
async def test_app_role_cannot_truncate_graph_rows(
    admin_engine: AsyncEngine,
    table: str,
) -> None:
    """Truncate stays forbidden for everyone: bulk-destroying every node of a
    task bypasses the per-row session deletion entirely."""
    assert not await _has_privilege(admin_engine, "poliscope_app", table, "TRUNCATE")


async def test_app_role_cannot_rewrite_the_event_ledger(
    admin_engine: AsyncEngine,
) -> None:
    """Events are append-only so that a replay reproduces the same graph.

    DELETE is the migration-0019 session-lifecycle exception (destroying a
    session removes its ledger rows together with everything else); UPDATE
    and TRUNCATE stay forbidden -- the ledger must never be rewritten or
    bulk-wiped outside an explicit per-session deletion.
    """
    assert await _has_privilege(
        admin_engine, "poliscope_app", "scientific_events", "INSERT"
    )
    assert await _has_privilege(
        admin_engine, "poliscope_app", "scientific_events", "SELECT"
    )
    assert await _has_privilege(
        admin_engine, "poliscope_app", "scientific_events", "DELETE"
    )
    for privilege in ("UPDATE", "TRUNCATE"):
        assert not await _has_privilege(
            admin_engine, "poliscope_app", "scientific_events", privilege
        )


async def test_projector_may_advance_event_status_but_not_rewrite_payload(
    admin_engine: AsyncEngine,
) -> None:
    """Column level UPDATE keeps the projector from editing what it projects."""
    async with admin_engine.connect() as connection:
        for column, expected in (("status", True), ("payload", False)):
            granted = await connection.scalar(
                text(
                    "SELECT has_column_privilege("
                    ":role, 'scientific_events', :column, 'UPDATE')"
                ),
                {"role": "poliscope_projector", "column": column},
            )
            assert bool(granted) is expected


async def test_app_role_insert_into_graph_nodes_is_refused_at_runtime(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The privilege check is not theoretical: the statement actually fails."""
    with pytest.raises(DBAPIError) as error:
        await app_session.execute(
            text(
                "INSERT INTO graph_nodes "
                "(id, task_id, node_type, payload, status) "
                "VALUES (:id, :task_id, 'Claim', '{}'::jsonb, 'active')"
            ),
            {"id": uuid4(), "task_id": seeded_task},
        )
    assert cast(Any, error.value.orig).sqlstate == INSUFFICIENT_PRIVILEGE
    await app_session.rollback()


async def test_app_role_can_append_an_event(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The append path the council actually uses must work end to end."""
    await app_session.execute(
        text(
            "INSERT INTO scientific_events "
            "(id, task_id, event_type, payload, idempotency_key, sequence, status) "
            "VALUES (:id, :task_id, 'CLAIM_PROPOSED', '{}'::jsonb, :key, 1, 'pending')"
        ),
        {"id": uuid4(), "task_id": seeded_task, "key": f"claim-1-{seeded_task}"},
    )
    stored = await app_session.scalar(
        text("SELECT count(*) FROM scientific_events WHERE task_id = :task_id"),
        {"task_id": seeded_task},
    )
    assert stored == 1
    await app_session.rollback()


async def test_duplicate_idempotency_key_is_rejected(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Replaying a task must not append the same event twice."""
    statement = text(
        "INSERT INTO scientific_events "
        "(id, task_id, event_type, payload, idempotency_key, sequence, status) "
        "VALUES (:id, :task_id, 'CLAIM_PROPOSED', '{}'::jsonb, :key, :seq, 'pending')"
    )
    key = f"replayed-{seeded_task}"
    await app_session.execute(
        statement,
        {"id": uuid4(), "task_id": seeded_task, "key": key, "seq": 1},
    )
    with pytest.raises(DBAPIError):
        await app_session.execute(
            statement,
            {"id": uuid4(), "task_id": seeded_task, "key": key, "seq": 2},
        )
    await app_session.rollback()
