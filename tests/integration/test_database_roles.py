import asyncio
import os
from typing import Any, cast
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from tests.conftest import _alembic_config

BUSINESS_TABLES = (
    "research_tasks",
    "research_scopes",
    "atomic_claims",
    "model_calls",
    "tool_calls",
)


async def test_app_role_can_use_dml_on_base_tables(
    app_session: AsyncSession,
) -> None:
    task_id = uuid4()
    await app_session.execute(
        text(
            "INSERT INTO research_tasks "
            "(id, task_id, question, status, created_by, wall_clock_minutes, "
            "model_cost_usd, tool_call_limit, source_limit, user_evidence) "
            "VALUES (:id, :task_id, :question, :status, :created_by, "
            ":wall_clock_minutes, :model_cost_usd, :tool_call_limit, "
            ":source_limit, CAST(:user_evidence AS jsonb))"
        ),
        {
            "id": task_id,
            "task_id": task_id,
            "question": "Does digital behavior affect mental health?",
            "status": "DRAFT",
            "created_by": "integration-test",
            "wall_clock_minutes": 60,
            "model_cost_usd": "10.00",
            "tool_call_limit": 20,
            "source_limit": 10,
            "user_evidence": "{}",
        },
    )
    inserts = {
        "research_scopes": (
            "populations, regions, languages, date_until, evidence_priorities, "
            "allow_preprints",
            "ARRAY[]::text[], ARRAY[]::text[], ARRAY[]::text[], CURRENT_DATE, "
            "ARRAY[]::text[], false",
        ),
        "atomic_claims": (
            "statement, claim_type, scope, falsification_condition",
            "'claim', 'descriptive', '{}'::jsonb, 'counterevidence'",
        ),
        "model_calls": (
            "actor, purpose, model_class, input_hash, input_tokens, output_tokens, "
            "cost_usd, latency_ms, retries, evidence_refs, schema_status",
            "'agent', 'research', 'balanced', 'input', 1, 1, 0, 1, 0, "
            "ARRAY[]::uuid[], 'valid'",
        ),
        "tool_calls": (
            "actor, tool_name, operation, input_hash, cost_usd, latency_ms, retries, "
            "evidence_refs, schema_status",
            "'agent', 'search', 'query', 'input', 0, 1, 0, ARRAY[]::uuid[], 'valid'",
        ),
    }
    for table_name, (columns, values) in inserts.items():
        await app_session.execute(
            text(
                f"INSERT INTO {table_name} "  # noqa: S608
                f"(id, task_id, status, created_by, {columns}) "
                f"VALUES (:id, :task_id, 'DRAFT', 'integration-test', {values})"
            ),
            {"id": uuid4(), "task_id": task_id},
        )
    for table_name in BUSINESS_TABLES:
        await app_session.execute(
            text(f"UPDATE {table_name} SET status = 'QUEUED' WHERE task_id = :id"),  # noqa: S608
            {"id": task_id},
        )
        status = await app_session.scalar(
            text(f"SELECT status FROM {table_name} WHERE task_id = :id"),  # noqa: S608
            {"id": task_id},
        )
        assert status == "QUEUED"
    for table_name in reversed(BUSINESS_TABLES):
        await app_session.execute(
            text(f"DELETE FROM {table_name} WHERE task_id = :id"),  # noqa: S608
            {"id": task_id},
        )
    await app_session.commit()


@pytest.mark.parametrize(
    "statement",
    (
        "CREATE TABLE forbidden_table(id integer)",
        "CREATE TEMP TABLE forbidden_temp_table(id integer)",
        "ALTER TABLE research_tasks ADD COLUMN forbidden integer",
        "DROP TABLE research_tasks",
    ),
)
async def test_app_role_cannot_run_ddl(
    app_session: AsyncSession,
    statement: str,
) -> None:
    assert await app_session.scalar(text("SELECT current_user")) == "poliscope_app"
    assert await app_session.scalar(text("SELECT to_regclass('public.research_tasks')"))
    with pytest.raises(DBAPIError) as error:
        await app_session.execute(text(statement))
    assert cast(Any, error.value.orig).sqlstate == "42501"
    await app_session.rollback()


async def test_runtime_roles_have_no_temporary_database_privilege(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        for role_name in ("poliscope_app", "poliscope_projector"):
            granted = await connection.scalar(
                text(
                    "SELECT has_database_privilege("
                    ":role_name, current_database(), 'TEMPORARY')"
                ),
                {"role_name": role_name},
            )
            assert granted is False


@pytest.mark.parametrize("table_name", BUSINESS_TABLES)
@pytest.mark.parametrize(
    "privilege",
    ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
)
async def test_projector_role_has_no_base_table_privileges(
    admin_engine: AsyncEngine,
    table_name: str,
    privilege: str,
) -> None:
    async with admin_engine.connect() as connection:
        granted = await connection.scalar(
            text(
                "SELECT has_table_privilege("
                ":role_name, :table_name, :privilege)"
            ),
            {
                "role_name": "poliscope_projector",
                "table_name": table_name,
                "privilege": privilege,
            },
        )
    assert granted is False


async def _configure_role_membership(
    postgres_admin_url: str,
    grant_sql: str,
) -> None:
    engine = create_async_engine(postgres_admin_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE ROLE inherited_test_role"))
            await connection.execute(text(grant_sql))
    finally:
        await engine.dispose()


async def _remove_role_membership(
    postgres_admin_url: str,
    revoke_sql: str,
) -> None:
    engine = create_async_engine(postgres_admin_url)
    try:
        async with engine.begin() as connection:
            role_exists = await connection.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'inherited_test_role'")
            )
            if role_exists is not None:
                await connection.execute(text(revoke_sql))
                await connection.execute(text("DROP ROLE inherited_test_role"))
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("grant_sql", "revoke_sql"),
    (
        (
            "GRANT inherited_test_role TO poliscope_app",
            "REVOKE inherited_test_role FROM poliscope_app",
        ),
        (
            "GRANT poliscope_projector TO inherited_test_role",
            "REVOKE poliscope_projector FROM inherited_test_role",
        ),
    ),
)
def test_existing_role_membership_fails_closed(
    isolated_postgres_admin_url: str,
    grant_sql: str,
    revoke_sql: str,
) -> None:
    config = _alembic_config(isolated_postgres_admin_url)
    os.environ["POLISCOPE_APP_DATABASE_PASSWORD"] = "isolated-app-test-password"
    os.environ["POLISCOPE_PROJECTOR_DATABASE_PASSWORD"] = (
        "isolated-projector-test-password"
    )
    try:
        command.upgrade(config, "0001_research_and_calls")
        command.downgrade(config, "base")
        asyncio.run(
            _configure_role_membership(isolated_postgres_admin_url, grant_sql)
        )
        with pytest.raises(RuntimeError, match="must have no role memberships"):
            command.upgrade(config, "0001_research_and_calls")
    finally:
        asyncio.run(_remove_role_membership(isolated_postgres_admin_url, revoke_sql))
        command.downgrade(config, "base")