import asyncio
import os

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import _alembic_config
from tests.helpers import table_column_details, table_names

BASE_TABLES = {
    "research_tasks",
    "research_scopes",
    "atomic_claims",
    "model_calls",
    "tool_calls",
}
EXPECTED_COLUMNS = {
    "research_tasks": {
        "id",
        "task_id",
        "question",
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "wall_clock_minutes",
        "model_cost_usd",
        "tool_call_limit",
        "source_limit",
        "user_evidence",
        # Plan phase 8.1: serialized CouncilCheckpoint, set only while the
        # task sits in AWAITING_COUNCIL_INPUT (migration 0008).
        "council_checkpoint",
        # Per-task model endpoint (migration 0009): the researcher's own
        # base_url/api_key/model_name, or NULL for the deployment gateway.
        "model_config",
        # Linked knowledge base (migration 0010): whose documents the council
        # treats as Level A user-provided sources, or NULL for web-only.
        "knowledge_base_id",
        # Owning account (migration 0012) and enabled skills (0013).
        "user_id",
        "skill_ids",
    },
    "research_scopes": {
        "id",
        "task_id",
        "status",
        "created_by",
        "created_at",
        "populations",
        "regions",
        "languages",
        "date_from",
        "date_until",
        "evidence_priorities",
        "allow_preprints",
    },
    "atomic_claims": {
        "id",
        "task_id",
        "statement",
        "claim_type",
        "scope",
        "falsification_condition",
        "status",
        "created_by",
        "created_at",
    },
    "model_calls": {
        "id",
        "task_id",
        "status",
        "created_by",
        "created_at",
        "actor",
        "purpose",
        "model_class",
        "output_schema",
        "input_hash",
        "output_hash",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "retries",
        "error_code",
        "evidence_refs",
        "schema_status",
        "request_summary",
    },
    "tool_calls": {
        "id",
        "task_id",
        "status",
        "created_by",
        "created_at",
        "actor",
        "tool_name",
        "operation",
        "input_hash",
        "output_hash",
        "cost_usd",
        "latency_ms",
        "retries",
        "error_code",
        "evidence_refs",
        "schema_status",
        "request_summary",
    },
}
NULLABLE_COLUMNS = {
    "research_tasks": {
        "council_checkpoint",
        "model_config",
        "knowledge_base_id",
        # Pre-account rows only; every task created after 0012 owns one.
        "user_id",
    },
    "research_scopes": {"date_from"},
    "atomic_claims": set(),
    "model_calls": {"output_hash", "error_code"},
    "tool_calls": {"output_hash", "error_code"},
}


async def _schema_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        return (await table_names(engine)) - {"alembic_version"}
    finally:
        await engine.dispose()


def _schema_tables_sync(database_url: str) -> set[str]:
    return asyncio.run(_schema_tables(database_url))


def test_migration_upgrade_downgrade_upgrade_is_reversible(
    isolated_postgres_admin_url: str,
) -> None:
    os.environ["POLISCOPE_APP_DATABASE_PASSWORD"] = "isolated-app-test-password"
    os.environ["POLISCOPE_PROJECTOR_DATABASE_PASSWORD"] = (
        "isolated-projector-test-password"
    )
    config = _alembic_config(isolated_postgres_admin_url)
    try:
        command.upgrade(config, "0001_research_and_calls")
        first_upgrade = _schema_tables_sync(isolated_postgres_admin_url)
        assert first_upgrade == BASE_TABLES

        command.downgrade(config, "base")
        assert _schema_tables_sync(isolated_postgres_admin_url).isdisjoint(
            BASE_TABLES
        )

        command.upgrade(config, "0001_research_and_calls")
        assert _schema_tables_sync(isolated_postgres_admin_url) == first_upgrade
    finally:
        command.downgrade(config, "base")


async def test_upgrade_creates_exact_research_and_call_tables(
    admin_engine: AsyncEngine,
) -> None:
    assert await table_names(admin_engine) >= BASE_TABLES


async def test_base_tables_have_exact_planned_columns_and_nullability(
    admin_engine: AsyncEngine,
) -> None:
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        details = await table_column_details(admin_engine, table_name)
        assert set(details) == expected_columns
        assert {
            name for name, column in details.items() if bool(column["nullable"])
        } == NULLABLE_COLUMNS[table_name]


async def test_task_id_unique_indexes_are_not_duplicated(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_name, count(*) FROM ("
                "SELECT relation.relname AS table_name, index_relation.oid "
                "FROM pg_class relation "
                "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_index index_data ON index_data.indrelid = relation.oid "
                "JOIN pg_class index_relation "
                "ON index_relation.oid = index_data.indexrelid "
                "WHERE namespace.nspname = 'public' "
                "AND relation.relname IN ('research_tasks', 'research_scopes') "
                "AND index_data.indisunique "
                "AND index_data.indnkeyatts = 1 "
                "AND (index_data.indkey::smallint[])[0] = ("
                "SELECT attnum FROM pg_attribute "
                "WHERE attrelid = relation.oid AND attname = 'task_id'"
                ")"
                ") unique_task_indexes GROUP BY table_name"
            )
        )
    assert {str(row[0]): int(row[1]) for row in rows} == {
        "research_scopes": 1,
        "research_tasks": 1,
    }

