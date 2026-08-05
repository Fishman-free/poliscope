"""Assert that the whole migration chain is reversible, not just its first step.

A downgrade path is only ever exercised deliberately, so a broken REVOKE or a
wrong drop order stays hidden until someone needs to roll back a deployment.
Revisions 0002, 0003, and 0005 all revoke grants on downgrade, which is exactly
the kind of statement that fails silently in review and loudly in production.
"""

from __future__ import annotations

import asyncio
import os

from alembic import command
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import _alembic_config

EVIDENCE_TABLES = frozenset(
    {
        "scientific_events",
        "event_audits",
        "graph_nodes",
        "graph_edges",
        "projection_checkpoints",
    }
)
COUNCIL_TABLES = frozenset({"council_rounds", "scientist_runs", "round_outputs"})
KNOWLEDGE_TABLES = frozenset({"knowledge_bases", "knowledge_documents"})
SETTINGS_TABLES = frozenset({"app_settings"})
ACCOUNT_TABLES = frozenset({"users", "auth_tokens"})
SKILL_TABLES = frozenset({"skills"})


def _list_tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


async def _tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(_list_tables)
    finally:
        await engine.dispose()
    return names - {"alembic_version"}


def _tables_sync(database_url: str) -> set[str]:
    return asyncio.run(_tables(database_url))


def test_full_chain_upgrade_downgrade_upgrade_is_reversible(
    isolated_postgres_admin_url: str,
) -> None:
    os.environ["POLISCOPE_APP_DATABASE_PASSWORD"] = "isolated-app-test-password"
    os.environ["POLISCOPE_PROJECTOR_DATABASE_PASSWORD"] = (
        "isolated-projector-test-password"
    )
    config = _alembic_config(isolated_postgres_admin_url)
    try:
        command.upgrade(config, "head")
        at_head = _tables_sync(isolated_postgres_admin_url)
        assert at_head >= EVIDENCE_TABLES
        assert at_head >= COUNCIL_TABLES
        assert at_head >= KNOWLEDGE_TABLES
        assert at_head >= SETTINGS_TABLES
        assert at_head >= ACCOUNT_TABLES
        assert at_head >= SKILL_TABLES

        command.downgrade(config, "base")
        assert _tables_sync(isolated_postgres_admin_url) == set()

        command.upgrade(config, "head")
        assert _tables_sync(isolated_postgres_admin_url) == at_head
    finally:
        command.downgrade(config, "base")


def test_each_revision_can_be_applied_one_step_at_a_time(
    isolated_postgres_admin_url: str,
) -> None:
    """Stepping revision by revision must not depend on running them as a batch.

    An operator upgrading a live database inspects the schema between steps, so
    every intermediate state has to be valid on its own.
    """
    os.environ["POLISCOPE_APP_DATABASE_PASSWORD"] = "isolated-app-test-password"
    os.environ["POLISCOPE_PROJECTOR_DATABASE_PASSWORD"] = (
        "isolated-projector-test-password"
    )
    config = _alembic_config(isolated_postgres_admin_url)
    revisions = (
        "0001_research_and_calls",
        "0002_papers_and_objects",
        "0003_evidence_ledger_and_graph",
        "0005_council_runtime",
        "0010_knowledge_bases",
        "0011_app_settings",
        "0012_accounts",
        "0013_skills",
    )
    try:
        previous: set[str] = set()
        for revision in revisions:
            command.upgrade(config, revision)
            current = _tables_sync(isolated_postgres_admin_url)
            assert previous < current, f"{revision} added no tables"
            previous = current
        for revision in reversed(revisions[:-1]):
            command.downgrade(config, revision)
            current = _tables_sync(isolated_postgres_admin_url)
            assert current < previous, f"downgrade past {revision} dropped no tables"
            previous = current
    finally:
        command.downgrade(config, "base")
